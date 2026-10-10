"""Standard-library terminal progress; never changes analysis state or stdout JSON."""
from __future__ import annotations

import codecs
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
import re
from pathlib import Path
import sys
import threading
import time

_ACTIVE = ContextVar("nanocofold_console_progress", default=None)
PHASES = {
    "preflight": "실행 환경 점검", "target": "표적 구조 준비",
    "input": "입력 준비", "design": "새 VHH 후보 생성",
    "screen": "후보 예측", "mature": "변이 후보 준비",
    "tier2": "복합체 정밀 예측", "prediction": "복합체 예측",
    "comparison_msa_search": "MSA 검색·서버 응답 대기",
    "engine_comparison": "엔진별 예측·지표 계산", "final": "결과·보고서 준비",
    "queued": "실행 대기", "starting": "작업 시작",
    "resume_validation": "재개 기록 확인", "colabfold_screen": "ColabFold 예측",
    "boltz_rescore": "Boltz-2 재평가",
}


def elapsed_text(seconds):
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return "시간 미확인"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _json(path, since=None):
    try:
        info = path.stat()
        if info.st_size > 8 * 1024 * 1024 or (since is not None and info.st_mtime < since):
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def run_detail(root, since=None):
    """Use observed counts, including failures, without inventing a percent/ETA."""
    root = Path(root)
    state = _json(root / "run_state.json", since)
    phases = state.get("phases", {})
    if not isinstance(phases, dict):
        phases = {}
    phase = state.get("current_phase")
    parts = [PHASES.get(phase, str(phase))] if phase else []
    comparison = _json(root / "02_comparison/comparison_status.json", since)
    rows, total = comparison.get("rows"), comparison.get("expected_rows")
    if (isinstance(rows, list) and type(total) is int and 0 <= len(rows) <= total
            and all(isinstance(row, dict) and row.get("status") in {"complete", "failed"} for row in rows)):
        succeeded = sum(row["status"] == "complete" for row in rows)
        parts.append(f"모델 종료 {len(rows)}/{total} · 성공 {succeeded} · 실패 {len(rows) - succeeded}")
    else:
        prediction = phases.get("prediction", {})
        if not isinstance(prediction, dict):
            prediction = {}
        done, total = prediction.get("completed_models"), prediction.get("total_models")
        if type(done) is int and type(total) is int and 0 <= done <= total:
            parts.append(f"모델 종료 {done}/{total} · 실패 {prediction.get('failed_models', 0)}")
    final = phases.get("final", {})
    if not isinstance(final, dict):
        final = {}
    if final.get("delivery_status") == "pending":
        parts.append("전달용 ZIP 작성 중")
    return " · ".join(parts)


def batch_detail(batch, terminal):
    jobs = [row["job"] for row in batch["rows"] if row.get("job")]
    states = [job["state"] for job in jobs]
    result = f"작업 종료 {sum(state in terminal for state in states)}/{len(jobs)} · 실행 {states.count('running')} · 대기 {states.count('queued')}"
    for job in [job for job in jobs if job["state"] == "running"][:3]:
        progress = job.get("progress", {})
        done, total = progress.get("completed"), progress.get("total")
        count = f" · 완료 {done}/{total}" if type(done) is int and type(total) is int else ""
        result += f"\n  {job['id'][:8]} · {PHASES.get(job.get('phase'), job.get('phase', '작업 중'))}{count}"
    return result


class ConsoleProgress:
    """A stderr heartbeat and optional incremental log relay, even without a TTY."""
    def __init__(self, label, *, run_dir=None, log_path=None, log_offset=0, interval=10, detail=None):
        self.label, self.run_dir = label, run_dir
        self.log_path, self.offset = log_path, log_offset
        self.interval = interval
        self.detail = detail
        self.start = time.monotonic()
        self.since = time.time()
        self.stop = threading.Event()
        self.thread = None
        self.activity = None
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @staticmethod
    def write(message):
        try:
            stream = sys.stderr
            if stream is None:
                return
            # Logs remain byte-exact on disk; terminal control sequences are
            # escaped only on the display surface (including split relays).
            message = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]",
                             lambda m: f"\\x{ord(m.group()):02x}", str(message))
            stream.write(message)
            stream.flush()
        except (OSError, ValueError, AttributeError):
            # A closed display must not turn completed scientific work into failure.
            pass

    def _relay(self):
        if self.log_path is None:
            return
        try:
            with Path(self.log_path).open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read(64 * 1024)
            self.offset += len(data)
            if data:
                self.write(self.decoder.decode(data))
        except OSError:
            pass

    def _detail(self):
        if self.detail:
            return self.detail()
        parts = [run_detail(self.run_dir, self.since)] if self.run_dir else []
        if self.activity:
            label, path = self.activity
            parts.append(label)
            if path:
                try:
                    parts.append(f"로그 {Path(path).stat().st_size // 1024} KiB: {path}")
                except OSError:
                    parts.append(f"로그: {path}")
        return " · ".join(part for part in parts if part)

    def _run(self):
        next_heartbeat = self.start
        while not self.stop.wait(.2):
            self._relay()
            now = time.monotonic()
            if now < next_heartbeat:
                continue
            try:
                detail = self._detail()
            except (AttributeError, TypeError, ValueError):
                detail = "상태 기록 확인 중"
            if os.environ.get("NANOCOFOLD_PROGRESS_PARENT") != "1":
                suffix = " · " + detail if detail else " · 출력 대기/계산 중"
                self.write(f"[진행] {self.label} · 경과 {elapsed_text(now - self.start)}{suffix}\n")
            next_heartbeat = now + self.interval
        # Drain the end of a short command as well as the final bytes of a long one.
        while self.log_path is not None:
            before = self.offset
            self._relay()
            if self.offset == before:
                break

    def __enter__(self):
        self.token = _ACTIVE.set(self)
        if os.environ.get("NANOCOFOLD_PROGRESS_PARENT") != "1":
            self.write(f"[시작] {self.label} · 진행 상태는 {self.interval:g}초마다 표시됩니다.\n")
        self.thread = threading.Thread(target=self._run, name="nanocofold-progress", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.stop.set()
        self.thread.join(timeout=1)
        _ACTIVE.reset(self.token)
        if os.environ.get("NANOCOFOLD_PROGRESS_PARENT") != "1":
            self.write(f"[{'중단' if kind else '종료'}] {self.label} · 경과 {elapsed_text(time.monotonic() - self.start)}\n")


@contextmanager
def activity(label, log_path=None):
    """Annotate a running CLI without opening a second monitor or changing logs."""
    monitor = _ACTIVE.get()
    previous = monitor.activity if monitor else None
    if monitor:
        monitor.activity = (label, log_path)
        monitor.write(f"[작업] {label}" + (f" · 로그: {log_path}" if log_path else "") + "\n")
    try:
        yield
    finally:
        if monitor:
            monitor.activity = previous


def command_label(argv):
    if "--profile" in argv and argv.index("--profile") + 1 < len(argv):
        return "환경·모델 설치: " + argv[argv.index("--profile") + 1]
    if "doctor" in argv:
        return "실행 환경·GPU·모델 점검"
    if "report" in argv:
        return "저장 결과로 보고서 복구"
    if "run" in argv:
        return "후보 생성 → 네 엔진 예측 → 지표·보고서"
    signature = " ".join(str(token).lower() for token in argv[:4])
    for fragment, label in (("rfdiffusion", "RFantibody 골격 생성"), ("proteinmpnn", "ProteinMPNN 서열 생성"),
                            ("rfantibody", "RFantibody"), ("boltzgen", "BoltzGen"),
                            ("colabfold", "ColabFold"), ("boltz2", "Boltz-2"),
                            ("chai", "Chai-1"), ("opendde", "OpenDDE"), ("ipsae", "ipSAE 지표 계산")):
        if fragment in signature:
            return label
    return Path(str(argv[0])).name
