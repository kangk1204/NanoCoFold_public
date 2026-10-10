#!/usr/bin/env bash
# NanoCoFold 오케스트레이터 개발환경 (.venv-dev). GPU/모델 불필요.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

uv venv --python 3.12 .venv-dev
uv pip install --python .venv-dev/bin/python -e ".[dev]"
echo
echo "[완료] .venv-dev 생성."
echo "  사용: source .venv-dev/bin/activate && nanocofold doctor"
