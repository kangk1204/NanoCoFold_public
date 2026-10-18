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
printf '%s\n' \
  'NanoCoFold 웹 서버를 시작합니다. / Starting the NanoCoFold web server.' \
  'NanoCoFold: 뒤에 주소가 표시되면 브라우저로 여세요. / Wait for the URL after NanoCoFold:, then open it in your browser.' \
  '사용하는 동안 이 터미널을 열어 두세요. / Keep this terminal open while using NanoCoFold.' \
  '서버가 실행되는 동안 새 명령 프롬프트가 표시되지 않는 것이 정상입니다. / No new command prompt is expected while the server is running.'
exec python3 "$repo_dir/scripts/start_server.py" "$@"
