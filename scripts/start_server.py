"""Start the installed browser application using its saved workspace and GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys

from install_runtime import ROOT, lock, require_complete_core


def installation_location(root):
    try:
        receipt = json.loads((root / ".nanocofold/install-location.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("설치가 필요합니다. 먼저 bash install.sh를 실행하세요. / First run bash install.sh, then start again.") from None
    except (OSError, ValueError, UnicodeError):
        raise ValueError("설치 기록을 읽을 수 없습니다. bash install.sh를 다시 실행하세요. / Cannot read the installation record. Rerun bash install.sh.") from None
    if (not isinstance(receipt, dict) or not isinstance(receipt.get("workspace"), str)
            or not receipt["workspace"].strip()
            or not isinstance(receipt.get("uv_path", ""), (str, type(None)))
            or not isinstance(receipt.get("gpu", ""), (str, type(None)))):
        raise ValueError("설치 기록을 확인할 수 없습니다. bash install.sh를 다시 실행하세요. / Invalid installation record. Rerun bash install.sh.")
    workspace = Path(receipt["workspace"])
    if not workspace.is_absolute() or not (workspace / ".venv-dev/bin/nanocofold").is_file():
        raise ValueError("설치된 프로그램을 찾을 수 없습니다. bash install.sh를 다시 실행하세요. / Cannot find the installed program. Rerun bash install.sh.")
    return receipt, workspace


def start(root=ROOT, arguments=None, *, call=subprocess.call):
    receipt, workspace = installation_location(root)
    binary = workspace / ".venv-dev/bin/nanocofold"
    # Hold before the child imports the installed package, until the server exits.
    with lock(workspace / ".platform/public-install.lock", shared=True) as descriptor:
        require_complete_core(workspace)
        try:
            state = json.loads((workspace / ".platform/core-update-state.json").read_text())
        except FileNotFoundError:
            state = {"status": "complete"}
        except (OSError, ValueError) as error:
            raise ValueError("업데이트 상태를 확인할 수 없습니다. bash update.sh를 다시 실행하세요. / Cannot verify update state; rerun bash update.sh.") from error
        if not isinstance(state, dict) or state.get("status") != "complete":
            raise ValueError("업데이트가 완료되지 않았습니다. bash update.sh를 다시 실행하세요. / Update is incomplete; rerun bash update.sh.")
        environment = dict(os.environ, NANOCOFOLD_ROOT=str(workspace))
        uv_binary = Path(receipt.get("uv_path") or root / ".nanocofold/bootstrap/bin/uv")
        if uv_binary.is_absolute() and uv_binary.is_file():
            environment["PATH"] = str(uv_binary.parent) + os.pathsep + environment.get("PATH", "")
        if receipt.get("gpu"):
            environment.update(NANOCOFOLD_GPU=receipt["gpu"], CUDA_VISIBLE_DEVICES=receipt["gpu"])
        # The product CLI defaults to localhost. User options remain CLI args.
        return call([str(binary), "serve", "--workspace", str(workspace), *(arguments or [])],
                    env=environment, pass_fds=(descriptor,))


if __name__ == "__main__":
    try:
        sys.exit(start(arguments=sys.argv[1:]))
    except (OSError, ValueError, KeyError) as error:
        print("시작할 수 없습니다. / Cannot start: " + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
