"""Checkout bootstrap for one real, explicitly authorized API-MSA comparison.

This entry point uses only the standard library until the pinned core install
exists. It never selects an old run by glob and never edits an existing config.
"""
from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import zipfile
import zlib

REPO = Path(__file__).resolve().parents[2]
# quickstart runs this file before the core environment is installed.
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO))
from nanocofold.console_progress import ConsoleProgress, command_label
AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
GPU_PATTERN = re.compile(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return stream_digest(stream)[0]


def stream_digest(stream) -> tuple[str, int]:
    checksum, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        checksum.update(chunk)
        size += len(chunk)
    return checksum.hexdigest(), size


def validate_delivery(path: Path) -> None:
    """Read every packaged byte without extracting files or loading a large ZIP."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("중복 파일 이름")
            for name in names:
                member = PurePosixPath(name)
                if (member.is_absolute() or ".." in member.parts or str(member) != name
                        or "\\" in name or ":" in name or any(ord(c) < 32 for c in name)):
                    raise ValueError("유효하지 않은 파일 이름")
            if archive.getinfo("delivery_manifest.json").file_size > 16 * 1024 * 1024:
                raise ValueError("manifest 크기 초과")
            manifest = json.loads(archive.read("delivery_manifest.json"))
            if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                    or manifest.get("kind") != "presentation_only"
                    or not isinstance(manifest.get("receipt"), dict)
                    or manifest["receipt"].get("offline") is not True
                    or not isinstance(manifest.get("files"), list)):
                raise ValueError("오프라인 전달 manifest 형식")
            listed = set()
            for entry in manifest["files"]:
                if not isinstance(entry, dict):
                    raise ValueError("manifest 파일 기록 형식")
                name = entry.get("path")
                if not isinstance(name, str) or name in listed or name == "delivery_manifest.json":
                    raise ValueError("manifest 파일 목록 중복/형식")
                with archive.open(name) as stream:
                    checksum, size = stream_digest(stream)
                if checksum != entry.get("sha256") or size != entry.get("size"):
                    raise ValueError(f"파일 해시/크기 불일치: {name}")
                listed.add(name)
            if listed != set(names) - {"delivery_manifest.json"} or "report.html" not in listed:
                raise ValueError("manifest 파일 목록 불일치 또는 report.html 누락")
            if archive.getinfo("report.html").file_size == 0:
                raise ValueError("빈 report.html")
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, EOFError, zlib.error) as exc:
        raise ValueError(f"전달 ZIP 검증 실패: {exc}. 계산 파일은 보존했습니다. "
                         "nanocofold report로 보고서만 복구하세요.") from exc


def save_json(path: Path, value: dict) -> None:
    from nanocofold.utils.atomic import write_json
    write_json(path, value)


def read_target(path: Path) -> tuple[str, str, str]:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("표적은 1 MiB 이하의 단일 단백질 FASTA 파일이어야 합니다.")
    raw = path.read_bytes()
    header, chunks = None, []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None or not line[1:].strip():
                raise ValueError("표적 FASTA에는 비어 있지 않은 헤더와 서열 하나만 필요합니다.")
            header = line[1:].split()[0]
        elif header is None:
            raise ValueError("FASTA 헤더(>)가 필요합니다. FASTQ 또는 read 파일은 입력할 수 없습니다.")
        else:
            chunks.append("".join(line.split()).upper())
    sequence = "".join(chunks)
    if header is None or not sequence or set(sequence) - AA:
        raise ValueError("표준 20개 아미노산으로 이루어진 단백질 서열 하나가 필요합니다.")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", header).strip(".-") or "target"
    return name[:100], sequence, digest(raw)


@contextmanager
def lock(path: Path):
    """Persistent inode, shared with the platform GPU reservation convention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError(f"잠금 파일 소유자/형식이 올바르지 않습니다: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"다른 실행이 사용 중입니다: {path}. 중복 실행하지 않았습니다.") from exc
        yield descriptor
    finally:
        # Closing releases this reference; live engine children retain theirs.
        # Never unlink: doing so would allow a different inode to be reserved.
        os.close(descriptor)


def check_host(gpu: str, skip_install: bool) -> str:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("실제 모델 설치·실행은 Linux x86_64 NVIDIA 호스트에서 지원합니다.")
    if not GPU_PATTERN.fullmatch(gpu):
        raise ValueError("--gpu에는 nvidia-smi에 표시된 전체 GPU UUID를 지정하세요.")
    required = ["nvidia-smi", "ps"]
    if not skip_install:
        required += ["uv", "git", "curl", "gcc", "g++", "make", "timeout", "sha256sum",
                     "tar", "gzip", "awk", "grep", "head", "mktemp", "nproc"]
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise ValueError("호스트 필수 도구가 없습니다: " + ", ".join(missing) +
                         ". README의 '새 Ubuntu 준비' 명령을 먼저 실행하세요.")
    libc, version = platform.libc_ver()
    if libc != "glibc" or not re.fullmatch(r"\d+(?:\.\d+)+", version) or tuple(map(int, version.split(".")[:2])) < (2, 34):
        raise ValueError(f"glibc 2.34 이상이 필요합니다 (현재: {libc} {version}). Ubuntu 22.04/24.04 LTS를 사용하세요.")
    for library in ("libstdc++.so.6", "libgomp.so.1"):
        try:
            ctypes.CDLL(library)
        except OSError as exc:
            raise ValueError(f"시스템 라이브러리가 없습니다: {library}. sudo apt-get install libstdc++6 libgomp1") from exc
    result = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,driver_version", "--format=csv,noheader"],
                            capture_output=True, text=True, check=True, timeout=20)
    rows = [tuple(part.strip() for part in row.split(",")) for row in result.stdout.splitlines() if row.strip()]
    if any(len(row) != 3 for row in rows):
        raise ValueError("nvidia-smi 응답 형식을 확인하지 못했습니다. 분석을 시작하지 않았습니다.")
    matches = [row for row in rows if row[1].lower() == gpu.lower()]
    if len(matches) != 1:
        raise ValueError("선택한 GPU UUID를 이 호스트에서 확인하지 못했습니다.")
    index, canonical, driver = matches[0]
    if not re.fullmatch(r"\d+(?:\.\d+)+", driver) or int(driver.split(".")[0]) < 580:
        raise ValueError(f"현재 CUDA 13 환경에는 NVIDIA 드라이버 580 이상이 필요합니다 (선택 GPU: {driver}). "
                         "드라이버를 갱신하고 재부팅한 뒤 다시 점검하세요. 이 스크립트는 드라이버를 변경하지 않습니다.")
    if "NANOCOFOLD_ALLOWED_GPUS" in os.environ:
        permitted = [item.strip() for item in os.environ["NANOCOFOLD_ALLOWED_GPUS"].split(",")]
        if not all(permitted) or not any(item.lower() == canonical.lower() or item == index for item in permitted):
            raise ValueError("선택한 GPU는 이 호스트의 허용 GPU 범위에 없습니다.")
    print(f"[호스트 확인] Linux x86_64 · glibc {version} · 드라이버 {driver} · {canonical}", flush=True)
    return canonical


class CommandFailed(subprocess.CalledProcessError):
    def __init__(self, code, argv, label, log):
        super().__init__(code, argv)
        self.label, self.log = label, log

    def __str__(self):
        return f"{self.label} 실패 (종료 코드 {self.returncode}). 위 오류와 로그를 확인하세요: {self.log}"


def run_command(argv: list[str], *, workspace: Path, environment: dict,
                descriptor: int, log: Path) -> None:
    label = command_label(argv)
    # Child Python and the first installer must flush before their command exits.
    environment = {**environment, "PYTHONUNBUFFERED": "1", "NANOCOFOLD_PROGRESS_PARENT": "1"}
    with log.open("a", encoding="utf-8") as stream:
        stream.write("\n" + datetime.now(timezone.utc).isoformat() + " " + repr(argv) + "\n")
        stream.flush()
        with ConsoleProgress(label, run_dir=log.parent if "run" in argv or "report" in argv else None,
                             log_path=log, log_offset=log.stat().st_size), subprocess.Popen(argv, cwd=workspace, env=environment, stdout=stream,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              pass_fds=(descriptor,)) as process:
            try:
                code = process.wait()
            except BaseException:
                # SIGINT lets the CLI reap its own engine process groups first.
                try:
                    os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=15)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                raise
    if code:
        raise CommandFailed(code, argv, label, log)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="설치 → RFantibody 2개 + BoltzGen 2개 → API MSA → ColabFold·Boltz-2·Chai-1·OpenDDE 네 엔진 독립 예측 16건")
    result.add_argument("--target", type=Path, help="단일 단백질 아미노산 FASTA (실행 시 필수)")
    result.add_argument("--gpu", required=True, help="전체 NVIDIA GPU UUID")
    result.add_argument("--workspace", type=Path, default=REPO / ".nanocofold/collaborator",
                        help="모델·환경 설치 폴더 (기본: 체크아웃/.nanocofold/collaborator)")
    result.add_argument("--out", type=Path, help="실행 결과 폴더; 재개할 때 반드시 같은 폴더")
    result.add_argument("--accept-public-msa", action="store_true",
                        help="표적과 생성 VHH 서열을 https://api.colabfold.com에 전송하도록 명시 허용")
    result.add_argument("--resume", action="store_true", help="동일 입력·저장 설정만 검증하여 재개")
    result.add_argument("--skip-install", action="store_true", help="준비된 동일 workspace의 환경·가중치 사용")
    result.add_argument("--check-host", action="store_true", help="다운로드·설치·서열 전송·추론 없이 시스템/GPU 전제만 점검")
    return result


def prepare_smoke_fixtures(workspace: Path) -> None:
    # doctor uses these public runtime fixtures independently of the user input.
    # init_workspace intentionally does not copy benchmark/reference datasets.
    from nanocofold.diagnostics import _public_smoke_fixtures
    for source in _public_smoke_fixtures():
        name = source.name
        destination = workspace / "examples/tutorial" / name
        if destination.exists():
            if digest(destination.read_bytes()) != digest(source.read_bytes()):
                raise ValueError(f"기존 smoke fixture가 다릅니다. 덮어쓰지 않았습니다: {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def retained_completion(output: Path, receipt: dict) -> bool:
    """Recognize verified intentional retention, never a missing/corrupt full run."""
    try:
        return _retained_completion(output, receipt)
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("핵심 보관본 기록이 손상되었습니다. 자동 재추론하지 않았습니다.") from exc


def _retained_completion(output: Path, receipt: dict) -> bool:
    archive = output / "retained"
    if not archive.exists():
        return False
    if archive.is_symlink():
        raise ValueError("연결된 보관본은 자동 완료 판정하지 않습니다.")
    manifest = json.loads((archive / "manifest.json").read_text())
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("핵심 보관본 manifest가 손상되었습니다. 자동 재추론하지 않았습니다.")
    seen = set()
    for entry in entries:
        relative = entry.get("path", "")
        if not isinstance(relative, str):
            raise ValueError("핵심 보관본 경로가 잘못되었습니다.")
        path = output / relative
        if (not isinstance(relative, str) or not relative.startswith("retained/") or relative in seen
                or ".." in Path(relative).parts or Path(relative).is_absolute()
                or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(archive))
                or not path.resolve().is_relative_to(archive.resolve())
                or not path.is_file() or path.stat().st_size != entry.get("size")
                or file_digest(path) != entry.get("sha256")):
            raise ValueError("핵심 보관본 파일·해시 검증 실패. 자동 재추론하지 않았습니다.")
        seen.add(relative)
    if not {"retained/record.json", "retained/plan.json"}.issubset(seen):
        raise ValueError("핵심 보관본의 필수 기록이 없습니다.")
    plan = json.loads((archive / "plan.json").read_text())
    originals = {entry["path"]: entry["sha256"] for key in ("kept_files", "deletable_files")
                 for entry in plan.get(key, [])}
    recorded = receipt.get("completed_artifacts_sha256")
    if not isinstance(recorded, dict) or not recorded or any(originals.get(name) != sha for name, sha in recorded.items()):
        raise ValueError("핵심 보관본과 원래 완료 영수증이 다릅니다.")
    for entry in plan.get("kept_files", []):
        path = output / entry["path"]
        if (not path.resolve().is_relative_to(output.resolve()) or path.is_symlink()
                or not path.is_file() or file_digest(path) != entry["sha256"]):
            raise ValueError("보존한 원본 핵심 파일이 변경되었습니다.")
    return True


def completion_artifacts(output: Path) -> dict[str, str]:
    required = ("run_state.json", "report.html", "03_score/scored_candidates.csv", "report_delivery.zip")
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise ValueError("완료 실행의 필수 산출물이 없습니다: " + ", ".join(missing) +
                         ". 자동 재추론하지 않았습니다. nanocofold report로 보고서를 복구하세요.")
    state = read_run_state(output)
    if state.get("execution", {}).get("state") != "complete":
        raise ValueError("실행 상태가 완료가 아닙니다. 상태·로그를 확인하세요.")
    report_status = state.get("phases", {}).get("final", {}).get("report_status")
    if report_status is not None and report_status != "complete":
        raise ValueError("보고서 생성이 완료되지 않았습니다. 계산 파일을 보존하고 nanocofold report로 복구하세요.")
    delivery_status = state.get("phases", {}).get("final", {}).get("delivery_status")
    if delivery_status is not None and delivery_status != "complete":
        raise ValueError("전달 ZIP 저장이 완료되지 않았습니다. nanocofold report로 보고서만 복구하세요.")
    validate_delivery(output / "report_delivery.zip")
    return {name: file_digest(output / name) for name in required}


def read_run_state(output: Path) -> dict:
    state = json.loads((output / "run_state.json").read_text())
    if (not isinstance(state, dict) or not isinstance(state.get("execution", {}), dict)
            or not isinstance(state.get("phases", {}), dict)
            or not isinstance(state.get("phases", {}).get("final", {}), dict)):
        raise ValueError("실행 상태 형식이 올바르지 않습니다. 원본을 보존하고 상태를 확인하세요.")
    return state


def report_only_resume(output: Path) -> bool:
    """A persisted final phase follows calculation; recovery must not infer again."""
    state_path = output / "run_state.json"
    if not state_path.is_file():
        return False
    state = read_run_state(output)
    final = state.get("phases", {}).get("final", {})
    if "tables_committed" in final and final["tables_committed"] is not True:
        return False
    if final.get("table_sha256") is not None:
        expected_hashes = final["table_sha256"]
        if not isinstance(expected_hashes, dict) or set(expected_hashes) != {
                "03_score/scored_candidates.csv", "05_final/selected_candidates.csv"}:
            return False
        for name, expected in expected_hashes.items():
            if not (output / name).is_file() or file_digest(output / name) != expected:
                return False
    return bool(final and (final.get("report_status") in {"pending", "failed", "complete"}
                          or final.get("report"))
                and (output / "03_score/scored_candidates.csv").is_file()
                and (output / "05_final/selected_candidates.csv").is_file())


def execute(args) -> int:
    if args.check_host:
        check_host(args.gpu, args.skip_install)
        print("[호스트 전제 통과] 엔진 설치·CUDA 커널·모델 실행 검증은 아직 수행하지 않았습니다.", flush=True)
        return 0
    if args.target is None:
        raise ValueError("실제 분석에는 --target 단일 단백질 FASTA가 필요합니다.")
    if not args.accept_public_msa:
        raise ValueError("API MSA는 외부 서열 전송입니다. 허용한다면 --accept-public-msa를 명시하세요.")
    if args.resume and args.out is None:
        raise ValueError("--resume에는 기존 --out 결과 폴더가 필요합니다.")
    name, sequence, source_sha = read_target(args.target.expanduser().resolve())
    with ConsoleProgress("입력·호스트 확인"):
        gpu = check_host(args.gpu, args.skip_install)
    workspace = args.workspace.expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = (args.out or workspace / "runs" / ("collaborator_api_" + stamp)).expanduser().absolute()
    if output.is_symlink():
        raise ValueError("결과 폴더 심볼릭 링크는 사용하지 않습니다.")
    output = output.resolve()
    if args.resume and not output.is_dir():
        raise ValueError("재개할 결과 폴더가 없습니다.")
    if not args.resume and output.exists() and any(output.iterdir()):
        raise ValueError("결과 폴더가 비어 있지 않습니다. 같은 입력 재개에는 --resume을 지정하세요.")
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    print(f"[진행 로그] {output / 'quickstart.log'} · 다른 터미널에서 tail -f로 확인할 수 있습니다.", flush=True)
    template = REPO / "config/collaborator_api.yaml"
    identity = {"schema_version": 1, "workspace": str(workspace), "gpu": gpu,
                "target_source_sha256": source_sha, "target_sequence_sha256": digest(sequence.encode()),
                "preset_sha256": digest(template.read_bytes()), "public_msa_upload_authorized": True}
    receipt_path, config_path = output / "quickstart_receipt.json", output / "quickstart_config.yaml"
    snapshot = output / "input_target.fasta"
    # Different GPUs must not upgrade the same core environment concurrently.
    with lock(workspace / ".platform/quickstart.lock"), lock(output / ".quickstart.lock"):
        if args.resume:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("identity") != identity:
                raise ValueError("저장된 입력·GPU·workspace·프리셋과 다릅니다. 기존 실행을 변경하지 않았습니다.")
            if not snapshot.is_file() or digest(snapshot.read_bytes()) != receipt.get("input_snapshot_sha256"):
                raise ValueError("저장된 표적 FASTA가 변경되었습니다. 기존 실행을 변경하지 않았습니다.")
            if receipt.get("config_sha256") and (not config_path.is_file() or digest(config_path.read_bytes()) != receipt["config_sha256"]):
                raise ValueError("저장된 실행 설정이 변경되었습니다. 기존 실행을 변경하지 않았습니다.")
            if config_path.exists() and not receipt.get("config_sha256"):
                raise ValueError("설정 해시 기록이 불완전합니다. 파일을 보존했으며 자동 재추론하지 않았습니다.")
            if receipt.get("status") == "complete":
                if retained_completion(output, receipt):
                    print(f"[기존 실행 · 검증된 핵심 보관본] {output}\n서열·점수·최종 구조는 유지했습니다. "
                          "결과 탐색에서 핵심 보고서를 내보낼 수 있습니다. 삭제된 PAE·중간 자료는 새 분석에서 재계산하세요. "
                          "추론을 자동 재실행하지 않았습니다.", flush=True)
                    return 0
                if completion_artifacts(output) != receipt.get("completed_artifacts_sha256"):
                    raise ValueError("완료 실행의 요약 산출물이 변경되었습니다. 자동 재추론하지 않았습니다.")
                print(f"[기존 실행 완료] {output / 'report.html'}", flush=True)
                return 0
        else:
            if any(path.name != ".quickstart.lock" for path in output.iterdir()):
                raise ValueError("결과 폴더에 새 산출물이 생겼습니다. 중복 실행하지 않았습니다.")
            snapshot.write_text(f">target\n{sequence}\n", encoding="utf-8")
            receipt = {"identity": identity, "input_snapshot_sha256": digest(snapshot.read_bytes()), "attempts": []}
        attempt = {"started_utc": datetime.now(timezone.utc).isoformat(), "status": "preparing"}
        receipt.setdefault("attempts", []).append(attempt)
        receipt["status"] = "preparing"
        save_json(receipt_path, receipt)
        root = Path(os.environ.get("NANOCOFOLD_GPU_LOCK_DIR", str(Path.home() / ".cache/nanocofold")))
        if not root.is_absolute():
            raise ValueError("NANOCOFOLD_GPU_LOCK_DIR는 절대 경로여야 합니다.")
        try:
            with lock(root / f"gpu-{gpu}.lock") as descriptor:
                recovering_report = args.resume and report_only_resume(output)
                environment = {**os.environ, "NANOCOFOLD_ROOT": str(workspace), "CUDA_VISIBLE_DEVICES": gpu,
                               "NANOCOFOLD_GPU_LOCK_FD": str(descriptor)}
                core = workspace / ".venv-dev/bin/python"
                if not args.skip_install:
                    if (workspace / ".venv-dev").is_symlink():
                        raise ValueError("연결된 기존 core 환경을 수정하지 않습니다. 준비된 환경에는 --skip-install을 사용하세요.")
                    marker_path = workspace / ".platform/quickstart_core.json"
                    marker = json.loads(marker_path.read_text()) if marker_path.is_file() else {}
                    managed = marker.get("workspace") == str(workspace) and marker.get("python") == str(core)
                    if core.exists() and not managed and not (workspace / ".platform/setup-core.json").is_file():
                        raise ValueError("미등록 기존 core 환경을 수정하지 않습니다. 새 workspace 또는 --skip-install을 사용하세요.")
                    save_json(marker_path, {"workspace": str(workspace), "python": str(core), "status": "installing"})
                    run_command(["bash", str(REPO / "install.sh"), "--workspace", str(workspace), "--profile", "core"],
                                workspace=workspace, environment=environment, descriptor=descriptor, log=output / "quickstart.log")
                    save_json(marker_path, {"workspace": str(workspace), "python": str(core), "status": "complete"})
                    for profile in (() if recovering_report else ("design", "colabfold", "opendde")):
                        run_command([str(core), "-m", "nanocofold", "setup", "--workspace", str(workspace), "--profile", profile, "--yes"],
                                    workspace=workspace, environment=environment, descriptor=descriptor, log=output / "quickstart.log")
                if not core.is_file() or not os.access(core, os.X_OK):
                    raise ValueError("workspace의 core Python이 없습니다. --skip-install을 해제하고 설치하세요.")
                if not config_path.exists():
                    selected_device = {"CUDA_VISIBLE_DEVICES": gpu}
                    partial = {"target": {"name": name, "sequence": sequence},
                        "run": {"outdir": str(output.parent)},
                        "design": {"env_vars": selected_device, "boltzgen": {"env_vars": selected_device}},
                        "structure": {"boltz2": {"env_vars": selected_device}, "chai1": {"env_vars": selected_device}},
                        "screen": {"env_vars": selected_device, "allow_public_msa_server": True,
                                   "colabfold": {"env_vars": selected_device, "msa_cache_dir": str(output / "01_msa/api_cache")}}}
                    overrides_path = output / "quickstart_overrides.json"
                    save_json(overrides_path, partial)
                    code = """import json,sys,yaml
from pathlib import Path
from nanocofold.config import _deep_merge,load_config,dump_resolved
preset=yaml.safe_load(Path(sys.argv[1]).read_text())
override=json.loads(Path(sys.argv[2]).read_text())
partial=Path(sys.argv[3]).with_suffix('.partial.yaml')
partial.write_text(yaml.safe_dump(_deep_merge(preset,override),sort_keys=False))
try:
    cfg=load_config(partial)
    dump_resolved(cfg,sys.argv[3])
finally:
    partial.unlink(missing_ok=True)
"""
                    run_command([str(core), "-c", code, str(template), str(overrides_path), str(config_path)],
                                workspace=workspace, environment=environment, descriptor=descriptor, log=output / "quickstart.log")
                    receipt["config_sha256"] = digest(config_path.read_bytes())
                    save_json(receipt_path, receipt)
                if not recovering_report:
                    prepare_smoke_fixtures(workspace)
                    run_command([str(core), "-m", "nanocofold", "doctor", "--config", str(config_path), "--smoke", "--json"],
                                workspace=workspace, environment=environment, descriptor=descriptor, log=output / "quickstart.log")
                else:
                    print("[보고서 복구] 저장된 계산 결과로 HTML·전달 ZIP만 다시 만듭니다. 새 추론·MSA 요청은 하지 않습니다.", flush=True)
                receipt["status"] = attempt["status"] = "recovering_report" if recovering_report else "running"
                attempt["report_only"] = bool(recovering_report)
                save_json(receipt_path, receipt)
                command = "report" if recovering_report else "run"
                run_command([str(core), "-m", "nanocofold", command, "--config", str(config_path), "--run-dir", str(output)],
                            workspace=workspace, environment=environment, descriptor=descriptor, log=output / "quickstart.log")
                receipt["completed_artifacts_sha256"] = completion_artifacts(output)
                receipt["status"] = attempt["status"] = "complete"
        except BaseException as exc:
            receipt["status"] = attempt["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            attempt["finished_utc"] = datetime.now(timezone.utc).isoformat()
            save_json(receipt_path, receipt)
    print(f"[실행 완료] {output / 'report.html'}\n[공동연구자 전달] {output / 'report_delivery.zip'}\n압축을 풀고 report.html을 열면 MolStar·PAE·전체 모델을 확인할 수 있습니다. 예측 실패·미평가와 발주 게이트도 보존합니다.", flush=True)
    return 0


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    def cancelled(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancelled)
    try:
        return execute(args)
    except KeyboardInterrupt:
        print("[취소] 로그·입력·완료 산출물을 보존했습니다. 같은 --out과 --resume으로 확인하세요.", flush=True)
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[오류] {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
