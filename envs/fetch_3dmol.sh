#!/usr/bin/env bash
# (선택) 고정된 오프라인 3Dmol.js (BSD-3) 로컬 자산 검증·복구.
set -euo pipefail
cd "$(dirname "$0")/.."
ASSET="assets/3dmol-min.js"
EXPECTED_SHA256="4ab374e1830bccec950b280fd285a430e05e91bd13c9ef0097c611c66de6cb36"
if [[ -L assets || -L "$ASSET" ]]; then
  echo "[오류] assets 또는 3dmol-min.js의 심볼릭 링크는 변경하지 않습니다." >&2
  exit 1
fi
mkdir -p assets
digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f1
  else
    shasum -a 256 "$1" | cut -d ' ' -f1
  fi
}
preserve_rejected() {
  local source="$1" actual="$2" rejected
  rejected="$(mktemp -d assets/.3dmol-rejected-XXXXXX)"
  mv -- "$source" "$rejected/3dmol-min.js"
  printf 'expected_sha256=%s\nactual_sha256=%s\noriginal_path=%s\n' \
    "$EXPECTED_SHA256" "$actual" "$source" > "$rejected/verification.txt"
  echo "[보존] 검증하지 못한 파일: $rejected/3dmol-min.js" >&2
}
if [[ -e "$ASSET" ]]; then
  if [[ ! -f "$ASSET" ]]; then
    echo "[오류] $ASSET 은 일반 파일이어야 합니다." >&2
    exit 1
  fi
  actual="$(digest "$ASSET")"
  if [[ "$actual" == "$EXPECTED_SHA256" ]]; then
    echo "[검증 완료] $ASSET · SHA256 $actual"
    exit 0
  fi
  preserve_rejected "$ASSET" "$actual"
fi
URL="${NANOCOFOLD_3DMOL_SOURCE_URL:-}"
if [[ "$URL" != https://?* ]]; then
  echo "[오류] 검증된 저장소 자산을 복구하거나 NANOCOFOLD_3DMOL_SOURCE_URL에 같은 파일의 고정 HTTPS 주소를 지정하세요." >&2
  echo "[기대 SHA256] $EXPECTED_SHA256" >&2
  exit 1
fi
staged="$(mktemp assets/.3dmol-staging-XXXXXX)"
echo "[fetch] $URL"
if ! curl --proto '=https' --proto-redir '=https' -fLS --retry 3 \
    --connect-timeout 20 --max-time 300 "$URL" -o "$staged"; then
  echo "[오류] 다운로드가 끝나지 않았습니다. 부분 파일 보존: $staged" >&2
  exit 1
fi
actual="$(digest "$staged")"
if [[ "$actual" != "$EXPECTED_SHA256" ]]; then
  preserve_rejected "$staged" "$actual"
  echo "[오류] SHA256이 다릅니다. 뷰어 자산으로 설치하지 않았습니다." >&2
  exit 1
fi
if [[ -L assets || -L "$ASSET" ]]; then
  echo "[오류] 출력 경로가 심볼릭 링크로 변경됐습니다. 검증 파일 보존: $staged" >&2
  exit 1
fi
chmod 644 "$staged"
mv -- "$staged" "$ASSET"
echo "[완료] $ASSET · SHA256 $actual"
