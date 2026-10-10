#!/usr/bin/env bash
# ESM-2 (MIT) + AntiFold (BSD-3) 설치: 단량체 적합도 및 VHH 역폴딩 스코어링.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }

source envs/install_locked.sh
install_locked esm 3.11.15

echo
echo "[완료] ESM-2 + AntiFold 설치. 검증:"
echo "  envs/esm/bin/python -c \"import esm; print(esm.pretrained.esm2_t33_650M_UR50D)\""
echo "  envs/esm/bin/python -m antifold.main --help"
echo "  nanocofold setup --profile mature  # 로컬 가중치 다운로드 및 SHA256 검증"
echo "AntiFold 실행은 mature.antifold.model_path의 로컬 가중치를 사용합니다."
