"""Detached worker entrypoint; survives browser and HTTP server shutdowns."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from .jobs import JobStore, process_stamp
from .paths import contained


class LogRelay:
    """Mirror disk-streamed engine logs to the browser's single append-only log."""
    def __init__(self, root: Path | None, *, resume: bool = False):
        self.root = root
        self.offsets = {}
        self.stop = threading.Event()
        self.thread = None
        if root and resume and root.exists():
            for path in root.rglob("*.log"):
                info = path.stat()
                self.offsets[str(path)] = ((info.st_dev, info.st_ino), info.st_size)

    def start(self):
        if self.root:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _read(self):
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*.log")):
            try:
                contained(self.root, path)
                info = path.stat()
                identity = (info.st_dev, info.st_ino)
                prior_identity, offset = self.offsets.get(str(path), (identity, 0))
                if prior_identity != identity or info.st_size < offset:
                    offset = 0
                with path.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read(64 * 1024)
                self.offsets[str(path)] = (identity, offset + len(data))
                if data:
                    text = data.decode("utf-8", errors="replace")
                    sys.stdout.write(f"\n[tool log: {path.relative_to(self.root)}]\n{text}")
                    sys.stdout.flush()
            except (OSError, ValueError):
                pass

    def _run(self):
        while not self.stop.wait(.2):
            self._read()
        self._read()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def _cancel_signal(_signum, _frame):
    raise KeyboardInterrupt("작업 취소 요청")


def execute(workspace: Path, identifier: str) -> int:
    store = JobStore(workspace)
    job = store.get(identifier)
    # The parent records the birth stamp before work begins.
    for _ in range(100):
        if job["state"] != "queued" and job.get("pid"):
            break
        time.sleep(.05)
        job = store.get(identifier)
    if job["state"] not in {"running", "cancelling"}:
        return 1
    if (job.get("pid") != os.getpid() or not job.get("process_stamp")
            or process_stamp(os.getpid()) != job["process_stamp"]):
        print("[platform] 작업 등록 PID/시작 식별자가 일치하지 않습니다. 작업을 실행하지 않았습니다.",
              file=sys.stderr, flush=True)
        return 1
    signal.signal(signal.SIGTERM, _cancel_signal)
    signal.signal(signal.SIGINT, _cancel_signal)
    started = job.get("started") or time.time()
    print(f"[platform] {job['kind']} {identifier} · {job['mode']} · mock={job['mock']}", flush=True)
    error, state, code = None, "failed", 1
    extra = {}
    cfg = None
    relay = LogRelay(Path(job["run_dir"]) if job.get("run_dir") else workspace / ".platform" if job["kind"] == "setup" else None,
                     resume=bool(job.get("resume") or job["kind"] == "setup"))
    relay.start()
    try:
        inherited_lock = int(os.environ.get("NANOCOFOLD_GPU_LOCK_FD", "-1"))
        if job.get("gpu_lock"):
            try:
                identity = os.fstat(inherited_lock)
            except OSError as exc:
                raise RuntimeError("호스트 GPU 작업 잠금이 전달되지 않았습니다. 대기열에서 다시 실행하세요.") from exc
            if (identity.st_dev, identity.st_ino) != (job["gpu_lock"]["device"], job["gpu_lock"]["inode"]):
                raise RuntimeError("호스트 GPU 잠금 파일이 일치하지 않습니다. 대기열에서 다시 실행하세요.")
            # Retain this descriptor until process exit. Do not reopen or unlock
            # the file: the queue already transferred its advisory lock to us.
        from ..config import load_config
        from ..utils import shell
        cfg = load_config(job["config_path"])
        scope = getattr(shell, "cancellation_scope", lambda _check: contextlib.nullcontext())
        with scope(lambda: store.get(identifier)["state"] in {"cancelling", "cancelled", "budget_exhausted"}):
            if job["kind"] == "diagnostics":
                from ..diagnostics import doctor
                diagnosis = doctor(cfg, smoke=False)
                destination = Path(job["config_path"]).parent / "diagnostics.json"
                destination.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2))
                extra["diagnostics"] = diagnosis
                code = 0
            elif job["kind"] == "setup":
                from ..cli import main
                code = main(["setup", "--workspace", str(workspace), "--profile", job["profile"], "--yes"])
            else:
                # cli.main owns the shared preflight and run lock for both entrypoints.
                from ..cli import main
                args = ["run", "-c", job["config_path"], "--mode", job["mode"]]
                if job.get("resume"):
                    args += ["--run-dir", job["run_dir"]]
                else:
                    args += ["--run-id", Path(job["run_dir"]).name]
                code = main(args)
                import shutil
                run_root = Path(job["run_dir"])
                if run_root.exists():
                    preflight = run_root / "preflight.json"
                    if preflight.is_file():
                        extra["diagnostics"] = json.loads(preflight.read_text())
                    for name in ("manifest.json", "residue_mapping.csv", "input_review.json"):
                        source = Path(job["config_path"]).parent / name
                        if source.is_file():
                            shutil.copy2(source, run_root / ("platform_manifest.json" if name == "manifest.json" else name))
                    from .structure_review import verified_reference
                    from .results import ResultReader
                    try:
                        reference, info = verified_reference(ResultReader(run_root))
                        destination = run_root / ("input_reference" + reference.suffix)
                        if reference.resolve() != destination.resolve():
                            shutil.copy2(reference, destination)
                        (run_root / "input_reference_manifest.json").write_text(json.dumps(
                            {"path": destination.name, "sha256": info["sha256"], "source": "verified_job_snapshot"}))
                    except FileNotFoundError:
                        pass  # FASTA or older direct CLI inputs have no reference.
                extra["result_id"] = Path(job["run_dir"]).name
        if code == 0:
            state = "complete"
            total = 4 if job["kind"] == "analysis" and job["mode"] == "design" else 3 if job["kind"] == "analysis" else 1
            if job["kind"] == "analysis" and job["mode"] == "complex":
                total = len(cfg["complex"]["seeds"]) * cfg["complex"]["models_per_seed"]
            extra["progress"] = {"completed": total, "total": total}
            if job["kind"] == "analysis":
                from .results import ResultReader
                reader = ResultReader(Path(job["run_dir"]))
                summary = reader.summary()
                if summary["failed_candidates"] or summary["issues"]:
                    state = "partial_failure"
                extra["summary"] = summary
        else:
            error = f"분석 명령이 종료 코드 {code}로 실패했습니다. 실행 로그를 확인하세요."
    except KeyboardInterrupt:
        current = store.get(identifier)
        state = "budget_exhausted" if current.get("budget_exceeded") else "cancelled"
        error = "실행 시간 한도에 도달했습니다." if state == "budget_exhausted" else "사용자가 작업을 취소했습니다."
        code = 130
    except BaseException as exc:
        if store.get(identifier)["state"] == "cancelling":
            current = store.get(identifier)
            state = "budget_exhausted" if current.get("budget_exceeded") else "cancelled"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        relay.close()
    current = store.get(identifier)
    if current["state"] == "cancelling":
        state = "budget_exhausted" if current.get("budget_exceeded") else "cancelled"
    previous_elapsed = job.get("history", [])[-1].get("elapsed_seconds", 0) if job.get("history") else 0
    elapsed = previous_elapsed + time.time() - started
    terminal = store.update(identifier, state=state, phase=state, error=error, exit_code=code, finished=time.time(),
                            elapsed_seconds=elapsed, can_resume=job["kind"] == "analysis" and state != "complete", **extra)
    from .jobs import write_platform_status
    write_platform_status(terminal)
    if job["kind"] == "analysis" and Path(job["run_dir"]).is_dir():
        try:
            from .result_catalog import ResultCatalog, build_identity
            catalog = ResultCatalog(workspace)
            identity_ref = job.get("execution_identity")
            identity = catalog.get_identity(identity_ref["digest"]) if identity_ref else None
            if identity and isinstance(cfg, dict):
                current_identity = build_identity(workspace, cfg, job["gpu"])
                if current_identity["digest"] != identity["digest"]:
                    identity = {**identity, "reusable": False,
                                "reasons": identity.get("reasons", []) + ["Runtime or inputs changed during execution"]}
            catalog.register(identifier, job["run_dir"], identity, state=state)
        except Exception as exc:
            # Index maintenance cannot reclassify completed scientific outputs.
            with contextlib.suppress(Exception):
                store.update(identifier, catalog_error=f"{type(exc).__name__}: {exc}")
            print(f"[catalog] index unavailable: {exc}", flush=True)
    print(f"[platform] state={state} elapsed={elapsed:.1f}s", flush=True)
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    return execute(args.workspace.resolve(), args.job)


if __name__ == "__main__":
    raise SystemExit(main())
