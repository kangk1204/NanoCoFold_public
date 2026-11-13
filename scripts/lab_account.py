"""Manage accounts using the Python environment installed for this workspace."""
import os
import sys
from pathlib import Path

from start_server import installation_location


if __name__ == "__main__":
    try:
        _, workspace = installation_location(Path(__file__).resolve().parents[1])
        python = workspace / ".venv-dev/bin/python"
        if not python.is_file():
            raise ValueError("설치 환경을 확인하세요. / Run bash update.sh in the installation folder.")
        os.execv(str(python), [str(python), "-I", "-m", "nanocofold.platform.lab", str(workspace), *sys.argv[1:]])
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
