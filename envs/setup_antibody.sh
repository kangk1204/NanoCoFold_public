#!/usr/bin/env bash
# ANARCI (BSD-3) + NanoBodyBuilder2 (ImmuneBuilder, BSD-3) 설치: CDR 넘버링/구조 모델링.
# ANARCI는 hmmscan(HMMER) 실행 파일이 필요하다. 없으면 소스에서 로컬 빌드한다.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv가 필요합니다."; exit 1; }

source envs/install_locked.sh
install_locked antibody 3.11.15

# --- HMMER (hmmscan) 준비 ---
if [ -x "envs/antibody/bin/hmmscan" ] && envs/antibody/bin/hmmscan -h >/dev/null 2>&1; then
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
  # Keep every failed/partial attempt for inspection; a retry gets a new folder.
  ATTEMPT_ROOT="$(pwd)/.platform/hmmer-source-attempts"
  mkdir -p "$ATTEMPT_ROOT"
  TMP_DIR="$(mktemp -d "$ATTEMPT_ROOT/attempt.XXXXXXXX")"
  HMMER_PREFIX="$(pwd)/tools/hmmer"
  STATUS_FILE="$TMP_DIR/status.txt"
  printf 'status=started\n' > "$STATUS_FILE"
  trap 'attempt_exit=$?; if [ "$attempt_exit" -ne 0 ]; then printf "status=failed\nexit_code=%s\n" "$attempt_exit" >> "$STATUS_FILE"; fi' EXIT
  if curl -fsSL --connect-timeout 20 --max-time 120 --retry 2 --retry-max-time 240 \
      https://eddylab.org/software/hmmer/hmmer-3.4.tar.gz -o "$TMP_DIR/hmmer.tar.gz.part"; then
    printf 'status=downloaded\n' >> "$STATUS_FILE"
  else
    download_exit=$?
    printf 'status=download_failed\nexit_code=%s\n' "$download_exit" >> "$STATUS_FILE"
    echo "[실패] HMMER 공식 소스 다운로드가 제한 시간 또는 네트워크 오류로 중단되었습니다." >&2
    echo "네트워크 연결을 확인한 뒤 같은 설치 명령을 다시 실행하세요. 실패 기록과 부분 파일은 .platform/hmmer-source-attempts에 보존됩니다." >&2
    exit "$download_exit"
  fi
  if ! echo "ca70d94fd0cf271bd7063423aabb116d42de533117343a9b27a65c17ff06fbf3  $TMP_DIR/hmmer.tar.gz.part" | sha256sum --check; then
    printf 'status=checksum_failed\n' >> "$STATUS_FILE"
    echo "[실패] HMMER 소스 SHA-256이 일치하지 않습니다. 파일을 사용하지 않았으며 .platform/hmmer-source-attempts에 보존했습니다. 네트워크 연결을 확인한 뒤 다시 설치하세요." >&2
    exit 1
  fi
  mv "$TMP_DIR/hmmer.tar.gz.part" "$TMP_DIR/hmmer.tar.gz"
  tar xzf "$TMP_DIR/hmmer.tar.gz" -C "$TMP_DIR"
  pushd "$TMP_DIR/hmmer-3.4" >/dev/null
  ./configure --prefix="$HMMER_PREFIX" >/dev/null
  make -j"$(nproc)" >/dev/null
  make install >/dev/null
  popd >/dev/null
  for exe in hmmscan hmmsearch hmmpress hmmbuild; do
    ln -sf "$HMMER_PREFIX/bin/$exe" "envs/antibody/bin/$exe"
  done
  printf 'status=complete\n' >> "$STATUS_FILE"
fi

echo
echo "[완료] 항체 도구 설치. 검증:"
echo "  envs/antibody/bin/python -c \"from anarci import number; print('ok')\""
echo "  envs/antibody/bin/python -c \"from ImmuneBuilder import NanoBodyBuilder2; print('ok')\""
