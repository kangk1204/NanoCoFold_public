#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null || { echo '먼저 bash install.sh를 실행하세요.' >&2; exit 1; }
exec python3 "$repo_dir/scripts/start_server.py" "$@"
