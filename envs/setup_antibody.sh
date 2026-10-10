#!/usr/bin/env bash
# ANARCI (BSD-3) + NanoBodyBuilder2 (ImmuneBuilder, BSD-3) 설치: CDR 넘버링/구조 모델링.
# ANARCI는 hmmscan(HMMER) 실행 파일이 필요하다. 없으면 소스에서 로컬 빌드한다.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }

source envs/install_locked.sh
install_locked antibody 3.11.15

# --- HMMER (hmmscan) 준비 ---
if [ -x "envs/antibody/bin/hmmscan" ]; then
  echo "[skip] envs/antibody/bin/hmmscan 이미 존재"
elif command -v hmmscan >/dev/null 2>&1 && hmmscan -h | head -3 | grep -q 'HMMER 3.4'; then
  echo "[link] 시스템 hmmscan 사용"
  for exe in hmmscan hmmsearch hmmpress hmmbuild; do
    ln -sf "$(command -v $exe)" "envs/antibody/bin/$exe"
  done
else
  echo "[build] HMMER 3.4 소스 빌드 (hmmscan 제공)..."
  command -v gcc >/dev/null || { echo "gcc가 필요합니다 (HMMER 빌드)."; exit 1; }
  command -v make >/dev/null || { echo "make가 필요합니다 (HMMER 빌드)."; exit 1; }
  # 영구 경로(tools/hmmer)에 설치한다. 임시 디렉터리를 지워 symlink가 깨지는 문제 방지(F15).
  mkdir -p tools
  TMP_DIR="$(mktemp -d)"
  HMMER_PREFIX="$(pwd)/tools/hmmer"
  trap 'rm -rf "$TMP_DIR"' EXIT
  curl -fsSL --retry 3 https://eddylab.org/software/hmmer/hmmer-3.4.tar.gz -o "$TMP_DIR/hmmer.tar.gz"
  echo "ca70d94fd0cf271bd7063423aabb116d42de533117343a9b27a65c17ff06fbf3  $TMP_DIR/hmmer.tar.gz" | sha256sum --check
  tar xzf "$TMP_DIR/hmmer.tar.gz" -C "$TMP_DIR"
  pushd "$TMP_DIR/hmmer-3.4" >/dev/null
  ./configure --prefix="$HMMER_PREFIX" >/dev/null
  make -j"$(nproc)" >/dev/null
  make install >/dev/null
  popd >/dev/null
  rm -rf "$TMP_DIR"
  for exe in hmmscan hmmsearch hmmpress hmmbuild; do
    ln -sf "$HMMER_PREFIX/bin/$exe" "envs/antibody/bin/$exe"
  done
fi

echo
echo "[완료] 항체 도구 설치. 검증:"
echo "  envs/antibody/bin/python -c \"from anarci import number; print('ok')\""
echo "  envs/antibody/bin/python -c \"from ImmuneBuilder import NanoBodyBuilder2; print('ok')\""
