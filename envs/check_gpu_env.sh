#!/usr/bin/env bash
# GPU / 시스템 환경 점검. 특별한 의존성 없이 실행 가능.
set -u
echo "=== NanoCoFold 환경 점검 ==="
echo "[OS] $(uname -srm)"
echo "[CPU] $(nproc) cores"
echo "[RAM] $(free -g 2>/dev/null | awk 'NR==2{print $2" GB total / "$7" GB available"}')"
echo "[DISK] $(df -h . | awk 'NR==2{print $4" free of "$2" at " $6}')"
echo
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "=== GPU ==="
  nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv
  CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
  MAJOR=${CAP%%.*}
  if [ "${MAJOR:-0}" -ge 12 ]; then
    echo
    echo "[경고] compute capability ${CAP} (Blackwell/신형)."
    echo "  - Boltz-2 / Chai-1: PyTorch cu128 이상 필요 (최신 휠 자동 선택됨)."
    echo "  - RFantibody: 기본 설치가 torch cu118 + DGL 조합이라 sm_120 미지원일 수 있음."
    echo "    대응: ① CPU로 파일럿 실행(--device cpu, 느림) ② 신형 torch로 수동 재설치"
    echo "    ③ 클라우드 A100/H100에서 design 단계만 실행 후 runs/ 복사."
  fi
else
  echo "[GPU] nvidia-smi 없음 → CPU 전용 모드(테스트/리포트만 가능)"
fi
echo
echo "=== 도구 env 상태 ==="
for e in rfantibody boltz2 chai1 esm antibody; do
  if [ -x "envs/$e/bin/python" ]; then
    echo "  envs/$e: OK"
  else
    echo "  envs/$e: 없음 (envs/setup_${e}.sh 실행 필요)"
  fi
done
echo
echo "=== 프레임워크/데이터 ==="
[ -f data/frameworks/h-NbBCII10.pdb ] && echo "  VHH framework: OK" || echo "  VHH framework: 없음 (RFantibody 설치 스크립트가 복사)"
echo
echo "완료. 문제가 있으면 docs/01_설치가이드.md 참조."
