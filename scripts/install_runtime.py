"""Standard-library bootstrap; no application imports or analysis submission."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.12.13"
PROFILES = ("all", "core", "complex", "design", "screen", "mature", "boltzgen", "colabfold", "opendde")
GPU_PATTERN = re.compile(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--workspace", type=Path, default=ROOT / ".nanocofold/workspace")
    result.add_argument("--profile", choices=PROFILES, default="all")
    result.add_argument("--gpu")
    result.add_argument("--check-host", action="store_true")
    result.add_argument("--skip-system-packages", action="store_true")
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def select_gpu(requested):
    output = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,driver_version,name,memory.total",
                             "--format=csv,noheader,nounits"], check=True, text=True,
                            capture_output=True, timeout=20).stdout
    rows = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5 or not GPU_PATTERN.fullmatch(parts[1]):
            raise ValueError("nvidia-smi 응답 형식을 확인하지 못했습니다.")
        rows.append(dict(zip(("index", "uuid", "driver", "name", "memory_mib"), parts)))
    if requested is None:
        if len(rows) != 1:
            raise ValueError("GPU가 여러 개이거나 없습니다. nvidia-smi -L에서 사용할 GPU의 전체 UUID를 확인하고 --gpu GPU-... 를 지정하세요.")
        selected = rows[0]
    else:
        if not GPU_PATTERN.fullmatch(requested):
            raise ValueError("--gpu에는 번호가 아닌 전체 GPU-UUID를 넣으세요.")
        matches = [row for row in rows if row["uuid"].lower() == requested.lower()]
        if len(matches) != 1:
            raise ValueError("선택한 GPU UUID를 확인하지 못했습니다.")
        selected = matches[0]
    selected["uuid"] = selected["uuid"].lower().replace("gpu-", "GPU-", 1)
    driver = selected["driver"]
    if not re.fullmatch(r"\d+(?:\.\d+)+", driver) or int(driver.split(".")[0]) < 580:
        raise ValueError("고정 CUDA 13 패키지에는 NVIDIA 드라이버 580 이상이 필요합니다. 이 설치기는 드라이버를 변경하지 않습니다.")
    if "NANOCOFOLD_ALLOWED_GPUS" in os.environ:
        allowed = [item.strip().lower() for item in os.environ["NANOCOFOLD_ALLOWED_GPUS"].split(",")]
        if not all(allowed) or (selected["uuid"].lower() not in allowed and selected["index"] not in allowed):
            raise ValueError("선택한 GPU는 관리자가 지정한 허용 GPU 범위에 없습니다.")
    return selected


def check_host(profile, gpu):
    if profile == "core":
        return None
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("GPU 환경은 Linux x86_64에서 설치하세요.")
    libc, version = platform.libc_ver()
    if libc != "glibc" or not re.fullmatch(r"\d+(?:\.\d+)+", version) or tuple(map(int, version.split(".")[:2])) < (2, 34):
        raise ValueError("GPU 환경은 glibc 2.34 이상이 필요합니다.")
    required = ("nvidia-smi", "git", "curl", "gcc", "g++", "make", "timeout", "sha256sum", "tar", "gzip", "awk", "grep", "head", "mktemp", "nproc", "ps")
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise ValueError("필수 시스템 도구가 없습니다: " + ", ".join(missing) + ". bash install.sh를 --skip-system-packages 없이 실행하세요.")
    for library in ("libstdc++.so.6", "libgomp.so.1"):
        ctypes.CDLL(library)
    return select_gpu(gpu)


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("잠금 파일 소유자/형식이 올바르지 않습니다: " + str(path))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("다른 설치 또는 GPU 작업이 실행 중입니다. 종료 후 같은 명령을 다시 실행하세요.") from exc
        yield descriptor
    finally:
        # Persistent lock inode: never remove/recreate it.
        os.close(descriptor)


def reject_active_jobs(workspace):
    database = workspace / ".nanocofold/jobs.sqlite3"
    if not database.is_file():
        return
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        active = connection.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')").fetchone()[0]
        if active:
            raise ValueError("이 작업 공간에 대기/실행 중인 작업이 있습니다. 종료 후 설치를 실행하세요.")
    finally:
        connection.close()


def ensure_uv(environment):
    binary = shutil.which("uv", path=environment.get("PATH"))
    if binary:
        version = subprocess.run([binary, "--version"], check=True, text=True, capture_output=True, timeout=20).stdout
        if version.strip().split()[:2] == ["uv", UV_VERSION]:
            return binary
    destination = ROOT / ".nanocofold/bootstrap/bin"
    binary = destination / "uv"
    if binary.is_file():
        version = subprocess.run([str(binary), "--version"], check=True, text=True, capture_output=True, timeout=20).stdout
        if version.strip().split()[:2] == ["uv", UV_VERSION]:
            return str(binary)
        raise ValueError("기존 bootstrap uv 버전이 예상과 다릅니다. 파일을 보존했습니다.")
    if not shutil.which("curl", path=environment.get("PATH")):
        raise ValueError("uv 설치에 curl이 필요합니다. --skip-system-packages 없이 다시 설치하세요.")
    with tempfile.TemporaryDirectory(prefix="nanocofold-uv-") as directory:
        script = Path(directory) / "install.sh"
        subprocess.run(["curl", "--proto", "=https", "--tlsv1.2", "-fLS", "--retry", "3", "--connect-timeout", "20", "--max-time", "120",
                        f"https://astral.sh/uv/{UV_VERSION}/install.sh", "-o", str(script)], check=True, env=environment)
        subprocess.run(["sh", str(script)], check=True,
                       env={**environment, "UV_INSTALL_DIR": str(destination), "UV_NO_MODIFY_PATH": "1"})
    version = subprocess.run([str(binary), "--version"], check=True, text=True, capture_output=True, timeout=20).stdout
    if version.strip().split()[:2] != ["uv", UV_VERSION]:
        raise ValueError("uv 버전 검증 실패")
    return str(binary)


def install(args):
    gpu = check_host(args.profile, args.gpu)
    print("[호스트 확인] " + (json.dumps(gpu, ensure_ascii=False) if gpu else "CPU 기본 프로그램"), flush=True)
    if args.check_host:
        print("설치·서열 전송·분석을 시작하지 않았습니다.")
        return
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if (workspace / ".venv-dev").is_symlink():
        raise ValueError("심볼릭 링크 core 환경은 변경하지 않습니다. 별도 workspace를 사용하세요.")
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "NANOCOFOLD_ROOT": str(workspace)}
    with contextlib.ExitStack() as stack:
        stack.enter_context(lock(workspace / ".platform/public-install.lock"))
        reject_active_jobs(workspace)
        core_marker = workspace / ".platform/public-core-install.json"
        core_python = workspace / ".venv-dev/bin/python"
        if core_python.exists():
            marker = json.loads(core_marker.read_text()) if core_marker.is_file() else {}
            if marker.get("workspace") != str(workspace) or marker.get("python") != str(core_python):
                raise ValueError("이 설치기가 관리하지 않는 기존 core 환경을 변경하지 않습니다. 새 workspace를 지정하세요.")
        descriptors = ()
        if gpu:
            lock_root = Path(os.environ.get("NANOCOFOLD_GPU_LOCK_DIR", str(Path.home() / ".cache/nanocofold")))
            if not lock_root.is_absolute():
                raise ValueError("NANOCOFOLD_GPU_LOCK_DIR는 절대 경로여야 합니다.")
            descriptor = stack.enter_context(lock(lock_root / ("gpu-" + gpu["uuid"] + ".lock")))
            environment.update(CUDA_VISIBLE_DEVICES=gpu["uuid"], NANOCOFOLD_GPU=gpu["uuid"], NANOCOFOLD_GPU_LOCK_FD=str(descriptor))
            descriptors = (descriptor,)
        attempt = workspace / ".platform/install-attempts" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
        receipt = {"schema_version": 1, "started_utc": now(), "status": "installing", "workspace": str(workspace), "profile": args.profile, "gpu": gpu}
        save(attempt, receipt)
        try:
            uv = ensure_uv(environment)
            environment["PATH"] = str(Path(uv).parent) + os.pathsep + environment.get("PATH", "")
            python = core_python
            save(core_marker, {"workspace": str(workspace), "python": str(python), "status": "installing"})
            def run(arguments):
                print("[설치 단계] " + " ".join(map(str, arguments)), flush=True)
                subprocess.run(list(map(str, arguments)), check=True, cwd=workspace, env=environment, pass_fds=descriptors)
            if not python.is_file():
                run([uv, "venv", "--python", "3.12.13", python.parent.parent])
            run([uv, "pip", "install", "--python", python, "--no-deps", "--require-hashes", "-r", ROOT / "nanocofold/_data/locks/core.txt"])
            run([uv, "pip", "install", "--python", python, "--no-deps", ROOT])
            run([uv, "pip", "check", "--python", python])
            run([python, "-c", 'import numpy,pandas,Bio,yaml,jinja2,openpyxl,fastapi,uvicorn; print("[core import] OK")'])
            run([python, "-m", "nanocofold", "init", "--workspace", workspace])
            run([python, "-m", "nanocofold", "setup", "--workspace", workspace, "--profile", args.profile, "--yes"])
            setup = json.loads((workspace / ".platform" / f"setup-{args.profile}.json").read_text())
            if setup.get("status") != "complete" or setup.get("offline_ready") is not True:
                raise ValueError("실제 설치 영수증이 완료/준비 상태를 확인하지 못했습니다.")
            save(core_marker, {"workspace": str(workspace), "python": str(python), "status": "complete"})
            receipt.update(status="complete", finished_utc=now(), setup=setup)
            save(attempt, receipt)
            location = {"schema_version": 1, "workspace": str(workspace), "gpu": gpu["uuid"] if gpu else None, "profile": args.profile, "uv_path": str(uv), "installed_utc": now(), "attempt_receipt": str(attempt)}
            save(ROOT / ".nanocofold/install-location.json", location)
            print("[설치 완료] bash start.sh\n설치 확인은 분석 완료를 뜻하지 않습니다. README의 공개 예제로 계산을 시작하세요.", flush=True)
        except BaseException as exc:
            receipt.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", finished_utc=now(), error=str(exc))
            save(attempt, receipt)
            raise


if __name__ == "__main__":
    try:
        install(parser().parse_args())
    except KeyboardInterrupt:
        print("\n설치 중단: 기존 파일과 설치 기록을 보존했습니다.", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as error:
        print("[설치 오류] " + str(error), file=sys.stderr)
        sys.exit(1)
