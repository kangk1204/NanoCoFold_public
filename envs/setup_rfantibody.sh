#!/usr/bin/env bash
# RFantibody (MIT) 설치: de novo VHH 백본/서열 설계 엔진.
# Blackwell은 별도 호환 환경으로 설치하고 기존 .venv는 보존한다.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }
command -v git >/dev/null || { echo "git이 필요합니다."; exit 1; }

profile="${1:-auto}"
case "$profile" in
  --blackwell) profile=blackwell ;;
  --legacy) profile=legacy ;;
  auto)
    if command -v nvidia-smi >/dev/null &&
       nvidia-smi --query-gpu=compute_cap --format=csv,noheader | awk -F. 'int($1)>=12 {found=1} END {exit !found}'; then
      profile=blackwell
    else
      profile=legacy
    fi
    ;;
  *) echo "사용법: bash envs/setup_rfantibody.sh [--blackwell|--legacy]"; exit 2 ;;
esac
if [ "$profile" = blackwell ]; then
  exec bash envs/setup_rfantibody_blackwell.sh
fi

# The same pinned CUDA 12.8 profile supports older CUDA-capable NVIDIA GPUs.
# Keep --legacy as a CLI compatibility alias; never clone an unpinned HEAD.
exec bash envs/setup_rfantibody_blackwell.sh
