"""Update the managed application, preserving workspace, GPU and engine installs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from install_runtime import ROOT, lock, now, reject_active_jobs, save


def installation(root):
    location_path = root / ".nanocofold/install-location.json"
    if not location_path.is_file():
        raise ValueError("처음 설치한 소스 폴더에서 업데이트하세요. 새 설치는 bash install.sh를 실행하세요. / "
                         "Update from the original installation folder. For a new installation, run bash install.sh.")
    location = json.loads(location_path.read_text())
    workspace = Path(location["workspace"])
    python = workspace / ".venv-dev/bin/python"
    uv = Path(location["uv_path"])
    marker_path = workspace / ".platform/public-core-install.json"
    marker = json.loads(marker_path.read_text())
    if (not workspace.is_absolute() or not python.is_file() or not uv.is_file() or
            (workspace / ".venv-dev").is_symlink() or
            marker.get("workspace") != str(workspace) or marker.get("python") != str(python) or
            marker.get("status") != "complete"):
        raise ValueError("관리 중인 설치 위치를 확인할 수 없습니다. 기존 파일을 보존했습니다.")
    return location, workspace, python, uv


def update(root=ROOT, *, run=subprocess.run):
    location, workspace, python, uv = installation(root)
    # This operation neither selects nor probes a GPU, and never initializes or
    # replaces the workspace. No setup/doctor/analysis command is invoked.
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "CUDA_VISIBLE_DEVICES": ""}
    environment.pop("PYTHONPATH", None)
    environment.pop("NANOCOFOLD_GPU", None)
    # Also exclude the scheduler of an already-running older application.
    with lock(workspace / ".platform/public-install.lock") as install_fd, lock(workspace / ".nanocofold/supervisor.lock") as supervisor_fd:
        reject_active_jobs(workspace)
        receipt = {"status": "updating", "started_utc": now(), "workspace": str(workspace),
                   "source": str(root), "saved_gpu": location.get("gpu")}
        destination = workspace / ".platform/core-updates" / (now().replace(":", "-") + ".json")
        current = workspace / ".platform/core-update-state.json"
        receipt["receipt_path"] = str(destination)
        save(destination, receipt)
        save(current, receipt)
        try:
            for args in ([uv,"pip","install","--python",python,"--no-deps","--require-hashes","-r",root/"nanocofold/_data/locks/core.txt"],
                         [uv,"pip","install","--python",python,"--no-deps","--reinstall-package","nanocofold",root],
                         [uv,"pip","check","--python",python],
                         [python,"-c",'from nanocofold.platform.app import create_app; from nanocofold.platform.environment import readiness; print("Application update verified")']):
                # A surviving package child retains exclusion if this wrapper
                # is interrupted or killed before it can finish cleanup.
                run(list(map(str,args)),check=True,cwd=workspace,env=environment,
                    pass_fds=(install_fd, supervisor_fd))
            receipt.update(status="complete", finished_utc=now())
        except BaseException as error:
            receipt.update(status="failed",finished_utc=now(),error=str(error))
            raise
        finally:
            try:
                save(destination, receipt)
            finally:
                save(current, receipt)
    print("업데이트 완료 / Update complete: bash start.sh\n작업 공간·GPU 선택·기존 결과를 보존했습니다. / Workspace, GPU selection, and results preserved.")


if __name__ == "__main__":
    try:
        # Use the installed Python for its job database and dependency checks.
        # The host Python can use a different SQLite library (notably on macOS).
        _, _, managed_python, _ = installation(ROOT)
        if Path(sys.executable).absolute() != managed_python:
            os.execv(str(managed_python), [str(managed_python), str(Path(__file__).resolve())])
        update()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,sqlite3.Error) as error:
        print("업데이트 실패 / Update failed: " + str(error),file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
