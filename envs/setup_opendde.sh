#!/usr/bin/env bash
# Optional OpenDDE Preview, isolated from existing prediction environments.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }
source envs/install_locked.sh
install_locked opendde 3.11.15
uv pip install --python envs/opendde/bin/python --no-deps --no-build-isolation --require-hashes \
  -r nanocofold/_data/locks/opendde-package.txt
uv pip check --python envs/opendde/bin/python
echo "OpenDDE Preview 환경 설치 완료. 가중치와 실제 GPU 추론 준비는 별도 점검합니다."
