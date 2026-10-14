from __future__ import annotations

import argparse
import copy
import contextlib
import contextvars
import json
import math
import subprocess
import sys
from pathlib import Path
from . import __version__

from .console_progress import ConsoleProgress, batch_detail, elapsed_text

import pandas as pd
import numpy as np

from . import design_rfantibody, developability, handoff, mature, provenance, rank_select, report, screen, target_prep
from . import score_complex as scorer
from .config import (
    PROJECT_ROOT, cfg_get, dump_resolved, load_config, resolve_path, resolve_run_artifact,
    resolve_run_dir, read_config_yaml,
)
from .utils.atomic import write_csv, write_json
from .state import RunState
from .utils import msa as msa_utils, pdb_io, seq as seq_utils, shell
from .utils.misc import format_seconds

_PLATFORM_COMPUTATION_ORIGIN = contextvars.ContextVar("nanocofold_platform_computation_origin", default=None)


@contextlib.contextmanager
def new_platform_computation(job: dict):
    """Bind a registered fresh worker without exposing a user-settable CLI/version option."""
    origin = None
    if (job.get("kind") == "analysis" and job.get("resume") is False and job.get("history") == []
            and not job.get("reused_result_id") and not job.get("reused")
            and isinstance(job.get("id"), str) and job["id"] and job.get("run_dir")):
        root = Path(job["run_dir"]).resolve()
        if root.name == job["id"]:
            origin = {"kind": "fresh_platform_job", "job_id": job["id"], "run_dir": root}
    token = _PLATFORM_COMPUTATION_ORIGIN.set(origin)
    try:
        yield
    finally:
        _PLATFORM_COMPUTATION_ORIGIN.reset(token)


def _fresh_computation_origin(run_dir: Path, state: RunState, args, new: bool, had_config: bool) -> dict | None:
    """An input-only directory is fresh only with an explicit new-worker identity."""
    if (not new or getattr(args, "command", None) not in {"run", "complex", "design", "screen", "mature", "prepare"}
            or getattr(args, "_existing_run_requested", bool(getattr(args, "run_dir", None)))
            or state.path.exists()):
        return None
    for path in run_dir.iterdir():
        if path.is_symlink() or path.name not in {"run_config.yaml", ".run.lock"} or not path.is_file():
            return None
    origin = _PLATFORM_COMPUTATION_ORIGIN.get()
    if origin and origin["run_dir"] == run_dir.resolve():
        return {"kind": origin["kind"], "job_id": origin["job_id"]}
    if not had_config:
        return {"kind": "new_cli_computation"}
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanocofold",
        description="타깃+핫스팟 조건 나노바디 설계/스크리닝/성숙 파이프라인 (상업용 라이선스 전용)",
    )
    parser.add_argument("--version", action="version", version=f"NanoCoFold {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="연구자용 작업 공간과 CPU 데모 준비")
    p_init.add_argument("--workspace", type=Path, default=Path.cwd())
    p_setup = sub.add_parser("setup", help="분석 모드별 환경과 검증된 모델 설치")
    p_setup.add_argument("--workspace", type=Path, default=Path.cwd())
    p_setup.add_argument("--profile", choices=["core", "complex", "design", "screen", "mature", "all", "colabfold", "boltzgen", "opendde"], default="core")
    p_setup.add_argument("--yes", action="store_true", help="명시한 설치 프로필 실행")
    p_serve = sub.add_parser("serve", help="설치·분석·결과 탐색 브라우저 앱 시작")
    p_serve.add_argument("--workspace", type=Path, default=Path.cwd())
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    p_serve.add_argument("--no-browser", action="store_true")
    p_catalog = sub.add_parser("catalog", help="계산 없이 저장 결과 DB 검색·기존 결과 색인")
    p_catalog.add_argument("--workspace", type=Path, default=Path.cwd())
    p_catalog.add_argument("--register", type=Path, help="작업 공간 안 결과 하나를 색인")
    p_catalog.add_argument("--index-existing", action="store_true", help="runs 직속 기존 결과를 한 번 색인 (추론 없음)")
    p_catalog.add_argument("--limit", type=int, default=25)
    p_catalog.add_argument("--cursor")
    p_catalog.add_argument("--search", default="")
    p_catalog.add_argument("--mode", default="")
    p_catalog.add_argument("--retention", choices=["", "full", "minimal"], default="")
    p_retention = sub.add_parser("retention", help="결과 용량 정리 미리보기·확인한 계획 적용")
    p_retention.add_argument("--workspace", type=Path, default=Path.cwd())
    p_retention.add_argument("--run-dir", required=True, type=Path)
    p_retention.add_argument("--apply", action="store_true", help="계획에 명시한 중간 파일 삭제")
    p_retention.add_argument("--plan-digest", help="앞서 미리보기에서 확인한 plan_digest")
    p_retention.add_argument("--yes", action="store_true", help="명시한 파일 삭제에 동의")

    p_batch = sub.add_parser("batch", help="네 분석 모드의 배치 입력 검토·제출·상태 조회")
    p_batch.add_argument("action", choices=["review", "submit", "status", "cancel"])
    p_batch.add_argument("--workspace", type=Path, default=Path.cwd())
    p_batch.add_argument("--manifest", type=Path, help="review: JSON 배치 입력")
    p_batch.add_argument("--id", help="검토에서 받은 배치 ID")
    p_batch.add_argument("--review-sha256", help="submit: 검토한 배치의 해시")
    p_batch.add_argument("--selected", nargs="+", help="실행할 항목 ID (기본: 전체)")
    p_batch.add_argument("--budget-seconds", type=int, default=36000, help="각 항목의 실행 시간 한도")
    p_batch.add_argument("--wait", action="store_true", help="submit: 큐를 실행하며 모든 선택 항목 종료까지 대기")

    p_wizard = sub.add_parser("wizard", help="초보자용 PDB·체인·hotspot 입력 마법사 (로컬 웹, GPU 사용 없음)")
    p_wizard.add_argument("--port", type=int, default=8765, help="로컬 포트 (0: 빈 포트 자동 선택)")
    p_wizard.add_argument("--output", type=Path, default=Path("prepared_targets"), help="검토한 입력 저장 위치")
    p_wizard.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 생략")

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", "-c", help="YAML 설정 파일")
        sp.add_argument("--set", action="append", default=[], help="설정 오버라이드 (a.b=c)")
        sp.add_argument("--run-id", help="실행 ID (미지정 시 타임스탬프)")
        sp.add_argument("--run-dir", help="기존 실행 디렉터리 (재개/후속 단계용)")
        sp.add_argument("--mock", action="store_true", help="GPU/모델 없이 합성 데이터로 검증")
        sp.add_argument("--dry-run", action="store_true", help="명령만 생성하고 실행하지 않음")
        sp.add_argument("--reuse-workspace", type=Path, help="정확한 실행 정체성을 비교할 결과 DB 작업 공간")
        sp.add_argument("--force-rerun", action="store_true", help="과거 결과 재사용을 생략하고 새 계산 수행")

    p_run = sub.add_parser("run", help="준비→후보생성→스코어링→선별→리포트 전체 실행")
    add_common(p_run)
    p_run.add_argument("--mode", choices=["complex", "design", "screen", "mature"], help="실행 모드 (config 우선)")
    p_run.add_argument("--vhh-fasta", help="screen 모드: 후보 나노바디 FASTA")
    p_run.add_argument("--wt-fasta", help="mature 모드: 야생형 VHH FASTA")

    p_complex = sub.add_parser("complex", help="일반 단백질 복합체 예측·체인 쌍 평가")
    add_common(p_complex)

    p_prep = sub.add_parser("prepare", help="타깃 구조 확보/핫스팟/truncation")
    add_common(p_prep)

    p_design = sub.add_parser("design", help="RFantibody de novo 설계")
    add_common(p_design)

    p_screen = sub.add_parser("screen", help="보유 VHH 라이브러리 복합체 예측 스크리닝")
    add_common(p_screen)
    p_screen.add_argument("--vhh-fasta", required=False, help="후보 나노바디 FASTA")

    p_mature = sub.add_parser("mature", help="기존 바인더 DMS 친화도 성숙")
    add_common(p_mature)
    p_mature.add_argument("--wt-fasta", required=False, help="야생형 VHH FASTA")

    p_score = sub.add_parser("score", help="복합체 스코어링 + 개발가능성 + 랭킹 + 리포트")
    add_common(p_score)

    p_report = sub.add_parser("report", help="기존 실행의 HTML 리포트만 재생성")
    add_common(p_report)

    p_specificity = sub.add_parser("specificity", help="절단형·전장형 짝지은 특이성 캠페인")
    add_common(p_specificity)
    p_specificity.add_argument("--vhh-fasta", help="보유 VHH FASTA (run.mode=screen)")
    p_specificity.add_argument("--wt-fasta", help="WT VHH FASTA (run.mode=mature)")
    p_doctor = sub.add_parser("doctor", help="환경/GPU/도구/라이선스 점검")
    p_doctor.add_argument("--config", "-c", help="실제 실행 설정에 필요한 환경 점검")
    p_doctor.add_argument("--set", action="append", default=[])
    p_doctor.add_argument("--json", action="store_true", help="기계 판독 가능한 진단")
    p_doctor.add_argument("--smoke", action="store_true", help="로컬 가중치로 작은 실제 추론 검증")
    p_handoff = sub.add_parser("validate-handoff", help="발주 전 서열·construct·비교·검토 상태 검사")
    p_handoff.add_argument("--run", required=True, type=Path)
    p_license = sub.add_parser("license", help="라이선스 매니페스트 출력 / 설치 패키지 감사")
    p_license.add_argument("--audit", action="store_true", help="각 env의 설치 패키지 라이선스 자동 감사")
    return parser


def main(argv: list[str] | None = None) -> int:
    from .utils.shell import cancellation_signals
    try:
        with cancellation_signals():
            return _main(argv)
    except KeyboardInterrupt:
        print("[취소] 사용자 요청으로 실행을 중단했습니다.", file=sys.stderr)
        return 130


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            from .install import init_workspace
            print(json.dumps(init_workspace(args.workspace), ensure_ascii=False, indent=2))
            return 0
        if args.command == "setup":
            from .install import setup_profile
            with ConsoleProgress("환경·모델 설치: " + args.profile):
                print(json.dumps(setup_profile(args.workspace, args.profile), ensure_ascii=False, indent=2))
            return 0
        if args.command == "serve":
            from .platform.app import serve
            serve(args.workspace, host=args.host, port=args.port, no_browser=args.no_browser)
            return 0
        if args.command == "batch":
            from .platform.batches import BatchManager
            from .platform.inputs import InputManager
            from .platform.jobs import JobManager, TERMINAL
            workspace = args.workspace.resolve()
            inputs, jobs = InputManager(workspace), JobManager(workspace)
            try:
                batches = BatchManager(inputs, jobs)
                if args.action == "review":
                    if not args.manifest:
                        raise ValueError("review에는 --manifest JSON 파일이 필요합니다.")
                    if args.manifest.stat().st_size > 20*1024*1024:
                        raise ValueError("배치 입력은 20MB 이하입니다.")
                    with ConsoleProgress("배치 입력 검토"):
                        batch = batches.review(json.loads(args.manifest.read_text()))
                elif not args.id:
                    raise ValueError("--id로 검토한 배치를 지정하세요.")
                elif args.action == "submit":
                    payload = {"review_sha256": args.review_sha256, "budget_seconds": args.budget_seconds}
                    if args.selected is not None:
                        payload["selected"] = args.selected
                    with ConsoleProgress("배치 제출"):
                        batch = batches.submit(args.id, payload)
                    if args.wait:
                        import time
                        jobs.start()
                        wait_started = time.monotonic()
                        next_progress = 0
                        while any(row.get("job") and row["job"]["state"] not in TERMINAL for row in batch["rows"]):
                            if time.monotonic() >= next_progress:
                                print(f"[배치 진행] 경과 {elapsed_text(time.monotonic() - wait_started)} · {batch_detail(batch, TERMINAL)}", file=sys.stderr, flush=True)
                                next_progress = time.monotonic() + 10
                            time.sleep(.5)
                            batch = batches.get(args.id)
                elif args.action == "cancel":
                    batch = batches.cancel(args.id)
                else:
                    batch = batches.get(args.id)
                print(json.dumps(batch, ensure_ascii=False, indent=2))
                return 1 if any(row.get("error") or row.get("submission_error") or row.get("job", {}).get("state") in TERMINAL - {"complete"} for row in batch["rows"]) else 0
            finally:
                jobs.close()
                inputs.close()
        if args.command == "catalog":
            from .platform.result_catalog import ResultCatalog
            from .platform.retention import retained_record
            workspace = args.workspace.resolve()
            catalog = ResultCatalog(workspace)
            paths = [args.register] if args.register else []
            if args.index_existing and (workspace / "runs").is_dir():
                paths.extend(path for path in (workspace / "runs").iterdir() if path.is_dir() and not path.is_symlink())
            for path in paths:
                root = path if path.is_absolute() else workspace / path
                # Historical records have no retrospective runtime claim.
                previous = None
                try:
                    previous = catalog.get(root.name)
                except KeyError:
                    pass
                catalog.register(root.name, root, previous.get("identity") if previous else None,
                                 retention="minimal" if retained_record(root) else "full")
            print(json.dumps(catalog.list(limit=args.limit, cursor=args.cursor, search=args.search,
                                          mode=args.mode, retention=args.retention), ensure_ascii=False, indent=2))
            return 0
        if args.command == "retention":
            from .platform.retention import RetentionManager
            from .platform.result_catalog import ResultCatalog
            workspace = args.workspace.resolve()
            root = args.run_dir if args.run_dir.is_absolute() else workspace / args.run_dir
            manager = RetentionManager(workspace)
            if not args.apply:
                result = manager.plan(root)
            else:
                catalog = ResultCatalog(workspace)
                identity = None
                try:
                    identity = catalog.get(root.name).get("identity")
                except KeyError:
                    pass
                result = manager.apply(root, args.plan_digest, args.yes)
                catalog.register(root.name, root, identity, retention="minimal")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("state") == "partial_failure" else 0
        if args.command == "wizard":
            from .wizard import serve
            serve(args.output, port=args.port, open_browser=not args.no_browser)
            return 0
        if args.command == "doctor":
            with ConsoleProgress("실행 환경·GPU·모델 점검"):
                return cmd_doctor(args)
        if args.command == "validate-handoff":
            result = handoff.validate_handoff(args.run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "specificity":
            return cmd_specificity(args)
        if args.command == "license":
            return cmd_license(args)
        if args.command in {"run", "prepare", "complex", "design", "screen", "mature", "score", "report"}:
            return _execute_command(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[오류] {exc}", file=sys.stderr)
        if "DEBUG" in str(__import__("os").environ.get("NANOCOFOLD_DEBUG", "")):
            raise
        return 1
    return 0


def _execute_command(args) -> int:
    """Shared readiness, run ownership and durable lifecycle for CLI/HTTP workers."""
    import time
    from .state import RunLock
    cfg = _load(args)
    directory = getattr(args, "run_dir", None)
    args._existing_run_requested = bool(directory)
    if directory:
        run_dir = resolve_path(cfg, directory)
    else:
        run_dir = resolve_run_dir(cfg, run_id=getattr(args, "run_id", None))
    # Bind ownership to one directory before locking; a mutable "latest"
    # alias must not redirect the subsequent state writes into another run.
    run_dir = run_dir.resolve()
    if args.command in {"score", "report"}:
        # The standalone specificity API writes its own campaign status rather
        # than a core run_state. It is still an existing, inspectable run.
        saved_config = run_dir / "run_config.yaml"
        campaign_status = run_dir / "06_specificity/status.json"
        campaign_run = (saved_config.is_file() and campaign_status.is_file()
                        and bool(cfg_get(load_config(saved_config), "specificity.enabled", False)))
        if not saved_config.is_file() or not ((run_dir / "run_state.json").is_file() or campaign_run):
            raise FileNotFoundError(f"기존 실행의 설정·상태 파일이 없습니다: {run_dir}")
    if (run_dir / "retained").exists():
        raise ValueError("정리 보관본은 읽기 전용입니다. 원본 계산이 필요하면 다른 run-id로 새로 실행하세요.")
    catalog, identity = None, None
    if (getattr(args, "reuse_workspace", None) and args.command in {"run", "complex", "screen", "mature"}
            and not _is_dry_run(cfg) and not (run_dir / "run_config.yaml").exists()):
        from .platform.result_catalog import ResultCatalog, build_identity
        catalog = ResultCatalog(args.reuse_workspace)
        effective = dict(cfg)
        if getattr(args, "vhh_fasta", None):
            effective = {**effective, "screen": {**effective["screen"], "vhh_fasta": args.vhh_fasta}}
        if getattr(args, "wt_fasta", None):
            effective = {**effective, "mature": {**effective["mature"], "wt_fasta": args.wt_fasta, "wt_sequence": ""}}
        import os
        # CLI adapters resolve relative inputs/environments against PROJECT_ROOT;
        # the catalog location controls storage, never scientific file resolution.
        identity = build_identity(PROJECT_ROOT, effective, os.environ.get("NANOCOFOLD_GPU", "0"))
        if not getattr(args, "force_rerun", False) and identity["reusable"]:
            with catalog.reuse_lease(identity["digest"]) as source:
                if source:
                    print(json.dumps({"reused": True, "result_id": source["id"], "run_dir": source["run_root"],
                                      "reuse_kind": source["reuse_kind"], "new_compute_seconds": 0}, ensure_ascii=False, indent=2))
                    return 0
    with RunLock(run_dir), ConsoleProgress("NanoCoFold " + args.command, run_dir=run_dir):
        args.run_dir = str(run_dir)
        run_dir, state = _open_run(cfg, args)
        if args.command in {"complex", "design", "screen", "mature"}:
            cfg["run"]["mode"] = args.command
        if getattr(args, "vhh_fasta", None):
            cfg["screen"]["vhh_fasta"] = args.vhh_fasta
        if getattr(args, "wt_fasta", None):
            cfg["mature"].update(wt_fasta=args.wt_fasta, wt_sequence="")
        dry = _is_dry_run(cfg)
        started = time.time()
        previous_execution = dict(state.data.get("execution", {}))
        if previous_execution and not dry:
            state.data["previous_execution"] = previous_execution
            if args.command == "report":
                # Keep the computation's state across repeated failed report
                # attempts. A report failure must not erase a cancellation.
                state.data.setdefault("report_source_execution", previous_execution)
            else:
                state.data.pop("report_source_execution", None)
        try:
            if not dry:
                state.data["execution"] = {"state": "running", "started": started, "command": args.command,
                                           "completed_phases": []}
                state.data["current_phase"] = "preflight"
                state.save()
            if args.command in {"run", "complex", "design", "screen", "mature"} and not (_is_mock(cfg) or dry):
                from .diagnostics import doctor
                readiness = doctor(cfg)
                (run_dir / "preflight.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2))
                if not readiness["ok"]:
                    failed = [item["name"] for item in readiness["checks"] if item.get("required") and not item.get("ok")]
                    raise RuntimeError("실행 전 점검 실패: " + ", ".join(failed) + ". preflight.json과 설치 안내를 확인하세요.")
            functions = {"complex": cmd_complex, "run": cmd_run, "prepare": cmd_prepare, "design": cmd_design, "screen": cmd_screen,
                         "mature": cmd_mature, "score": cmd_score, "report": cmd_report}
            code = functions[args.command](args)
            if not dry:
                state = RunState(run_dir)
                will_deliver = ("final" in state.data.get("phases", {}) and
                    args.command in {"run", "complex", "screen", "mature", "score", "report"})
                if will_deliver:
                    final = dict(state.get_phase("final"))
                    final.update(delivery_status="pending")
                    final.pop("delivery_error", None)
                    # Publish execution completion and delivery-pending together;
                    # readers must never observe an intervening flushable state.
                    state.data["phases"]["final"] = final
                state.data["execution"] = {"state": "complete" if code == 0 else "failed", "started": started,
                    "finished": time.time(), "command": args.command, "exit_code": code}
                state.save()
                if will_deliver:
                    from .report_delivery import save_result_report
                    try:
                        delivery = save_result_report(run_dir)
                    except Exception as exc:
                        final.update(delivery_status="failed", delivery_error=f"{type(exc).__name__}: {exc}")
                        state.set_phase("final", final)
                        raise
                    final.update(delivery_status="complete", delivery=str(delivery))
                    state.set_phase("final", final)
                    print(f"[전달용 보고서] {delivery} · {delivery.stat().st_size / 1024**2:.2f} MiB", flush=True)
                if catalog and identity:
                    try:
                        from .platform.result_catalog import build_identity
                        current = build_identity(PROJECT_ROOT, cfg, os.environ.get("NANOCOFOLD_GPU", "0"))
                        if current["digest"] != identity["digest"]:
                            identity = {**identity, "reusable": False, "reasons": identity.get("reasons", []) + ["Runtime or inputs changed during execution"]}
                        catalog.register(run_dir.name, run_dir, identity, state="complete" if code == 0 else "failed")
                    except Exception as exc:
                        print(f"[catalog] 색인 실패 · 계산 상태 유지: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return code
        except BaseException as exc:
            if not dry:
                state = RunState(run_dir)
                from .utils.shell import ExecutionCancelled, ExecutionBudgetExceeded
                status = ("cancelled" if isinstance(exc, (KeyboardInterrupt, ExecutionCancelled)) else
                          "budget_exhausted" if isinstance(exc, ExecutionBudgetExceeded) else "failed")
                attempt = {"state": status, "started": started, "finished": time.time(),
                    "command": args.command, "error": f"{type(exc).__name__}: {exc}"}
                source = state.data.get("report_source_execution", {})
                if args.command == "report" and source.get("state") in {"cancelled", "interrupted", "budget_exhausted", "running"}:
                    state.data["execution"] = dict(source)
                    state.data["report_attempt"] = {**attempt, "state": "rejected"}
                else:
                    state.data["execution"] = attempt
                state.save()
            raise


def _is_mock(cfg: dict) -> bool:
    return bool(cfg_get(cfg, "run.mock", False))


def _is_dry_run(cfg: dict) -> bool:
    return bool(cfg_get(cfg, "run.dry_run", False))


def _antibody_env_available(cfg: dict) -> bool:
    env = resolve_path(cfg, cfg_get(cfg, "mature.anarci.env"))
    return bool(env and (env / "bin" / "python").exists())


def _load(args) -> dict:
    overrides = list(args.set)
    mode = getattr(args, "mode", None)
    if not mode and getattr(args, "command", None) in {"complex", "design", "screen", "mature"}:
        mode = args.command
    if mode:
        # Validate the selected CLI mode together with mode-specific options.
        overrides.append(f"run.mode={mode}")
    cfg = load_config(args.config, overrides)
    if getattr(args, "mode", None):
        cfg["run"]["mode"] = args.mode
    if getattr(args, "mock", False):
        cfg["run"]["mock"] = True
    if getattr(args, "dry_run", False):
        cfg["run"]["dry_run"] = True
    return cfg


def _open_run(cfg: dict, args, new: bool = True) -> tuple[Path, RunState]:
    """run-dir 재개 시 저장된 run_config.yaml 을 기준으로 설정을 복원한다(F08)."""
    run_dir_arg = getattr(args, "run_dir", None)
    run_id = getattr(args, "run_id", None)
    if run_dir_arg:
        run_dir = Path(run_dir_arg)
        if not run_dir.is_absolute():
            run_dir = PROJECT_ROOT / run_dir
        run_dir = run_dir.resolve()
        saved = run_dir / "run_config.yaml"
        if saved.exists():
            from .config import _deep_merge, load_config as _load_saved

            import yaml as _yaml
            from .config import _set_dotted

            effective = _load_saved(saved)
            if getattr(args, "config", None):
                # 부분 config는 "명시된 키만" 덮어쓴다(H10).
                partial = read_config_yaml(Path(args.config).read_text(encoding="utf-8")) or {}
                effective = _deep_merge(effective, partial)
                partial_target = (partial or {}).get("target", {}) or {}
                if partial_target.get("fasta") and not partial_target.get("sequence"):
                    effective["target"]["sequence"] = ""  # 새 FASTA가 무시되지 않도록 교체(I07)
            for override in list(getattr(args, "set", []) or []):
                key, value = override.split("=", 1)
                _set_dotted(effective, key.strip(), _yaml.safe_load(value.strip()))
            from .config import _validate

            _validate(effective)  # 병합 후 상호 배타 입력 검증(I07)
            cfg.clear()
            cfg.update(effective)
        if getattr(args, "mode", None):
            cfg["run"]["mode"] = args.mode
        if getattr(args, "mock", False):
            cfg["run"]["mock"] = True
        if getattr(args, "dry_run", False):
            cfg["run"]["dry_run"] = True
        state = RunState(run_dir)
    else:
        run_dir = resolve_run_dir(cfg, run_id=run_id)
        state = RunState(run_dir)
    had_config = (run_dir / "run_config.yaml").exists()
    origin = _fresh_computation_origin(run_dir, state, args, new, had_config)
    if new and not had_config:
        dump_resolved(cfg, run_dir / "run_config.yaml")
        state.data["run_id"] = run_dir.name
        state.data["mode"] = cfg["run"]["mode"]
        if origin:
            state.data["application_version"] = __version__
            state.data["application_version_source"] = origin
        state.save()
    elif origin:
        # A registered new platform job may have prepared its config before CLI
        # ownership began. Existing states, archives and explicit resumes never
        # qualify, so this does not relabel previously computed predictions.
        state.data.update(run_id=run_dir.name, mode=cfg["run"]["mode"], application_version=__version__,
                          application_version_source=origin)
        state.save()
    return run_dir, state


def _set_run_mode(cfg: dict, state: RunState, mode: str, dry_run: bool = False) -> None:
    cfg["run"]["mode"] = mode
    if dry_run:
        return
    dump_resolved(cfg, state.run_dir / "run_config.yaml")
    state.data["mode"] = mode
    state.save()


def _file_content_hash(value) -> str | None:
    import hashlib

    path = resolve_path(None, value) if isinstance(value, (str, Path)) else None
    if path is None or not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _target_fingerprint(cfg: dict, run_dir: Path | None = None, mock: bool = False) -> str:
    import hashlib

    msa_files: dict[str, str] = {}
    if run_dir is not None:
        phase_dir = run_dir / "01_target"
        if phase_dir.exists():
            for path in sorted(phase_dir.rglob("*.a3m")):
                msa_files[str(path.relative_to(phase_dir))] = _file_content_hash(path)
    msa_cfg = cfg_get(cfg, "structure.msa", {}) or {}
    backend = str(msa_cfg.get("backend", "empty"))
    configured_msa = None
    if backend != "empty":
        try:
            sequence = target_prep.resolve_target_sequence(cfg)
        except (OSError, ValueError):
            sequence = ""
        if sequence:
            cache_dir = Path(msa_cfg.get("cache_dir") or "data/msa_cache")
            if not cache_dir.is_absolute():
                cache_dir = PROJECT_ROOT / cache_dir
            configured_msa = cache_dir / f"{msa_utils.sequence_hash(sequence)}.a3m"
    payload = json.dumps({
        "mock": bool(mock),
        "msa_files": msa_files,
        "configured_msa_path": str(configured_msa) if configured_msa else None,
        "configured_msa_exists": bool(
            configured_msa and configured_msa.is_file() and configured_msa.stat().st_size > 0
        ),
        "configured_msa_hash": _file_content_hash(configured_msa),
        "sequence": cfg_get(cfg, "target.sequence"),
        "target_name": cfg_get(cfg, "target.name"),
        "fasta": cfg_get(cfg, "target.fasta"),
        "fasta_hash": _file_content_hash(cfg_get(cfg, "target.fasta")),
        "pdb": str(cfg_get(cfg, "target.pdb")),
        "pdb_hash": _file_content_hash(cfg_get(cfg, "target.pdb")),
        "chain": cfg_get(cfg, "target.chain"),
        "hotspots": cfg_get(cfg, "target.hotspots"),
        "hotspot_set": cfg_get(cfg, "target.hotspot_set"),
        "hotspot_auto": cfg_get(cfg, "hotspots.auto"),
        "provider": cfg_get(cfg, "structure.provider"),
        "msa": cfg_get(cfg, "structure.msa"),
        "boltz2": cfg_get(cfg, "structure.boltz2"),
        "chai1": cfg_get(cfg, "structure.chai1"),
        "seed": cfg_get(cfg, "run.seed"),
        "aux_chains": cfg_get(cfg, "target.aux_chains"),
        "truncate": cfg_get(cfg, "truncate"),
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _target_artifact_hashes(run_dir: Path) -> dict[str, str]:
    phase_dir = run_dir / "01_target"
    names = (
        "target_raw.pdb", "target_raw.cif", "target_prepared.pdb", "target_qc.json",
        "hotspot_table.csv", "hotspot_sets.csv", "target_sequence.fasta", "target_msa.a3m",
    )
    return {
        name: _file_content_hash(phase_dir / name)
        for name in names
        if (phase_dir / name).is_file()
    }


def _resolved_target_cache(run_dir: Path, cached: dict) -> dict:
    resolved = dict(cached)
    for key in ("raw_structure", "structure", "target_msa"):
        value = resolved.get(key)
        if key == "target_msa" and value in (None, "", "empty"):
            continue
        path = resolve_run_artifact(run_dir, value)
        if path is not None:
            resolved[key] = str(path)
    return resolved


def _target_cache_valid(
    run_dir: Path, cached: dict, *, no_hotspots: bool = False, cfg: dict | None = None,
) -> bool:
    phase_dir = run_dir / "01_target"
    expected_hashes = cached.get("artifact_hashes")
    if not isinstance(expected_hashes, dict) or not expected_hashes:
        return False
    if _target_artifact_hashes(run_dir) != expected_hashes:
        return False
    try:
        qc = json.loads((phase_dir / "target_qc.json").read_text(encoding="utf-8"))
        if int(qc.get("sequence_length", -1)) != len(str(cached.get("sequence", ""))):
            return False
        fasta_path = phase_dir / "target_sequence.fasta"
        if not fasta_path.is_file():
            return False
        fasta_records = seq_utils.read_fasta(fasta_path)
        if (
            not fasta_records
            or seq_utils.clean_sequence(next(iter(fasta_records.values())))
            != str(cached.get("sequence", ""))
        ):
            return False
        positions = [int(value) for value in (cached.get("hotspot_seq_positions") or [])]
        if (not positions and not no_hotspots) or (positions and no_hotspots) or any(
            position < 1 or position > len(str(cached.get("sequence", "")))
            for position in positions
        ):
            return False
        hotspot_path = phase_dir / "hotspot_sets.csv"
        if not hotspot_path.is_file():
            return False
        hotspot_frame = pd.read_csv(hotspot_path)
        if "seq_position" not in hotspot_frame.columns:
            return False
        stored_positions = set(
            pd.to_numeric(hotspot_frame["seq_position"], errors="coerce").dropna().astype(int)
        )
        if no_hotspots and (stored_positions or cached.get("hotspot_labels") or cached.get("hotspot_seq_labels")):
            return False
        if not set(positions).issubset(stored_positions):
            return False
        msa_path = cached.get("target_msa")
        if msa_path not in (None, "", "empty"):
            resolved_msa = resolve_run_artifact(run_dir, msa_path)
            if resolved_msa is None or not resolved_msa.is_file():
                return False
            msa_utils.validate_target_a3m(resolved_msa, str(cached.get("sequence", "")))
        if (
            cfg is not None
            and not cfg_get(cfg, "target.pdb")
            and cfg_get(cfg, "structure.provider") in {"boltz2", "boltz", "chai1"}
        ):
            raw_path = resolve_run_artifact(run_dir, cached.get("raw_structure"))
            if raw_path is None or not raw_path.is_file() or raw_path.name not in expected_hashes:
                return False
            records = {"A": target_prep.resolve_target_sequence(cfg)}
            records.update({
                chr(ord("B") + index): entry["sequence"]
                for index, entry in enumerate(target_prep._parse_aux_chains(cfg))
            })
            target_prep.validate_generated_target_identity(raw_path, records)
        structure_path = resolve_run_artifact(run_dir, cached.get("structure"))
        if structure_path is None:
            return cached.get("structure") is None
        if not structure_path.is_file():
            return False
        structure = target_prep.pdb_io.load_structure(structure_path)
        chain_id = str(cached.get("chain_id", "A"))
        chain = target_prep.pdb_io.get_chain(structure, chain_id)
        return bool(list(target_prep.pdb_io.iter_residues(chain)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return False


def _target_phase(cfg: dict, run_dir: Path, state: RunState, mock: bool, dry_run: bool) -> dict:
    for warning in scorer.iptm_availability_warnings(cfg):
        print("[안내] " + warning, file=sys.stderr)
        if not dry_run:
            state.add_warnings(warning)
    if not dry_run: state.begin_phase("target")
    cached = state.get_phase("target")
    fingerprint = _target_fingerprint(cfg, run_dir, mock)
    if cached and cfg_get(cfg, "run.resume", True) and not mock:
        same_inputs = cached.get("fingerprint") == fingerprint
        if not same_inputs:
            if not dry_run:
                state.add_warnings(
                    "타깃 설정/서열/핫스팟이 이전 실행과 달라 target 캐시와 모델 출력을 무효화하고 다시 계산합니다."
                )
                phase_dir = run_dir / "01_target"
                provenance.archive_existing(
                    [phase_dir / "boltz_out", phase_dir / "chai_out"], phase_dir,
                )
        else:
            resolved = _resolved_target_cache(run_dir, cached)
            hotspot_spec = cfg_get(cfg, "target.hotspots", "auto")
            no_hotspots = isinstance(hotspot_spec, list) and not hotspot_spec
            if _target_cache_valid(run_dir, resolved, no_hotspots=no_hotspots, cfg=cfg):
                if not dry_run:
                    state.set_phase("target", resolved)
                return resolved
    info = target_prep.prepare_target(cfg, run_dir, mock=mock, dry_run=dry_run)
    if dry_run:
        return {
            "sequence": info["sequence"],
            "chain_id": info["chain_id"],
            "raw_structure": str(info["raw_structure"]) if info.get("raw_structure") else None,
            "structure": str(info["structure"]) if info.get("structure") else None,
            "target_msa": info.get("target_msa", "empty"),
            "aux_chains": info.get("aux_chains", []),
            "hotspot_labels": info["hotspot_labels"],
            "hotspot_pdb_labels": info.get("hotspot_pdb_labels"),
            "hotspot_seq_positions": info.get("hotspot_seq_positions", []),
            "hotspot_seq_labels": info.get("hotspot_seq_labels", []),
            "hotspot_sets": info["hotspot_sets"],
            "qc": info["qc"],
        }
    state.add_warnings(info.get("warnings", []))
    fingerprint = _target_fingerprint(cfg, run_dir, mock)
    state.set_phase("target", {
        "sequence": info["sequence"],
        "chain_id": info["chain_id"],
        "raw_structure": str(info["raw_structure"]) if info.get("raw_structure") else None,
        "structure": str(info["structure"]) if info.get("structure") else None,
        "target_msa": info.get("target_msa", "empty"),
        "aux_chains": info.get("aux_chains", []),
        "hotspot_labels": info["hotspot_labels"],
        "hotspot_pdb_labels": info.get("hotspot_pdb_labels"),
        "hotspot_seq_positions": info.get("hotspot_seq_positions", []),
        "hotspot_seq_labels": info.get("hotspot_seq_labels", []),
        "hotspot_sets": info["hotspot_sets"],
        "qc": info["qc"],
        "fingerprint": fingerprint,
        "artifact_hashes": _target_artifact_hashes(run_dir),
    })
    return state.get_phase("target")


def _select_boltz_candidates(
    cfg: dict,
    candidates: pd.DataFrame,
    predictions: pd.DataFrame,
    target: dict,
    id_column: str,
    n: int,
) -> pd.DataFrame:
    """1차 엔진 예측을 스코어링해 상위 n종을 고른다 (FASTA 입력 순서 사용 금지)."""
    if n <= 0 or predictions.empty:
        return candidates.iloc[0:0]
    prelim = _score_candidates(cfg, predictions, target, id_column=id_column)
    score_column = "iptm" if "iptm" in prelim.columns else None
    if score_column is None or prelim[score_column].isna().all():
        ranked = predictions[id_column].tolist()
    else:
        sort_cols = [score_column]
        if "ipsae" in prelim.columns and prelim["ipsae"].notna().any():
            sort_cols.append("ipsae")
        ranked = prelim.sort_values(sort_cols, ascending=False)[id_column].tolist()
    ranked = ranked[:n]
    return candidates[candidates[id_column].isin(ranked)].copy()


def _predictions_columns(frame: pd.DataFrame, id_column: str) -> pd.DataFrame:
    if id_column not in frame.columns:
        frame = frame.copy()
        frame[id_column] = pd.Series(dtype="object")
    keep = [id_column] + [c for c in ("pred_dir", "engine", "target_chain", "binder_chain", "model_index") if c in frame.columns]
    return frame[keep].copy()


def _score_candidates(cfg: dict, pred_frame: pd.DataFrame, target: dict, id_column: str = "design_id") -> pd.DataFrame:
    rows: list[dict] = []
    for _, row in pred_frame.iterrows():
        candidate_id = str(row[id_column])
        if row.get("prediction_status") == "failed":
            rows.append({id_column: candidate_id, "error": row.get("prediction_error") or "예측 엔진 실패"})
            continue
        target_chain = str(row.get("target_chain", "A"))
        binder_chain = str(row.get("binder_chain", "B"))
        pred_dir = _pick_pred_dir(row)
        if pred_dir is None:
            rows.append({id_column: candidate_id, "error": "예측 경로를 찾을 수 없습니다(존재하지 않는 pred_dir)"})
            continue
        try:
            if pred_dir.is_dir() or pred_dir.is_file():
                raw_model_index = row.get("model_index")
                model_index = None
                if raw_model_index is not None and not pd.isna(raw_model_index):
                    if isinstance(raw_model_index, bool) or float(raw_model_index) != int(raw_model_index) or int(raw_model_index) < 0:
                        raise ValueError("모델 번호는 0 이상의 정수여야 합니다.")
                    model_index = int(raw_model_index)
                pred = scorer.find_prediction_files(pred_dir, model_index=model_index)
            else:
                matches = sorted(pred_dir.parent.glob(f"{candidate_id}*.pdb")) if pred_dir.parent.exists() else []
                if not matches:
                    raise FileNotFoundError(pred_dir)
                pred = scorer.find_prediction_files(matches[0])
            hotspot_labels = _prediction_hotspot_labels(pred.cif, target_chain, target)
            _validate_candidate_prediction_sequences(pred.cif, target_chain, binder_chain, row, target)
            metrics = scorer.score_complex(
                pred.cif, target_chain, binder_chain, hotspot_labels, cfg, pred=pred, label=candidate_id
            )
            metrics[id_column] = candidate_id
            rows.append(metrics)
        except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired):
            raise
        except Exception as exc:  # noqa: BLE001
            rows.append({id_column: candidate_id, "error": screen.prediction_failure_reason(pred_dir) or str(exc)})
    frame = pd.DataFrame(rows)
    if id_column not in frame.columns:
        frame[id_column] = pd.Series(dtype="object")
    if "label" in frame.columns:
        frame = frame.drop(columns=["label"])
    return frame


def _prediction_hotspot_labels(prediction_path: Path, target_chain: str, target: dict) -> list[str]:
    positions = _target_hotspot_seq_positions(target)
    declared_sequence = seq_utils.clean_sequence(str(target.get("sequence") or ""))
    if not declared_sequence:
        legacy_labels = list(target.get("hotspot_seq_labels") or target.get("hotspot_labels") or [])
        if not positions and not target.get("hotspot_pdb_labels"):
            return legacy_labels
        if legacy_labels and all(str(label).split(":", 1)[0] == target_chain for label in legacy_labels if ":" in str(label)):
            return legacy_labels
        raise ValueError(
            "핫스팟 서열 위치를 예측 구조 라벨로 매핑하려면 target.sequence 가 필요합니다."
        )
    structure = pdb_io.load_structure(prediction_path)
    chain = pdb_io.get_chain(structure, target_chain)
    predicted_residues = list(pdb_io.iter_residues(chain))
    predicted_sequence = pdb_io.chain_sequence(chain)
    if predicted_sequence != declared_sequence and not target.get("structure"):
        raise ValueError(
            "예측 타깃 체인 "
            f"{target_chain} 서열이 target.sequence 와 정확히 일치하지 않아 hotspot_seq_labels 를 매핑할 수 없습니다 "
            f"(target.sequence 길이={len(declared_sequence)}, 예측 길이={len(predicted_sequence)}, "
            f"{_sequence_mismatch_hint(declared_sequence, predicted_sequence)}). "
            "동일한 타깃 서열/체인으로 생성한 예측인지 확인하세요. "
            "절단 타깃은 원래 잔기 ID를 보존한 target.structure 준비 구조가 필요합니다."
        )
    if not positions and not target.get("hotspot_pdb_labels"):
        if predicted_sequence != declared_sequence:
            # Design outputs can contain the resolved prepared primary chain,
            # including coordinate gaps/truncation. Never infer it by alignment
            # or concatenate partner chains to match the full sequence.
            prepared_path = target.get("structure")
            if not prepared_path or not Path(prepared_path).is_file():
                raise ValueError(f"target.structure 준비 구조를 찾을 수 없습니다: {prepared_path}")
            chain_id = target.get("chain_id")
            if not chain_id:
                raise ValueError("준비 구조 서열 검증에는 target.chain_id 가 필요합니다.")
            prepared = pdb_io.load_structure(prepared_path)
            prepared_sequence = pdb_io.chain_sequence(pdb_io.get_chain(prepared, str(chain_id)))
            if predicted_sequence != prepared_sequence:
                raise ValueError(
                    f"예측 타깃 체인 {target_chain} 서열이 target.sequence 또는 준비 구조 체인 {chain_id} "
                    f"서열과 정확히 일치하지 않습니다 ({_sequence_mismatch_hint(prepared_sequence, predicted_sequence)})."
                )
        return list(target.get("hotspot_labels") or target.get("hotspot_seq_labels") or [])
    if predicted_sequence != declared_sequence or not positions:
        positions = _prepared_hotspot_positions(target, declared_sequence, predicted_sequence, positions)
    labels = [pdb_io.residue_label(residue, target_chain) for residue in predicted_residues]
    mapped: list[str] = []
    for position in positions:
        if not 1 <= position <= len(labels):
            raise ValueError(
                f"핫스팟 서열 위치 {position} 이 예측 타깃 체인 {target_chain} 범위(1-{len(labels)})를 벗어났습니다."
            )
        mapped.append(labels[position - 1])
    return mapped


def _validate_candidate_prediction_sequences(
    prediction_path: Path, target_chain: str, binder_chain: str, row: pd.Series, target: dict
) -> None:
    """Bind scoring to the declared candidate and partner sequences, not just their lengths."""
    from collections import Counter

    if target_chain == binder_chain:
        raise ValueError("예측 타깃 체인과 바인더 체인은 서로 달라야 합니다.")
    structure = pdb_io.load_structure(prediction_path)
    binder_sequence = pdb_io.chain_sequence(pdb_io.get_chain(structure, binder_chain))
    # Preliminary engine-only frames have no candidate sequence column. Their
    # exact inputs are validated by the predictor wrapper; final candidate rows
    # carry the sequence and must be checked again before producing scores.
    if "sequence" in row:
        value = row["sequence"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("후보 sequence 가 없어서 예측 바인더 서열을 검증할 수 없습니다.")
        expected = seq_utils.clean_sequence(value)
        if not expected or binder_sequence != expected:
            raise ValueError(
                f"예측 바인더 체인 {binder_chain} 서열이 후보 sequence 와 정확히 일치하지 않습니다 "
                f"(후보 길이={len(expected)}, 예측 길이={len(binder_sequence)}, "
                f"{_sequence_mismatch_hint(expected, binder_sequence)})."
            )
    expected_partners = Counter(
        seq_utils.clean_sequence(str(entry.get("sequence") or ""))
        for entry in (target.get("aux_chains") or [])
    )
    observed_partners = Counter(
        sequence for chain in next(structure.get_models())
        if chain.id not in {target_chain, binder_chain}
        and (sequence := pdb_io.chain_sequence(chain))
    )
    if observed_partners != expected_partners:
        raise ValueError("예측 보조 체인 서열/개수가 준비된 target.aux_chains 와 정확히 일치하지 않습니다.")


def _prepared_hotspot_positions(
    target: dict, declared_sequence: str, predicted_sequence: str, original_positions: list[int]
) -> list[int]:
    """Map explicit native hotspot IDs through an exactly matching single prepared chain."""
    prepared_path = target.get("structure")
    if not prepared_path or not Path(prepared_path).is_file():
        raise ValueError(
            f"target.structure 준비 구조를 찾을 수 없습니다: {prepared_path}. "
            "이 예측에 사용한 원래 잔기 ID가 보존된 준비 구조를 지정하세요."
        )
    chain_id = target.get("chain_id")
    if not chain_id:
        raise ValueError("준비 구조 매핑에는 원래 체인 ID인 target.chain_id 가 필요합니다.")
    prepared = pdb_io.load_structure(prepared_path)
    protein_chains = [
        chain for chain in next(prepared.get_models()) if any(pdb_io.iter_residues(chain))
    ]
    if len(protein_chains) != 1:
        raise ValueError(
            "준비 구조 매핑은 단백질 체인 1개만 지원합니다 "
            f"(발견: {[chain.id for chain in protein_chains]}). "
            "여러 체인을 합친 예측 타깃은 매핑할 수 없습니다. 단일 타깃 체인으로 준비하고 다시 예측하세요."
        )
    chain = protein_chains[0]
    if chain.id != chain_id:
        raise ValueError(
            f"target.chain_id='{chain_id}' 가 준비 구조의 단백질 체인 '{chain.id}' 와 다릅니다. "
            "변환된 체인 이름을 추측하지 않습니다. 원래 체인/잔기 ID를 보존한 준비 구조를 지정하세요."
        )
    prepared_sequence = pdb_io.chain_sequence(chain)
    if predicted_sequence != prepared_sequence:
        raise ValueError(
            f"예측 타깃 서열이 준비 구조 체인 {chain_id} 서열과 정확히 일치하지 않습니다 "
            f"(준비 길이={len(prepared_sequence)}, 예측 길이={len(predicted_sequence)}, "
            f"{_sequence_mismatch_hint(prepared_sequence, predicted_sequence)}). "
            "target.structure 가 이 예측에 사용한 준비 구조인지 확인하세요."
        )
    native_labels = list(target.get("hotspot_pdb_labels") or [])
    if not native_labels:
        raise ValueError("준비 구조 매핑에는 원래 잔기 ID인 target.hotspot_pdb_labels 가 필요합니다.")
    if original_positions and len(original_positions) != len(native_labels):
        raise ValueError(
            "target.hotspot_pdb_labels 와 hotspot_seq_positions 개수가 다릅니다. "
            "동일한 순서로 대응하는 원래 핫스팟 정보를 확인하세요."
        )
    native_indices = {
        pdb_io.residue_label(residue, chain_id): index
        for index, residue in enumerate(pdb_io.iter_residues(chain))
    }
    mapped_positions: list[int] = []
    for index, label in enumerate(native_labels):
        if label not in native_indices:
            raise ValueError(
                f"원래 핫스팟 {label} 이 준비 구조 체인 {chain_id} 에 없습니다. "
                "target.hotspot_pdb_labels 와 준비 구조의 체인/잔기 ID를 확인하세요."
            )
        prepared_index = native_indices[label]
        if original_positions:
            position = original_positions[index]
            if not 1 <= position <= len(declared_sequence):
                raise ValueError(
                    f"hotspot_seq_positions 의 {position} 이 target.sequence 범위(1-{len(declared_sequence)})를 "
                    "벗어났습니다. 원래 핫스팟 서열 위치를 확인하세요."
                )
            if prepared_sequence[prepared_index] != declared_sequence[position - 1]:
                raise ValueError(
                    f"원래 핫스팟 {label} 의 아미노산 {prepared_sequence[prepared_index]} 이 "
                    f"target.sequence 위치 {position} 의 {declared_sequence[position - 1]} 와 다릅니다. "
                    "원래 서열 위치와 준비 구조의 잔기 변형 여부를 확인하세요."
                )
        mapped_positions.append(prepared_index + 1)
    return mapped_positions


def _target_hotspot_seq_positions(target: dict) -> list[int]:
    raw_positions = target.get("hotspot_seq_positions") or []
    positions: list[int] = []
    for value in raw_positions:
        positions.append(int(value))
    if positions:
        return positions
    for label in target.get("hotspot_seq_labels") or []:
        text = str(label)
        try:
            positions.append(int(text.split(":", 1)[1] if ":" in text else text))
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"핫스팟 서열 라벨을 해석할 수 없습니다: {label}") from exc
    return positions


def _sequence_mismatch_hint(expected: str, observed: str) -> str:
    for index, (left, right) in enumerate(zip(expected, observed), start=1):
        if left != right:
            return f"첫 불일치={index}:{left}->{right}"
    if len(expected) != len(observed):
        return f"공통 접두사 {min(len(expected), len(observed))} aa 뒤 길이 불일치"
    return "서열 불일치"


def _pick_pred_dir(row: pd.Series) -> Path | None:
    """예측 디렉터리/파일 경로를 반환한다. 존재하지 않으면 None (cwd 대체 금지, G07)."""
    for key in ("pred_dir", "pred_dir_y", "pred_dir_x", "pred_dir_boltz"):
        value = row.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)) or str(value) in ("", "None", "nan"):
            continue
        path = Path(str(value))
        if path.exists():
            return path
    return None


def _finalize(
    cfg: dict,
    run_dir: Path,
    state: RunState,
    candidates: pd.DataFrame,
    target: dict,
    cdr_ranges: dict | None = None,
    id_column: str = "design_id",
    sequence_column: str = "sequence",
    *, publish_report: bool = True,
) -> dict:
    """스코어링 결과 병합 → 개발가능성 → 랭킹/선별 → 합성시트 → 리포트."""
    score_dir = run_dir / "03_score"
    score_dir.mkdir(parents=True, exist_ok=True)
    final_dir = run_dir / "05_final"
    final_dir.mkdir(parents=True, exist_ok=True)

    previous = state.get_phase("final")
    previous_publication = {key: value for key, value in previous.items() if key != "previous_publication"}
    state.set_phase("final", {**previous, "previous_publication": previous_publication,
        "tables_committed": False, "exports_status": "not_started", "report_status": "blocked",
        "delivery_status": "blocked", "report": None, "synthesis": None, "handoff_gate": None})
    metrics = _score_candidates(cfg, candidates, target, id_column)
    state.add_warnings(scorer.iptm_missing_warnings(metrics.to_dict("records")))
    merged = candidates.merge(metrics, on=id_column, how="left", suffixes=("", "_score"))
    merged = merged.loc[:, ~merged.columns.duplicated()]
    if "error_score" in merged.columns:
        # A source-stage error must not conceal a final scoring failure.
        merged["input_error"] = merged.get("error")
        merged["error"] = merged["error_score"]
    score_errors = merged.get("error", pd.Series(None, index=merged.index, dtype="object")).notna()
    prediction_failed = merged.get("prediction_status", pd.Series("complete", index=merged.index)).eq("failed")
    merged["final_evaluated"] = (
        merged[id_column].isin(metrics[id_column]) & ~score_errors & ~prediction_failed
    )
    # The scored structure is the provenance of these metrics. A design-stage
    # path (or its CSV NaN) must never stand in for a missing prediction.
    if "cif_path" in candidates.columns:
        merged["input_cif_path"] = merged["cif_path"]
        merged["cif_path"] = merged.get("cif_path_score", pd.Series(None, index=merged.index, dtype="object"))
    for field in ("target_chain", "binder_chain"):
        if field + "_score" in merged.columns:
            merged[field] = merged[field + "_score"]
    if "error" in merged.columns:
        errors = merged[merged["error"].notna()][id_column].tolist()
        if errors:
            state.add_warnings([f"스코어링 실패 {len(errors)}종 (예: {errors[:3]})"])

    esm_frame = _run_sequence_scores(cfg, run_dir, merged, state, sequence_column)
    merged[id_column] = merged[id_column].astype(str)
    if id_column in esm_frame.columns:
        esm_frame[id_column] = esm_frame[id_column].astype(str)
    merged = merged.merge(esm_frame, on=id_column, how="left")
    merged = merged.loc[:, ~merged.columns.duplicated()]
    if "esm_llr" in merged.columns and merged["esm_llr"].notna().any():
        merged["esm_score"] = merged["esm_llr"]
    elif "esm_mean_logprob" in merged.columns and merged["esm_mean_logprob"].notna().any():
        values = pd.to_numeric(merged["esm_mean_logprob"], errors="coerce")
        std = values.std(ddof=0)
        merged["esm_score"] = ((values - values.mean()) / std).fillna(0.0) if std and std > 0 else 0.0
    else:
        merged["esm_score"] = float("nan")

    cdr_by_id = None
    cdr_source = str(cfg_get(cfg, "mature.cdr.source", "auto")).lower()
    if not cdr_ranges and cdr_source == "config":
        configured_ranges = cfg_get(cfg, "mature.cdr.regions", {}) or {}
        normalized_configured_ranges = {}
        if isinstance(configured_ranges, dict):
            for name, bounds in configured_ranges.items():
                if (
                    name not in {"CDR1", "CDR2", "CDR3"}
                    or not isinstance(bounds, (list, tuple))
                    or len(bounds) != 2
                ):
                    continue
                try:
                    start, end = int(bounds[0]), int(bounds[1])
                except (TypeError, ValueError):
                    continue
                if 1 <= start <= end:
                    normalized_configured_ranges[name] = (start, end)
        cdr_ranges = normalized_configured_ranges
        if not cdr_ranges:
            state.add_warnings("mature.cdr.source=config 이지만 유효한 CDR 범위가 없어 CDR 개발가능성을 미평가합니다.")
        else:
            # 설정 범위는 서열 하나를 기준으로 검토한 값이다. 길이가 다른 후보(라이브러리 스크리닝)에서는
            # 범위가 서열을 벗어나 조용히 '미평가'가 되고 기본 선별에서 빠진다. 어떤 후보가 왜 빠졌는지 알린다.
            longest = max(int(end) for _, end in cdr_ranges.values())
            too_short = [str(row[id_column]) for _, row in merged.iterrows()
                         if len(str(row[sequence_column])) < longest]
            if too_short:
                shown = ", ".join(too_short[:5]) + ("" if len(too_short) <= 5 else f" 외 {len(too_short) - 5}개")
                state.add_warnings(
                    f"mature.cdr.regions 의 최대 위치({longest})가 후보 {len(too_short)}종의 서열 길이를 넘어 "
                    f"CDR 의존 liability 를 미평가로 기록하고 기본 선별에서 제외합니다({shown}). "
                    "서열마다 길이가 다르면 설정 범위 하나로는 맞출 수 없습니다. "
                    "envs/setup_antibody.sh 로 ANARCI 를 설치해 mature.cdr.source=auto 를 쓰거나, "
                    "각 서열에 맞는 범위를 확인하세요."
                )
    elif not cdr_ranges and not _is_mock(cfg) and not _is_dry_run(cfg) and _antibody_env_available(cfg):
        cdr_by_id = mature.resolve_cdr_ranges_batch(
            cfg,
            {str(row[id_column]): str(row[sequence_column]) for _, row in merged.iterrows()},
            run_dir / "04_developability",
        )
        annotated = sum(1 for value in cdr_by_id.values() if value)
        if annotated < len(merged):
            state.add_warnings(
                f"ANARCI CDR 주석: {annotated}/{len(merged)} 후보 성공. "
                "실패 후보의 CDR 의존 liability 는 '미평가'로 기록됩니다."
            )
    elif not cdr_ranges and (_is_mock(cfg) or _is_dry_run(cfg)):
        state.add_warnings("mock/dry-run: CDR 의존 liability 는 미평가로 기록됩니다.")
    liability_columns = {
        "n_cys", "pI", "net_charge", "mw_kda", "gravy",
        "n_hydrophobic_patches", "cdr_hydrophobic_patches",
    }
    liability_columns.update(column for column in merged.columns if column.startswith("dev_"))
    merged = merged.drop(columns=[column for column in liability_columns if column in merged.columns])
    merged = developability.triage(
        merged, cfg, cdr_ranges, sequence_column=sequence_column,
        cdr_by_id=cdr_by_id, id_column=id_column,
    )

    max_severe = float(cfg_get(cfg, "rank.max_dev_severe", 1))
    fallback = bool(cfg_get(cfg, "rank.fallback_to_ineligible", False))

    def passed(value) -> bool:
        # CSV columns with missing values can contain NaN, strings or 0/1.
        # Their Python truthiness is not a recorded scientific gate decision.
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() == "true"
        if isinstance(value, (int, float, np.integer, np.floating)):
            return math.isfinite(float(value)) and value == 1
        return False

    def exclusion_reasons(row: pd.Series) -> list[str]:
        reasons: list[str] = []
        if pd.notna(row.get("error")):
            return ["scoring_error"]
        if not passed(row.get("pass_clash", False)):
            reasons.append("clash")
        if not passed(row.get("pass_ipsae", False)):
            reasons.append("ipsae")
        if not passed(row.get("pass_iptm", False)):
            reasons.append("iptm")
        if not passed(row.get("pass_plddt", False)):
            reasons.append("interface_plddt")
        if cfg_get(cfg, "rank.require_hotspot", False) and not passed(row.get("pass_hotspot", False)):
            reasons.append("hotspot")
        if not passed(row.get("dev_cdr_evaluated", False)):
            reasons.append("developability_unassessed")
        try:
            raw_severe = row.get("dev_severe")
            severe = float(raw_severe)
            if (isinstance(raw_severe, (bool, np.bool_)) or not math.isfinite(severe)
                    or severe < 0 or not severe.is_integer() or severe > max_severe):
                reasons.append("developability")
        except (TypeError, ValueError):
            reasons.append("developability")
        return reasons

    merged["exclusion_reason"] = merged.apply(lambda row: ";".join(exclusion_reasons(row)), axis=1)
    merged["final_eligible"] = merged["exclusion_reason"].eq("")
    is_mature = str(cfg_get(cfg, "run.mode", "")) == "mature"
    from .cascade import control_flags
    is_control = control_flags(merged, id_column) if is_mature or cfg_get(cfg, "screen.cascade.enabled", False) or cfg_get(cfg, "screen.comparison.enabled", False) else pd.Series(False, index=merged.index)
    merged.loc[is_control, "exclusion_reason"] = "parent_control"
    merged.loc[is_control & merged[id_column].astype(str).eq("WT"), "exclusion_reason"] = "wt_control"
    merged.loc[is_control, "final_eligible"] = False
    # Keep historical quality gates/ranks intact. Versioned structural review
    # adds site-aware eligibility and consensus without averaging engine scores.
    from .pose_integration import annotate_frame
    merged, pose_receipt = annotate_frame(merged, cfg, target, run_dir, id_column, sequence_column)
    if pose_receipt.get("error"):
        state.add_warnings("자세 진단 미평가: " + pose_receipt["error"]["reason"])
    n_eligible = int(merged["final_eligible"].sum())
    if n_eligible == 0:
        state.add_warnings(
            "final_eligible 후보가 0종입니다. "
            + ("rank.fallback_to_ineligible=true는 종합점수가 있는 부적격 후보만 포함하며 미평가 보류 후보에는 적용되지 않습니다." if fallback
               else "합성 목록은 비어 있습니다. 임계값/예측 품질을 점검하세요.")
        )
    from . import engine_comparison
    if engine_comparison.enabled(cfg):
        ranked, selected = engine_comparison.rank_and_select(merged, cfg, cdr_ranges, sequence_column)
        # composite_score/rank have meaning only inside their recorded engine.
        scored_sorted = ranked.reset_index(drop=True)
    else:
        ranked = rank_select.rank_candidates(merged, cfg, cdr_ranges, sequence_column=sequence_column)
        # 모든 원본 대조군은 fallback과 무관하게 발주 선별 풀에서 제거한다.
        selection_pool = ranked[~control_flags(ranked, id_column)] if is_control.any() else ranked
        selected = rank_select.select_final(
            selection_pool, cfg, cdr_ranges, sequence_column=sequence_column, allow_fallback=fallback,
            pre_ranked=True,
        )
        scored_sorted = ranked.reset_index(drop=True)
    state.add_warnings(rank_select.ranking_warnings(scored_sorted, cfg))
    for frame in (scored_sorted, selected):
        if "cif_path" in frame.columns:
            frame["cif_path"] = frame["cif_path"].map(lambda value: _portable_run_artifact(run_dir, value))
    # Commit the configuration that actually produced these tables even when
    # a later derived export fails. Report-only recovery must never replace it.
    dump_resolved(cfg, run_dir / "run_config.yaml")
    state.data["scoring_version"] = __version__
    write_csv(score_dir / "scored_candidates.csv", scored_sorted, root=run_dir)
    write_csv(final_dir / "selected_candidates.csv", selected, root=run_dir)
    pose_receipt["source_csv_sha256"] = provenance.file_digest(score_dir / "scored_candidates.csv")
    write_json(score_dir / "pose_assessment.json", pose_receipt, root=run_dir)
    from .publication import table_hashes
    final_record = {"scored_csv": str(score_dir / "scored_candidates.csv"),
                    "selected_csv": str(final_dir / "selected_candidates.csv"),
                    "tables_committed": True, "exports_status": "pending", "report_status": "pending",
                    "report": None, "synthesis": None, "n_selected": int(len(selected)),
                    "previous_publication": previous_publication, "table_sha256": table_hashes(run_dir)}
    final_record["ranking_policy_status"] = "pending"
    state.set_phase("final", final_record)
    try:
        applied_ranking_policy = rank_select.ranking_policy(cfg, frame=scored_sorted)
        write_json(score_dir / "ranking_policy.json", applied_ranking_policy, root=run_dir)
        final_record["ranking_policy_status"] = "complete"
        state.set_phase("final", final_record)
        if applied_ranking_policy["cross_engine_scores_combined"]:
            state.add_warnings("일부 후보만 Boltz-2로 재평가하는 기존 경로에서 여러 엔진의 관측을 함께 정규화했습니다. "
                               "이 순위는 엔진 간 독립 비교가 아닙니다. 독립 비교는 screen.comparison.enabled=true로 실행하세요.")
        exports = _final_exports(cfg, run_dir, state, scored_sorted, selected, id_column, sequence_column)
    except Exception as exc:
        if final_record["ranking_policy_status"] == "pending":
            final_record["ranking_policy_status"] = "failed"
            state.add_warnings("순위 정책 영수증을 저장하지 못했습니다. 계산 CSV와 설정은 보존했으며, "
                               "이전 ranking_policy.json이 남아 있어도 새 표의 정책으로 확정하지 않습니다.")
        final_record.update(exports_status="failed", exports_error=f"{type(exc).__name__}: {exc}")
        state.set_phase("final", final_record)
        raise
    synthesis, pymol_script = exports["synthesis"], exports["pymol_script"]
    gate_summary, gate_blocks = exports["handoff_gate"], exports["gate_blocks"]
    final_record.update(exports_status="complete", synthesis=synthesis, handoff_gate=gate_summary)
    state.set_phase("final", final_record)
    if not publish_report:
        return {"selected": selected, "scored": scored_sorted, "report": None, "synthesis": synthesis,
                "gate_blocks": gate_blocks}

    context = {
        "run_id": run_dir.name,
        "mode": cfg_get(cfg, "run.mode"),
        "handoff_gate": gate_summary,
        "num_candidates": int(len(candidates)),
        "num_clusters": int(selected["cluster_id"].nunique()) if "cluster_id" in selected.columns else 0,
        "hotspots": target.get("hotspot_labels", []),
        "target_qc": target.get("qc", {}),
        "synthesis": synthesis,
        "pymol_script": pymol_script,
        "warnings": state.warnings,
        "n_eligible": n_eligible,
    }
    try:
        report_path = report.generate_report(run_dir, cfg, context, scored=scored_sorted, selected=selected)
    except Exception as exc:
        final_record.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
        state.set_phase("final", final_record)
        state.add_warnings(
            "보고서 생성에 실패했습니다. 계산 CSV와 합성 기록은 저장되었습니다. "
            "원인을 복구한 뒤 `nanocofold report --run-dir`로 보고서만 다시 생성하세요."
        )
        raise
    final_record.update(report=str(report_path), report_status="complete")
    state.set_phase("final", final_record)
    print(f"[완료] 선별 {len(selected)}종 · 리포트: {report_path}")
    if gate_blocks:
        # 진단용 리포트/상태를 남긴 뒤 중단하고, specificity 경로와 같은 종료 코드(3)를 반환한다.
        print(
            f"[중단] handoff.require_gate=true 이지만 게이트 미통과({gate_summary['n_errors']}건)입니다. "
            "05_final/handoff_gate.json 을 확인하세요.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    return {"selected": selected, "scored": scored_sorted, "report": report_path, "synthesis": synthesis}


def _final_exports(cfg, run_dir, state, scored_sorted, selected, id_column="design_id", sequence_column="sequence"):
    """Recover derived exports from committed tables, without scoring or inference."""
    from . import engine_comparison
    final_dir = run_dir / "05_final"

    if engine_comparison.enabled(cfg):
        synthesis = engine_comparison.write_synthesis_by_engine(selected, cfg, final_dir, sequence_column)
        state.add_warnings("엔진별 추천 목록을 따로 저장했습니다. 통합 발주 목록은 연구자가 서열을 선택한 뒤 작성해야 합니다.")
    else:
        synthesis = handoff.write_synthesis_sheet(selected, cfg, final_dir, sequence_column=sequence_column)
    pymol_script = None
    if bool(cfg_get(cfg, "report.pymol_sessions", True)):
        structures = []
        for _, row in selected.head(20).iterrows():
            path = resolve_run_artifact(run_dir, row.get("cif_path"))
            if path is not None and path.exists():
                structures.append((
                    str(row.get(id_column)),
                    str(path),
                    str(row.get("binder_chain") or "B"),
                ))
        if structures:
            pymol_script = handoff.write_pymol_session_script(
                structures, final_dir / "top_candidates.pml"
            )

    # 발주 목록을 만드는 모든 경로에서 handoff 게이트를 평가한다(수동 명령에만 의존하지 않음).
    # The computation commits run_config.yaml with the score tables. Derived
    # export recovery reads that receipt and cannot introduce report-time gates.
    gate = handoff.validate_handoff(run_dir)
    gate_summary = {"ok": bool(gate.get("ok")), "n_errors": len(gate.get("errors", [])),
                    "n_warnings": len(gate.get("warnings", []))}
    write_json(final_dir / "handoff_gate.json", {"ok": gate_summary["ok"], "errors": gate.get("errors", []),
                                               "warnings": gate.get("warnings", [])}, root=run_dir)
    if not gate_summary["ok"]:
        state.add_warnings(
            f"handoff 게이트 미통과({gate_summary['n_errors']}건): 발주 목록은 확정할 수 없습니다. "
            "`nanocofold validate-handoff` 로 항목을 확인하세요."
        )
    gate_blocks = (
        bool(cfg_get(cfg, "handoff.require_gate", False))
        and not gate_summary["ok"]
        and not _is_mock(cfg)
    )

    return {"synthesis": synthesis, "pymol_script": pymol_script,
            "handoff_gate": gate_summary, "gate_blocks": gate_blocks}


def _portable_run_artifact(run_dir: Path, value):
    if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value):
        return value
    path = resolve_run_artifact(run_dir, value)
    if path is None:
        return value
    try:
        return str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        return str(path)


def _run_sequence_scores(cfg: dict, run_dir: Path, merged: pd.DataFrame, state: RunState, sequence_column: str) -> pd.DataFrame:
    id_column = next((c for c in ("design_id", "variant_id") if c in merged.columns), merged.columns[0])
    if "esm_pll" in merged.columns:
        cols = [id_column] + [c for c in ("esm_pll", "esm_delta") if c in merged.columns]
        return merged[cols].copy()
    reference = merge_reference_sequence(cfg, merged, sequence_column)
    sequences = {str(row[id_column]): str(row[sequence_column]) for _, row in merged.iterrows()}
    esm_dir = run_dir / "04_developability"
    mock_run = bool(cfg_get(cfg, "run.mock", False))
    dry_run = bool(cfg_get(cfg, "run.dry_run", False))
    if dry_run:
        frame = merged[[id_column]].copy()
        frame["esm_mean_logprob"] = float("nan")
        frame["esm_delta"] = float("nan")
        frame["esm_source"] = "dry_run"
        return frame
    if not mock_run and not _esm_env_available(cfg):
        missing = merged[[id_column]].copy() if id_column in merged.columns else merged[["design_id"]].copy()
        missing = missing.rename(columns={id_column: id_column}) if id_column in missing.columns else missing
        missing["esm_mean_logprob"] = float("nan")
        missing["esm_delta"] = float("nan")
        missing["esm_source"] = "missing_env"
        state.add_warnings(
            "ESM env 를 찾지 못해 ESM 서열 점수를 결측(NaN)으로 기록합니다. envs/setup_esm.sh 를 실행하세요."
        )
        return missing
    frame = developability.run_esm_sequence_scores(
        sequences, reference, cfg, esm_dir, mock=mock_run, dry_run=dry_run
    )
    if id_column != "design_id" and "design_id" in frame.columns:
        frame = frame.rename(columns={"design_id": id_column})
    frame["esm_source"] = "mock" if mock_run else "computed"
    keep = [c for c in (id_column, "esm_mean_logprob", "esm_delta", "esm_source") if c in frame.columns]
    return frame[keep].copy()


def merge_reference_sequence(cfg: dict, merged: pd.DataFrame, sequence_column: str) -> str:
    configured = cfg_get(cfg, "developability.reference_sequence")
    if configured:
        return str(configured)
    if sequence_column in merged.columns and len(merged):
        return str(merged[sequence_column].iloc[0])
    return ""


def _esm_env_available(cfg: dict) -> bool:
    env = resolve_path(cfg, cfg_get(cfg, "mature.esm.env"))
    if env is None:
        return False
    return (env / "bin" / "python").exists()


def _tier2_selection(cfg, candidates, tier1_metrics, id_column):
    """Order finite first-stage scores; ties retain their recorded input order.

    A canonical unavailable pDockQ is never converted back to its raw author
    zero to influence selection. No-interface and missing-confidence rows can
    therefore tie; this is an availability-policy change since audit 67.
    """
    policy = {"schema_version": 1, "method": "first_stage_lexicographic_v1",
              "tie_break": "recorded_tier1_row_order", "missing_values": "last_per_metric; unavailable pdockq remains missing",
              "pdockq_unavailable_statuses_tied": ["no_interface", "missing_contact_atoms", "missing_plddt"],
              "calibrated_binding_probability": False}
    if tier1_metrics is not None and not tier1_metrics.empty and "pdockq" in tier1_metrics:
        columns = ["pdockq"]
        if float(cfg_get(cfg, "rank.weights.hotspot_contact_frac", 0.5)) > 0 and "hotspot_contact_frac" in tier1_metrics:
            columns.insert(0, "hotspot_contact_frac")
        policy["sort_columns_descending"] = columns
        # Explicit row-order tie breaking also makes multi-key sorting stable.
        ordered = tier1_metrics.assign(_tier1_row_order=np.arange(len(tier1_metrics)))
        ordered = ordered.sort_values(columns + ["_tier1_row_order"], ascending=[False] * len(columns) + [True], na_position="last")
        return ordered[id_column].tolist(), policy
    policy.update(method="candidate_input_order_v1", tie_break="recorded_candidate_row_order", sort_columns_descending=[])
    return candidates[id_column].tolist(), policy


def _tier2_and_finalize(
    cfg: dict,
    run_dir: Path,
    state: RunState,
    candidates: pd.DataFrame,
    target: dict,
    cdr_ranges: dict | None,
    id_column: str,
    sequence_column: str,
    tier1_metrics: pd.DataFrame | None = None,
) -> dict:
    if not bool(cfg_get(cfg, "run.dry_run", False)): state.begin_phase("tier2")
    from . import engine_comparison
    if engine_comparison.enabled(cfg):
        return engine_comparison.run(cfg, run_dir, state, target, candidates, cdr_ranges,
                                     predict=screen.predict_complexes, finalize=_finalize,
                                     id_column=id_column, sequence_column=sequence_column)
    tier2_n = int(cfg_get(cfg, "screen.tier2_n", 50))
    engine = str(cfg_get(cfg, "screen.engine", "chai1"))
    mock = bool(cfg_get(cfg, "run.mock", False))
    dry_run = bool(cfg_get(cfg, "run.dry_run", False))

    ranked_ids, selection_policy = _tier2_selection(cfg, candidates, tier1_metrics, id_column)
    selected_ids = ranked_ids[:tier2_n]
    if not dry_run:
        policy_path = run_dir / "03_score" / "tier2_selection_policy.json"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        selection_policy.update(ordered_ids=ranked_ids, selected_ids=selected_ids, tier2_n=tier2_n)
        policy_path.write_text(json.dumps(selection_policy, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    subset = candidates[candidates[id_column].isin(selected_ids)].copy()

    if (engine.lower() == "chai1" and bool(cfg_get(cfg, "screen.restraint.enabled", True))
            and not mock and not dry_run):
        # Contact restraints can bias the contact metric for any candidate.
        # A restraint-induced contact cannot establish biological binding.
        state.add_warnings(
            "screen.restraint.enabled=true 이므로 hotspot 접촉률이 restraint 로 유도된 값입니다. "
            "decoy 에서도 함께 오르므로 결합자/비결합자 판별 근거로 쓰지 마세요. "
            "판별이 목적이면 restraint 를 끄고 비교하거나(specificity 캠페인 기본) Boltz-2 재평가를 사용하세요."
        )
    print(f"[tier2] {engine} 복합체 예측: {len(subset)}종")
    predictions = screen.predict_complexes(
        cfg, run_dir / "02b_tier2", target, subset, engine=engine, mock=mock, dry_run=dry_run,
        id_column=id_column, sequence_column=sequence_column,
    )
    boltz_top = int(cfg_get(cfg, "screen.boltz2_rescore_top", 0))
    if boltz_top > 0 and engine != "boltz2":
        boltz_subset = _select_boltz_candidates(cfg, candidates, predictions, target, id_column, boltz_top)
        print(f"[tier2] Boltz-2 정밀 재평가(1차 지표 상위 {boltz_top}): {len(boltz_subset)}종")
        boltz_predictions = screen.predict_complexes(
            cfg, run_dir / "02c_boltz2", target, boltz_subset, engine="boltz2", mock=mock, dry_run=dry_run,
            id_column=id_column, sequence_column=sequence_column,
        )
        predictions = pd.concat([predictions, boltz_predictions], ignore_index=True)
        predictions = predictions.drop_duplicates(subset=[id_column], keep="last")

    final_candidates = candidates.drop(columns=[c for c in ("pred_dir", "engine", "target_chain", "binder_chain", "model_index") if c in candidates.columns])
    final_candidates = final_candidates.merge(
        _predictions_columns(predictions, id_column),
        on=id_column,
        how="inner",
    )
    if not dry_run:
        state.set_phase("tier2", {"n": int(len(final_candidates)), "engine": engine, "dir": str(run_dir / "02b_tier2")})

    if dry_run:
        print("[dry-run] 스코어링/리포트를 건너뜁니다 (명령만 생성됨).")
        return {"selected": pd.DataFrame(), "scored": pd.DataFrame(), "report": None, "synthesis": None}

    return _finalize(cfg, run_dir, state, final_candidates, target, cdr_ranges, id_column, sequence_column)


def cmd_prepare(args) -> int:
    cfg = _load(args)
    if cfg_get(cfg, "specificity.enabled", False) or _is_specificity_resume(args):
        # Definition/mapping preview is deliberately side-effect free.
        args.dry_run = True
        return cmd_specificity(args)
    run_dir, state = _open_run(cfg, args)
    target = _target_phase(cfg, run_dir, state, _is_mock(cfg), _is_dry_run(cfg))
    print(f"[prepare] 타깃 구조: {target['structure']}")
    print(f"[prepare] 핫스팟: {', '.join(target['hotspot_labels'])}")
    for warning in state.warnings:
        print(f"  [경고] {warning}")
    return 0


def cmd_design(args) -> int:
    cfg = _load(args)
    if cfg_get(cfg, "specificity.enabled", False) or _is_specificity_resume(args):
        args.mode = "design"
        return cmd_specificity(args)
    run_dir, state = _open_run(cfg, args)
    mock, dry_run = _is_mock(cfg), _is_dry_run(cfg)
    _set_run_mode(cfg, state, "design", dry_run)
    target = _target_phase(cfg, run_dir, state, mock, dry_run)
    result = _run_design_backend(cfg, run_dir, target, mock, dry_run)
    if not dry_run:
        state.set_phase("design", {k: v for k, v in result["info"].items()})
    print(f"[design] 후보 {len(result['candidates'])}종 생성")
    if _is_dry_run(cfg):
        _write_dry_run_notice(run_dir, state)
        return 0
    if _design_refinement_flow(cfg, run_dir, state, target, result):
        return 0
    tier1 = _score_candidates(cfg, result["candidates"], target) if not result["candidates"].empty else pd.DataFrame()
    if not tier1.empty:
        tier1.to_csv(run_dir / "02_design" / "tier1_metrics.csv", index=False)
    _tier2_and_finalize(cfg, run_dir, state, result["candidates"], target, None, "design_id", "sequence", tier1)
    return 0


def _run_design_backend(cfg, run_dir, target, mock, dry_run):
    # Keep the established RF-only path and its return contract intact.
    if cfg_get(cfg, "design.engine", "rfantibody") == "rfantibody" and not cfg_get(cfg, "design.refinement.enabled", False):
        return design_rfantibody.run_design(cfg, run_dir, target, mock=mock, dry_run=dry_run)
    from .cascade import preflight_msa
    preflight_msa(cfg, run_dir, target)
    from .design_routing import run_design
    return run_design(cfg, run_dir, target, mock=mock, dry_run=dry_run)


def _design_refinement_flow(cfg, run_dir, state, target, result):
    from . import design_refinement
    if not design_refinement.enabled(cfg):
        return False
    design_refinement.run(cfg, run_dir, state, target, result,
                          predict=screen.predict_complexes, score=_score_candidates, finalize=_finalize)
    return True


def cmd_screen(args) -> int:
    cfg = _load(args)
    if cfg_get(cfg, "specificity.enabled", False) or _is_specificity_resume(args):
        args.mode = "screen"
        return cmd_specificity(args)
    run_dir, state = _open_run(cfg, args)
    mock, dry_run = _is_mock(cfg), _is_dry_run(cfg)
    _set_run_mode(cfg, state, "screen", dry_run)
    target = _target_phase(cfg, run_dir, state, mock, dry_run)

    vhh_fasta = args.vhh_fasta or cfg_get(cfg, "screen.vhh_fasta")
    if args.vhh_fasta:
        cfg["screen"]["vhh_fasta"] = args.vhh_fasta
        if not dry_run:
            dump_resolved(cfg, run_dir / "run_config.yaml")
    if mock and not vhh_fasta:
        candidates = _mock_vhh_library()
    else:
        fasta_path = resolve_path(cfg, vhh_fasta)
        if fasta_path is None or not fasta_path.exists():
            raise FileNotFoundError("screen 모드에는 --vhh-fasta (후보 나노바디 FASTA)가 필요합니다.")
        candidates = screen.fasta_to_candidates(fasta_path)
    print(f"[screen] 후보 {len(candidates)}종")
    (run_dir / "02_screen").mkdir(parents=True, exist_ok=True)
    if not dry_run:
        candidates.to_csv(run_dir / "02_screen" / "candidates.csv", index=False)
    return _screen_flow(cfg, run_dir, state, target, candidates)


def _screen_flow(cfg, run_dir, state, target, candidates):
    from . import engine_comparison
    if engine_comparison.enabled(cfg):
        engine_comparison.run(cfg, run_dir, state, target, candidates, predict=screen.predict_complexes, finalize=_finalize)
        return 0
    from . import cascade
    if cascade.enabled(cfg):
        cascade.run(cfg, run_dir, state, target, candidates, None,
                    predict=screen.predict_complexes, score=_score_candidates, finalize=_finalize)
        return 0
    mock = bool(cfg_get(cfg, "run.mock", False))
    dry_run = bool(cfg_get(cfg, "run.dry_run", False))
    engine = str(cfg_get(cfg, "screen.engine", "chai1"))
    if engine == "colabfold":
        cfg, candidates = cascade.prepare_msa(cfg, run_dir, state, target, candidates)
    predictions = screen.predict_complexes(
        cfg, run_dir / "02_screen", target, candidates, engine=engine, mock=mock, dry_run=dry_run
    )
    if not dry_run:
        state.set_phase("screen", {"engine": engine, "n": int(len(predictions))})
    if dry_run:
        print("[dry-run] 스코어링/리포트를 건너뜁니다.")
        return 0
    merged_candidates = candidates.merge(
        predictions[["design_id", "pred_dir", "engine", "target_chain", "binder_chain"]], on="design_id", how="inner"
    )
    boltz_top = int(cfg_get(cfg, "screen.boltz2_rescore_top", 0))
    if boltz_top > 0 and engine != "boltz2":
        boltz_subset = _select_boltz_candidates(cfg, candidates, predictions, target, "design_id", boltz_top)
        print(f"[screen] Boltz-2 정밀 재평가(1차 지표 상위 {boltz_top}): {len(boltz_subset)}종")
        boltz_predictions = screen.predict_complexes(
            cfg, run_dir / "02c_boltz2", target, boltz_subset, engine="boltz2", mock=mock, dry_run=dry_run
        )
        merged_candidates = merged_candidates.merge(
            boltz_predictions[["design_id", "pred_dir", "engine", "target_chain", "binder_chain"]],
            on="design_id", how="left", suffixes=("", "_boltz"),
        )
        has_boltz = merged_candidates["pred_dir_boltz"].notna()
        merged_candidates.loc[has_boltz, "pred_dir"] = merged_candidates.loc[has_boltz, "pred_dir_boltz"]
        merged_candidates.loc[has_boltz, "engine"] = "boltz2"
        merged_candidates = merged_candidates.drop(columns=[c for c in merged_candidates.columns if c.endswith("_boltz")])
    _finalize(cfg, run_dir, state, merged_candidates, target, None)
    return 0


def cmd_mature(args) -> int:
    cfg = _load(args)
    if cfg_get(cfg, "specificity.enabled", False) or _is_specificity_resume(args):
        args.mode = "mature"
        return cmd_specificity(args)
    run_dir, state = _open_run(cfg, args)
    mock, dry_run = _is_mock(cfg), _is_dry_run(cfg)
    _set_run_mode(cfg, state, "mature", dry_run)
    target = _target_phase(cfg, run_dir, state, mock, dry_run)

    if args.wt_fasta:
        cfg["mature"]["wt_fasta"] = args.wt_fasta
        cfg["mature"]["wt_sequence"] = ""
        if not dry_run:
            dump_resolved(cfg, run_dir / "run_config.yaml")
    wt_sequence = mature.resolve_wt_sequence(cfg)
    cdr_ranges, warnings = mature.resolve_cdr_ranges(
        cfg, wt_sequence, mock=mock, dry_run=dry_run, log_path=run_dir / "02_mature" / "anarci.log"
    )
    if not dry_run:
        state.add_warnings(warnings)
    print(f"[mature] CDR 범위: {cdr_ranges}")

    library = mature.generate_library(wt_sequence, cdr_ranges, cfg)
    phase_dir = run_dir / "02_mature"
    from . import cascade
    prefiltered, info = mature.run_prefilter(library, wt_sequence, cascade.mature_prefilter_config(cfg), phase_dir, mock=mock, dry_run=dry_run)
    print(f"[mature] 라이브러리 {len(library)}종 → 프리필터 {info.get('selected', 0)}종 (ΔLLR ≥ {info.get('threshold')})")
    if not dry_run:
        state.set_phase("mature", {
            "wt_sequence": wt_sequence,
            "cdr_ranges": {k: list(v) for k, v in cdr_ranges.items()},
            "library_size": int(len(library)),
            **info,
        })
    if dry_run:
        print("[dry-run] 종료.")
        return 0
    candidates = prefiltered.rename(columns={"variant_id": "design_id"}).copy()
    candidates["source"] = cfg_get(cfg, "mature.library.strategy", "dms")
    if not bool(cfg_get(cfg, "rank.include_wt_control", False)) and not cascade.enabled(cfg):
        candidates = candidates[candidates["design_id"] != "WT"].copy()
    return _mature_flow(cfg, run_dir, state, target, candidates, cdr_ranges)


def _mature_flow(cfg, run_dir, state, target, candidates, cdr_ranges):
    from . import engine_comparison
    if engine_comparison.enabled(cfg):
        engine_comparison.run(cfg, run_dir, state, target, candidates, cdr_ranges,
                              predict=screen.predict_complexes, finalize=_finalize)
        return 0
    from . import cascade
    if cascade.enabled(cfg):
        cascade.run(cfg, run_dir, state, target, candidates, cdr_ranges,
                    predict=screen.predict_complexes, score=_score_candidates, finalize=_finalize)
        return 0
    mock = bool(cfg_get(cfg, "run.mock", False))
    engine = str(cfg_get(cfg, "screen.engine", "chai1"))
    if engine == "colabfold":
        cfg, candidates = cascade.prepare_msa(cfg, run_dir, state, target, candidates, cdr_ranges)
    predictions = screen.predict_complexes(
        cfg, run_dir / "02b_mature_complex", target, candidates, engine=engine, mock=mock, dry_run=False,
        id_column="design_id", sequence_column="sequence",
    )
    merged = candidates.merge(
        predictions[["design_id", "pred_dir", "engine", "target_chain", "binder_chain"]], on="design_id", how="inner"
    )
    _finalize(cfg, run_dir, state, merged, target, cdr_ranges)
    return 0


def cmd_score(args) -> int:
    if _is_specificity_resume(args):
        from . import campaign
        root = Path(args.run_dir)
        cfg = load_config(root / "run_config.yaml", args.set)
        if args.config:
            raise ValueError("특이성 재분석은 저장 설정과 --set 만 사용합니다. 타깃 변경은 새 캠페인에서 수행하세요.")
        if args.dry_run:
            print("[dry-run] 특이성 재분석 예정 (파일 변경 없음)")
            return 0
        result = campaign.reanalyze(cfg, root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "complete" else 2
    cfg = _load(args)
    run_dir, state = _open_run(cfg, args, new=False)
    if _is_dry_run(cfg):
        _write_dry_run_notice(run_dir, state)
        return 0
    target = state.get_phase("target")
    if not target:
        raise RuntimeError("이 실행에 target 단계가 없습니다. 먼저 prepare/run 을 수행하세요.")
    from . import cascade
    if (run_dir / "03_score" / "cascade_candidates.csv").exists():
        mature_state = state.get_phase("mature")
        cdr_ranges = {k: tuple(v) for k, v in mature_state.get("cdr_ranges", {}).items()} or None
        cascade.reanalyze(cfg, run_dir, state, target, cdr_ranges,
                          score=_score_candidates, finalize=_finalize)
        return 0
    comparison_table = run_dir / "02_comparison/comparison_candidates.csv"
    if cfg_get(cfg, "screen.comparison.enabled", False) and comparison_table.exists():
        candidates = pd.read_csv(comparison_table, dtype={"design_id": str, "source_sequence_id": str})
        mature_state = state.get_phase("mature")
        cdr_ranges = {k: tuple(v) for k, v in mature_state.get("cdr_ranges", {}).items()} or None
        _finalize(cfg, run_dir, state, candidates, target, cdr_ranges)
        return 0
    candidates = _load_candidates(cfg, run_dir, state)
    if candidates is None or candidates.empty:
        raise RuntimeError("스코어링할 후보 목록을 찾지 못했습니다.")
    cdr_ranges = None
    mature_state = state.get_phase("mature")
    if mature_state.get("cdr_ranges"):
        cdr_ranges = {k: tuple(v) for k, v in mature_state["cdr_ranges"].items()}
    _run_from_state(cfg, run_dir, state, target, candidates, cdr_ranges)
    return 0


def _load_candidates(cfg, run_dir: Path, state: RunState) -> pd.DataFrame | None:
    predictions = _load_prediction_manifests(run_dir)
    design_state = state.get_phase("design")
    if design_state and (run_dir / "02_design" / "design_candidates.csv").exists():
        frame = pd.read_csv(run_dir / "02_design" / "design_candidates.csv", dtype={"design_id": str})
        if not predictions.empty:
            merged = frame.merge(
                predictions[["design_id", "pred_dir", "engine", "target_chain", "binder_chain"]],
                on="design_id", how="inner",
            )
            if not merged.empty:
                return _resolve_prediction_paths(_normalize_prediction_columns(merged), run_dir)
        return _resolve_prediction_paths(_normalize_prediction_columns(frame), run_dir)
    for path in (run_dir / "02_screen" / "candidates.csv", run_dir / "02_mature" / "prefiltered_candidates.csv"):
        if path.exists():
            frame = pd.read_csv(path, dtype={"design_id": str, "variant_id": str})
            id_column = "design_id" if "design_id" in frame.columns else "variant_id"
            frame = frame.rename(columns={id_column: "design_id"})
            if not predictions.empty:
                frame = frame.merge(
                    predictions[["design_id", "pred_dir", "engine", "target_chain", "binder_chain"]],
                    on="design_id", how="inner",
                )
            return _resolve_prediction_paths(_normalize_prediction_columns(frame), run_dir)
    return None


def _normalize_prediction_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """merge 로 생긴 pred_dir_x/_y, target_chain_x/_y 등을 authoritative 이름으로 정규화한다(N04)."""
    frame = frame.copy()
    for base in ("pred_dir", "engine", "target_chain", "binder_chain", "aux_chains"):
        if base not in frame.columns:
            for suffix in ("_y", "_x", "_boltz"):
                candidate = f"{base}{suffix}"
                if candidate in frame.columns:
                    frame[base] = frame[candidate]
                    break
        frame = frame.drop(columns=[base + suffix for suffix in ("_y", "_x", "_boltz")
                                   if base + suffix in frame.columns])
    return frame


def _resolve_prediction_paths(frame: pd.DataFrame, run_dir: Path) -> pd.DataFrame:
    frame = frame.copy()
    if "pred_dir" not in frame.columns:
        return frame

    def resolve(value):
        if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value):
            return value
        path = resolve_run_artifact(run_dir, value)
        return str(path) if path is not None else value

    frame["pred_dir"] = frame["pred_dir"].map(resolve)
    return frame


def _load_prediction_manifests(run_dir: Path) -> pd.DataFrame:
    """모든 predictions.csv 를 읽어 후보 ID별로 중복 제거한다 (boltz2 우선)."""
    frames: list[pd.DataFrame] = []
    from .utils.predictions import current_manifests
    for manifest in sorted(current_manifests(run_dir)):
        try:
            frame = pd.read_csv(manifest, dtype={"design_id": str})
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            import warnings
            warnings.warn(f"예측 목록을 읽지 못했습니다: {manifest}: {exc}", RuntimeWarning)
            continue
        if "pred_dir" in frame.columns:
            frame = frame[frame["pred_dir"].notna()]
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    priority = {"boltz2": 0, "chai1": 1, "rfantibody": 2, "mock": 3}
    combined["_priority"] = combined.get("engine", pd.Series(index=combined.index)).map(priority).fillna(9)
    combined = combined.sort_values("_priority").drop_duplicates(subset=["design_id"], keep="first")
    combined = combined.drop(columns=["_priority"])
    return _resolve_prediction_paths(combined, run_dir)


def _resolve_prediction_dir(candidate_dir: Path) -> Path:
    """후보 디렉터리에서 실제 예측 출력 위치를 찾는다 (chai_out/boltz_out 우선)."""
    chai_out = candidate_dir / "chai_out"
    if (chai_out / "pred.model_idx_0.cif").exists():
        return chai_out
    boltz_out = candidate_dir / "boltz_out"
    if boltz_out.exists():
        try:
            nested = target_prep.find_boltz_prediction_dir(boltz_out)
            if nested is not None:
                return nested
        except Exception:  # noqa: BLE001
            pass
    return candidate_dir


def _run_from_state(cfg, run_dir, state, target, candidates, cdr_ranges):
    if candidates is None or candidates.empty:
        raise RuntimeError("후보가 없습니다.")
    candidates = _normalize_prediction_columns(candidates)
    if "pred_dir" not in candidates.columns or candidates["pred_dir"].isna().all():
        raise RuntimeError(
            "예측 경로(pred_dir)를 후보에서 찾지 못했습니다. prediction manifest(predictions.csv)와 "
            "run_config를 확인하세요. 기존 예측 재사용이 불가능하면 run 을 다시 실행하세요 (N04)."
        )
    return _finalize(cfg, run_dir, state, candidates, target, cdr_ranges)


def cmd_report(args) -> int:
    if _is_specificity_resume(args):
        return cmd_score(args)
    cfg = _load(args)
    run_dir, state = _open_run(cfg, args, new=False)
    saved_config = run_dir / "run_config.yaml"
    if saved_config.is_file():
        from .config import read_config_yaml
        saved_cfg = read_config_yaml(saved_config.read_text(encoding="utf-8"))
        if not isinstance(saved_cfg, dict) or not saved_cfg:
            raise ValueError("계산 당시 run_config.yaml이 손상되었습니다. 원본 설정을 복구하세요.")
        from .config import _deep_merge, _set_dotted
        report_options = copy.deepcopy(saved_cfg.get("report", cfg.get("report", {})))
        if getattr(args, "config", None):
            requested_config = read_config_yaml(Path(args.config).read_text(encoding="utf-8")) or {}
            report_options = _deep_merge(report_options, requested_config.get("report", {}))
        ignored = []
        for override in getattr(args, "set", []) or []:
            key, raw = override.split("=", 1)
            if key.strip().startswith("report."):
                options = {"report": report_options}
                _set_dotted(options, key.strip(), read_config_yaml(raw.strip()))
                report_options = options["report"]
            else:
                ignored.append(key.strip())
        if ignored:
            print("[안내] 보고서 재생성은 계산 당시 설정을 유지합니다. 적용하지 않은 --set 항목: " + ", ".join(ignored), file=sys.stderr)
        cfg.clear()
        cfg.update(saved_cfg)
        cfg["report"] = report_options
    execution = state.data.get("report_source_execution")
    if execution is None:
        current = state.data.get("execution", {})
        # The outer report command replaces execution with its own running
        # status. Direct report regeneration instead reads the completed
        # computation, even when a previous running checkpoint remains.
        execution = (state.data.get("previous_execution", {})
                     if current.get("command") == "report" and current.get("state") == "running"
                     else current)
    if execution.get("state") in {"cancelled", "interrupted", "budget_exhausted", "running"}:
        raise ValueError("중단된 실행은 report로 완료 처리할 수 없습니다. 계산을 재개하거나 별도 읽기 전용 보고서를 내보내세요.")
    if cfg_get(cfg, "run.mode") == "complex" and (run_dir / "complex_results.json").is_file():
        prediction = state.get_phase("prediction")
        if ("final" not in state.data.get("phases", {}) or not prediction
                or prediction.get("completed_models") != prediction.get("total_models")):
            raise ValueError("완료되지 않은 복합체 실행입니다. report는 부분 예측을 완료로 바꾸지 않습니다.")
        # The common command finalizer publishes the report-only archive after
        # saving execution state. Complex models do not use VHH score CSVs.
        print("[report] 일반 복합체 전달용 보고서를 준비합니다.")
        return 0
    target = state.get_phase("target")
    selected_path = run_dir / "05_final" / "selected_candidates.csv"
    scored_path = run_dir / "03_score" / "scored_candidates.csv"
    if not selected_path.exists() and not scored_path.exists():
        raise RuntimeError("리포트에 사용할 스코어 결과가 없습니다. 먼저 score/run 을 수행하세요.")
    identifiers = {"design_id": str, "variant_id": str, "source_sequence_id": str, "case_id": str}
    selected = pd.read_csv(selected_path, dtype=identifiers) if selected_path.exists() else pd.DataFrame()
    scored = pd.read_csv(scored_path, dtype=identifiers) if scored_path.exists() else pd.DataFrame()
    final = state.get_phase("final")
    from .publication import tables_available
    if ("tables_committed" in final or "table_sha256" in final) and not tables_available(run_dir, final):
        raise ValueError("최신 계산 표가 확정되지 않았거나 해시가 다릅니다. 이전 표로 보고서를 재발행하지 않았습니다.")
    if final.get("exports_status") in {"pending", "failed"}:
        from .config import read_config_yaml
        saved_path = run_dir / "run_config.yaml"
        if not saved_path.is_file():
            raise ValueError("내보내기 복구에는 계산 당시 run_config.yaml이 필요합니다. 보고서 설정으로 게이트 기준을 추정하지 않습니다.")
        export_cfg = read_config_yaml(saved_path.read_text(encoding="utf-8"))
        if not isinstance(export_cfg, dict) or not export_cfg:
            raise ValueError("계산 당시 run_config.yaml이 손상되었습니다. 원본 설정을 복구한 뒤 내보내기를 다시 생성하세요.")
        # Renderer/PyMOL options may be changed to recover an export, while all
        # scientific, ranking, synthesis and handoff settings stay recorded.
        export_cfg["report"] = cfg.get("report", {})
        exports = _final_exports(export_cfg, run_dir, state, scored, selected)
        final.update(exports_status="complete", synthesis=exports["synthesis"], handoff_gate=exports["handoff_gate"])
        final.pop("exports_error", None)
        state.set_phase("final", final)
    gate_path = run_dir / "05_final/handoff_gate.json"
    gate = (None if final.get("empty_final_pool") else
            json.loads(gate_path.read_text()) if gate_path.is_file() else final.get("handoff_gate"))
    context = {
        "run_id": run_dir.name,
        "mode": cfg_get(cfg, "run.mode"),
        "num_candidates": int(len(scored)),
        "num_clusters": int(selected["cluster_id"].nunique()) if "cluster_id" in selected.columns and not selected.empty else 0,
        "hotspots": target.get("hotspot_labels", []),
        "target_qc": target.get("qc", {}),
        "warnings": state.warnings,
        "handoff_gate": gate,
        "synthesis": final.get("synthesis"),
    }
    policy_path = run_dir / "03_score" / "ranking_policy.json"
    failed_policy = final.get("ranking_policy_status") in {"pending", "failed"}
    if not policy_path.exists() or failed_policy:
        # A report command does not rerank. Current defaults cannot recover a
        # missing historical normalization/selection policy from old CSVs.
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        if failed_policy and policy_path.exists():
            provenance.archive_existing([policy_path], policy_path.parent)
        write_json(policy_path, {
            "schema_version": 1, "method": "historical_policy_not_recorded",
            "recorded_by": "report_regeneration_without_reranking",
            "scored_values_and_selected_rows_preserved": True,
            "cross_engine_scores_combined": None,
            "historical_normalization_scope": "unknown",
            "calibrated_binding_probability": False,
        }, root=run_dir)
        state.add_warnings("적용 순위 정책은 미기록입니다. 현재 기본값으로 과거 정책을 추정하거나 "
                           "점수·선별을 다시 계산하지 않았습니다. 이전 정책 파일이 있으면 history에 보존합니다.")
        final["ranking_policy_status"] = "not_recorded"
        state.set_phase("final", final)
        context["warnings"] = state.warnings
    final_record = dict(state.get_phase("final"))
    final_record.update(report_status="pending", report=None)
    final_record.pop("report_error", None)
    state.set_phase("final", final_record)
    try:
        path = report.generate_report(run_dir, cfg, context, scored=scored, selected=selected)
    except Exception as exc:
        final_record.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
        state.set_phase("final", final_record)
        raise
    final_record.update(report_status="complete", report=str(path))
    state.set_phase("final", final_record)
    print(f"[report] {path}")
    from .publication import gate_summary
    normalized_gate = gate_summary(gate)
    if (cfg_get(cfg, "handoff.require_gate", False) and not _is_mock(cfg)
            and (not normalized_gate or normalized_gate["ok"] is not True)):
        return 3
    return 0


def cmd_complex(args):
    args.mode = "complex"
    return cmd_run(args)


def cmd_run(args) -> int:
    start = __import__("time").time()
    cfg = _load(args)
    if cfg_get(cfg, "specificity.enabled", False) or _is_specificity_resume(args):
        return cmd_specificity(args)
    run_dir, state = _open_run(cfg, args)
    mode = cfg_get(cfg, "run.mode")
    mock, dry_run = _is_mock(cfg), _is_dry_run(cfg)
    _set_run_mode(cfg, state, mode, dry_run)
    print(f"=== NanoCoFold 실행: {run_dir.name} (mode={mode}, mock={mock}, dry_run={dry_run}) ===")

    if mode == "complex":
        from .complex import run
        return run(cfg, run_dir, state)

    target = _target_phase(cfg, run_dir, state, mock, dry_run)

    if mode == "design":
        result = _run_design_backend(cfg, run_dir, target, mock, dry_run)
        if not dry_run:
            state.set_phase("design", result["info"])
        candidates = result["candidates"]
        print(f"[design] 후보 {len(candidates)}종")
        if dry_run:
            _write_dry_run_notice(run_dir, state)
            return 0
        if not _design_refinement_flow(cfg, run_dir, state, target, result):
            tier1 = _score_candidates(cfg, candidates, target) if not candidates.empty else pd.DataFrame()
            if not tier1.empty:
                tier1.to_csv(run_dir / "02_design" / "tier1_metrics.csv", index=False)
            _tier2_and_finalize(cfg, run_dir, state, candidates, target, None, "design_id", "sequence", tier1)

    elif mode == "screen":
        vhh_fasta = getattr(args, "vhh_fasta", None) or cfg_get(cfg, "screen.vhh_fasta")
        if mock and not vhh_fasta:
            candidates = _mock_vhh_library()
            (run_dir / "02_screen").mkdir(parents=True, exist_ok=True)
            if not dry_run:
                candidates.to_csv(run_dir / "02_screen" / "candidates.csv", index=False)
        else:
            fasta_path = resolve_path(cfg, vhh_fasta)
            if fasta_path is None or not fasta_path.exists():
                raise FileNotFoundError("screen 모드에는 --vhh-fasta 가 필요합니다.")
            cfg["screen"]["vhh_fasta"] = str(vhh_fasta)
            if not dry_run:
                dump_resolved(cfg, run_dir / "run_config.yaml")
            candidates = screen.fasta_to_candidates(fasta_path)
            (run_dir / "02_screen").mkdir(parents=True, exist_ok=True)
            if not dry_run:
                candidates.to_csv(run_dir / "02_screen" / "candidates.csv", index=False)
        _screen_flow(cfg, run_dir, state, target, candidates)

    elif mode == "mature":
        wt_fasta = getattr(args, "wt_fasta", None)
        if wt_fasta:
            cfg["mature"]["wt_fasta"] = wt_fasta
            cfg["mature"]["wt_sequence"] = ""
            if not dry_run:
                dump_resolved(cfg, run_dir / "run_config.yaml")
        wt_sequence = mature.resolve_wt_sequence(cfg)
        cdr_ranges, warnings = mature.resolve_cdr_ranges(
            cfg, wt_sequence, mock=mock, dry_run=dry_run, log_path=run_dir / "02_mature" / "anarci.log"
        )
        if not dry_run:
            state.add_warnings(warnings)
        library = mature.generate_library(wt_sequence, cdr_ranges, cfg)
        from . import cascade
        prefiltered, info = mature.run_prefilter(library, wt_sequence, cascade.mature_prefilter_config(cfg), run_dir / "02_mature", mock=mock, dry_run=dry_run)
        if not dry_run:
            state.set_phase("mature", {
                "wt_sequence": wt_sequence,
                "cdr_ranges": {k: list(v) for k, v in cdr_ranges.items()},
                "library_size": int(len(library)),
                **info,
            })
        print(f"[mature] 라이브러리 {len(library)} → 후보 {len(prefiltered)}")
        if dry_run:
            _write_dry_run_notice(run_dir, state)
            return 0
        candidates = prefiltered.rename(columns={"variant_id": "design_id"}).copy()
        candidates["source"] = cfg_get(cfg, "mature.library.strategy", "dms")
        if not bool(cfg_get(cfg, "rank.include_wt_control", False)) and not cascade.enabled(cfg):
            candidates = candidates[candidates["design_id"] != "WT"].copy()
        _mature_flow(cfg, run_dir, state, target, candidates, cdr_ranges)

    elapsed = format_seconds(__import__("time").time() - start)
    print(f"=== 완료 ({elapsed}) · 리포트: {run_dir / 'report.html'} ===")
    for warning in state.warnings:
        print(f"  [경고] {warning}")
    return 0


def _mock_vhh_library(n: int = 8) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(11)
    rows = []
    for index in range(n):
        sequence = design_rfantibody.mock_mutate_sequence(
            design_rfantibody.FALLBACK_FRAMEWORK_SEQ, rng, 2 + index % 5
        )
        rows.append({"design_id": f"lib_{index:03d}", "sequence": sequence, "source": "mock"})
    return pd.DataFrame(rows)


def _write_dry_run_notice(run_dir: Path, state: RunState) -> None:
    print(f"[dry-run] 생성된 명령/로그를 확인하세요: {run_dir}")


def _is_specificity_resume(args) -> bool:
    directory = getattr(args, "run_dir", None)
    if not directory:
        return False
    saved = Path(directory) / "run_config.yaml"
    return saved.exists() and bool(cfg_get(read_config_yaml(saved.read_text()), "specificity.enabled", False))


def cmd_specificity(args) -> int:
    from . import campaign
    from .config import _deep_merge, _set_dotted, _validate
    import yaml

    cfg = _load(args)
    directory = getattr(args, "run_dir", None)
    if directory:
        saved = Path(directory) / "run_config.yaml"
        if not saved.is_file():
            raise FileNotFoundError(f"재개할 run_config.yaml 이 없습니다: {saved}")
        cfg = load_config(saved)
        if getattr(args, "config", None):
            cfg = _deep_merge(cfg, read_config_yaml(Path(args.config).read_text()) or {})
        for value in getattr(args, "set", []):
            key, raw = value.split("=", 1)
            _set_dotted(cfg, key, yaml.safe_load(raw))
    cfg.setdefault("specificity", {})["enabled"] = True
    for flag in ("mock", "dry_run"):
        if getattr(args, flag, False):
            cfg["run"][flag] = True
    if getattr(args, "mode", None):
        cfg["run"]["mode"] = args.mode
    for argument, section, key in (("vhh_fasta", "screen", "vhh_fasta"), ("wt_fasta", "mature", "wt_fasta")):
        if getattr(args, argument, None):
            cfg[section][key] = getattr(args, argument)
            if section == "mature":
                cfg[section]["wt_sequence"] = ""
    _validate(cfg)
    root = Path(directory).resolve() if directory else resolve_run_dir(cfg, run_id=getattr(args, "run_id", None))
    result = campaign.run_campaign(cfg, root, dry_run=_is_dry_run(cfg))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] not in {"complete", "dry_run"}:
        return 2
    if (
        bool(cfg_get(cfg, "handoff.require_gate", False))
        and not result.get("handoff_ready", True)
        and not _is_mock(cfg)
    ):
        # mock 결과는 어차피 발주할 수 없으므로 run/design/screen/mature 경로와 동일하게 제외한다.
        return 3
    return 0


def cmd_doctor(args=None) -> int:
    if args is not None and (args.config or args.json or args.smoke or args.set):
        from . import diagnostics
        result = diagnostics.doctor(load_config(args.config, args.set), smoke=args.smoke)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    print("=== NanoCoFold 환경 점검 ===")
    print(f"프로젝트 루트: {PROJECT_ROOT}")
    gpu_ok = shell.has_executable("nvidia-smi")
    print(f"GPU: {'있음' if gpu_ok else '없음 (CPU 전용: mock/스코어링만 가능)'}")
    tools = [
        ("RFantibody", PROJECT_ROOT / "envs" / "rfantibody" / "bin" / "rfdiffusion"),
        ("Boltz-2", PROJECT_ROOT / "envs" / "boltz2" / "bin" / "boltz"),
        ("Chai-1", PROJECT_ROOT / "envs" / "chai1" / "bin" / "chai-lab"),
        ("ESM/AntiFold", PROJECT_ROOT / "envs" / "esm" / "bin" / "antifold"),
        ("ANARCI(NanoBodyBuilder)", PROJECT_ROOT / "envs" / "antibody" / "bin" / "python"),
    ]
    for name, path in tools:
        print(f"  {name}: {'OK' if Path(path).exists() else '미설치 (envs/setup_*.sh)'}")
    framework = PROJECT_ROOT / "data" / "frameworks" / "h-NbBCII10.pdb"
    print(f"  VHH 프레임워크: {'OK' if framework.exists() else '없음'}")
    manifest = PROJECT_ROOT / "LICENSE_MANIFEST.md"
    print(f"  라이선스 매니페스트: {'OK' if manifest.exists() else '없음'}")
    if gpu_ok:
        result = shell.run(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader"], quiet=True)
        if result.log_path:
            print("  " + result.log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    return 0


def cmd_license(args=None) -> int:
    from .config import ASSET_ROOT

    manifest = ASSET_ROOT / "LICENSE_MANIFEST.md"
    if not manifest.exists():
        manifest = PROJECT_ROOT / "LICENSE_MANIFEST.md"
    if not manifest.exists():
        print("LICENSE_MANIFEST.md 가 없습니다.")
        return 1
    print(manifest.read_text(encoding="utf-8"))
    if args is not None and getattr(args, "audit", False):
        return _audit_licenses()
    return 0


def _audit_licenses() -> int:
    """각 env의 설치 패키지 라이선스를 pip-licenses(MIT)로 수집해 CSV로 저장."""
    import csv
    import io

    envs = sorted(path for path in (PROJECT_ROOT / "envs").glob("*/bin/python") if path.exists())
    if not envs:
        print("[audit] 설치된 env가 없습니다. envs/setup_*.sh 를 먼저 실행하세요.")
        return 0
    rows: list[dict] = []
    missing: list[str] = []
    import tempfile

    for python in envs:
        env_name = python.parent.parent.name
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / f"{env_name}_licenses.log"
            result = shell.run(
                [str(python), "-m", "piplicenses", "--format=csv", "--with-urls"],
                check=False, quiet=True, log_path=log_path,
            )
            if result.returncode != 0 or not log_path.exists():
                missing.append(env_name)
                continue
            body = "\n".join(log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[2:])
        if not body.strip():
            missing.append(env_name)
            continue
        for row in csv.DictReader(io.StringIO(body)):
            row["env"] = env_name
            rows.append(row)
    if not rows:
        print("[audit] 수집된 라이선스 정보가 없습니다. 설치 방법:")
        print("  uv pip install --python envs/<env>/bin/python piplicenses")
        return 0
    out_path = PROJECT_ROOT / "license_audit.csv"
    fieldnames = ["env"] + [key for key in rows[0] if key != "env"]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("License", "UNKNOWN")] = counts.get(row.get("License", "UNKNOWN"), 0) + 1
    print(f"[audit] {len(rows)}개 패키지 → {out_path}")
    for license_name, count in sorted(counts.items(), key=lambda item: -item[1]):
        print(f"  {license_name}: {count}")
    if missing:
        print(f"  [건너뜀] piplicenses 미설치 env: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
