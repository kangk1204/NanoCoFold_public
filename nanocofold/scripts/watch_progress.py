"""Read-only progress for an already running checkout quickstart or CLI job."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nanocofold.console_progress import ConsoleProgress, _json, elapsed_text, run_detail


def snapshot(root, workspace=None):
    state = _json(root / "run_state.json")
    receipt = _json(root / "quickstart_receipt.json")
    execution = state.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    parts = [run_detail(root)]
    if receipt.get("status") == "preparing":
        parts.append("설치·모델·환경 점검 준비 중 (저장 기록)")
    elif execution.get("state"):
        parts.append("실행 기록: " + str(execution["state"]))
    if isinstance(execution.get("started"), (int, float)):
        end = execution.get("finished") or time.time()
        parts.append("실행 경과 " + elapsed_text(end - execution["started"]))
    try:
        logs = list(root.rglob("*.log"))
        if workspace and (workspace / ".platform").is_dir():
            logs.extend((workspace / ".platform").glob("install-*.log"))
        latest = max((path.stat().st_mtime, path) for path in logs if path.is_file())[1]
        age = max(0, int(time.time() - latest.stat().st_mtime))
        parts.append(f"최근 로그: {latest} · 마지막 기록 {age}초 전")
    except (OSError, ValueError):
        parts.append("로그 기록 대기 중")
    return " · ".join(part for part in parts if part)


def main(argv=None):
    parser = argparse.ArgumentParser(description="이미 실행 중인 분석 기록 확인 (설치·제출·재실행 없음)")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, help="설치 중이라면 설치 workspace도 지정하세요.")
    parser.add_argument("--once", action="store_true", help="현재 기록만 출력하고 종료")
    args = parser.parse_args(argv)
    root = args.run_dir.expanduser().resolve()
    workspace = args.workspace.expanduser().resolve() if args.workspace else None
    if not root.is_dir():
        parser.error("결과 폴더가 없습니다. 기존 명령의 --out 또는 --run-dir 경로를 지정하세요.")
    if args.once:
        print(snapshot(root, workspace), flush=True)
        return 0
    print("저장된 상태와 로그 시간을 확인합니다. 출력이 없어도 모델 다운로드·MSA 대기·계산은 오래 걸릴 수 있습니다. 종료는 Ctrl+C이며 원래 분석은 계속됩니다.", flush=True)
    try:
        with ConsoleProgress("기존 실행 확인", detail=lambda: snapshot(root, workspace)):
            while True:
                time.sleep(.5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
