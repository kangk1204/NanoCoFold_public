#!/usr/bin/env bash
# Boltz-2 (MIT) 설치: 타깃 구조 예측 및 복합체 정밀 검증.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }

source envs/install_locked.sh
install_locked boltz2 3.11.15

echo
echo "[완료] Boltz-2 설치. 검증:"
echo "  envs/boltz2/bin/boltz predict --help"
