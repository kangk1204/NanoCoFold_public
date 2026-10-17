#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  -h|--help)
    printf '%s\n' \
      '사용법 / Usage: bash start.sh [--port 18765] [--no-browser]' \
      '처음에는 bash install.sh로 설치하세요. 설치 옵션: bash install.sh --help' \
      'First run bash install.sh. Installation options: bash install.sh --help' \
      '예시 / Example: bash start.sh --port 18765' \
      '원격 서버 / Remote server: bash start.sh --no-browser --port 18765'
    exit 0 ;;
esac
repo_dir="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null || { echo '먼저 bash install.sh를 실행하세요. / First run bash install.sh.' >&2; exit 1; }
exec python3 "$repo_dir/scripts/start_server.py" "$@"
