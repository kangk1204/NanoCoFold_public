#!/usr/bin/env bash
# Chai-1 (Apache-2.0) 설치: 고속 복합체 예측 + hotspot restraint 기반 스크리닝.
# Blackwell(sm_120+)에서는 chai_lab의 torch<2.7 핀에 cu128 휠이 없어 torch를 수동 오버라이드한다.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }

source envs/install_locked.sh
# Exact tested torch 2.8.0+cu128 override, with every NVIDIA dependency hashed.
install_locked chai1 3.11.15

echo
echo "[완료] Chai-1 설치. 검증:"
echo "  envs/chai1/bin/python -c \"import torch, chai_lab; x=torch.randn(32,32,device='cuda'); print(torch.__version__, float((x@x).sum()))\""
echo "  GPU OOM 시 screen.env_vars: {PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True} 설정"
