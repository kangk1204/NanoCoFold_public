"""Start the installed browser application using its saved workspace and GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
try:
    receipt = json.loads((root / ".nanocofold/install-location.json").read_text())
    workspace = Path(receipt["workspace"])
    binary = workspace / ".venv-dev/bin/nanocofold"
    if not workspace.is_absolute() or not binary.is_file():
        raise ValueError("설치 위치를 확인하지 못했습니다.")
    environment = dict(os.environ, NANOCOFOLD_ROOT=str(workspace))
    uv_binary = Path(receipt.get("uv_path") or root / ".nanocofold/bootstrap/bin/uv")
    if uv_binary.is_absolute() and uv_binary.is_file():
        environment["PATH"] = str(uv_binary.parent) + os.pathsep + environment.get("PATH", "")
    if receipt.get("gpu"):
        environment.update(NANOCOFOLD_GPU=receipt["gpu"], CUDA_VISIBLE_DEVICES=receipt["gpu"])
    # The product CLI defaults to localhost. User-supplied port/no-browser options
    # remain ordinary CLI arguments; this wrapper never opens a public listener.
    sys.exit(subprocess.call([str(binary), "serve", "--workspace", str(workspace), *sys.argv[1:]], env=environment))
except (OSError, ValueError, KeyError) as error:
    print("시작할 수 없습니다. 먼저 bash install.sh를 완료하세요. " + str(error), file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    sys.exit(130)
