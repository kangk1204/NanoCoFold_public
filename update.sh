#!/usr/bin/env bash
set -euo pipefail
usage() {
  printf '%s\n' \
    '사용법 / Usage: bash update.sh' \
    '대기·실행 작업이 끝나고 웹 서버를 종료한 뒤 실행하세요.' \
    'Finish queued/running jobs and stop the web server before updating.' \
    '저장된 설치 위치의 앱만 갱신합니다. / Updates the app in the saved installation.' \
    '도움말 / Help: bash update.sh --help'
}
if [ "$#" -gt 0 ]; then
  if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
    usage
    exit 0
  fi
  printf '%s\n' '지원하지 않는 옵션입니다. / Unsupported arguments; no update was started.' >&2
  usage >&2
  exit 2
fi
repo_dir="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$repo_dir/scripts/update_runtime.py" "$@"
