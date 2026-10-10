#!/usr/bin/env bash
# Install the application and selected engines into a separate workspace.
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")" && pwd)"
profile=all
skip_system=false
check_only=false
args=("$@")
usage() {
  cat <<'USAGE'
Usage: bash install.sh [--gpu GPU-UUID] [--workspace DIR] [--profile all|core|complex|design|screen|mature|boltzgen|colabfold|opendde]
                      [--check-host] [--skip-system-packages]
Default: all engines, .nanocofold/workspace, automatically select a single GPU.
--check-host changes nothing. --profile core installs only the CPU application.
--skip-system-packages is for administrators who already prepared system packages.
USAGE
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --gpu|--workspace|--profile)
      if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
        echo "[오류] $1 뒤에 값을 지정하세요." >&2; exit 2
      fi
      if [ "$1" = --profile ]; then profile="$2"; fi
      shift 2 ;;
    --skip-system-packages) skip_system=true; shift ;;
    --check-host) check_only=true; shift ;;
    *) usage >&2; exit 2 ;;
  esac
done
case "$profile" in all|core|complex|design|screen|mature|boltzgen|colabfold|opendde) ;; *) usage >&2; exit 2;; esac
if [ "$profile" != core ] || ! "$skip_system"; then
  if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    echo "[오류] 전체 설치는 Ubuntu x86_64 NVIDIA 컴퓨터에서 실행하세요." >&2; exit 1
  fi
  if [ ! -r /etc/os-release ]; then echo "[오류] Ubuntu 버전을 확인할 수 없습니다." >&2; exit 1; fi
  # /etc/os-release is a trusted operating-system file.
  . /etc/os-release
  if [ "${ID:-}" != ubuntu ]; then
    echo "[오류] 전체 설치는 Ubuntu x86_64 컴퓨터에서 실행하세요." >&2; exit 1
  fi
  # Check actual runtime requirements below instead of enumerating Ubuntu releases.
fi
if [ "$profile" != core ]; then
  command -v nvidia-smi >/dev/null || { echo "[오류] nvidia-smi가 없습니다. NVIDIA 드라이버를 먼저 준비하세요." >&2; exit 1; }
  nvidia-smi -L >/dev/null
fi
if ! "$skip_system" && ! "$check_only"; then
  if [ "$(id -u)" -eq 0 ]; then
    echo "[오류] 일반 사용자로 bash install.sh를 실행하세요. 시스템 패키지 설치만 sudo를 사용합니다." >&2; exit 1
  fi
  command -v sudo >/dev/null || { echo "[오류] 시스템 패키지 설치에 sudo 권한이 필요합니다." >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git python3 python3-venv build-essential \
    pkg-config coreutils procps tar gzip unzip util-linux libstdc++6 libgomp1
fi
command -v python3 >/dev/null || {
  echo "[오류] Python 3가 없습니다. --check-host/--skip-system-packages를 빼고 설치하거나 관리자가 python3를 준비하세요." >&2; exit 1;
}
exec python3 -u "$repo_dir/scripts/install_runtime.py" "${args[@]}"
