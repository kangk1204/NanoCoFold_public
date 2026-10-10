#!/usr/bin/env bash
# Source this helper from a profile script after changing to the workspace.
set -euo pipefail
install_locked() {
  local engine="$1" python_version="$2" destination="${3:-envs/$1}"
  local locks="$PWD/nanocofold/_data/locks"
  command -v uv >/dev/null || { echo "uv가 필요합니다."; return 1; }
  [ -f "$locks/$engine.txt" ] || { echo "의존성 lock 없음: $locks/$engine.txt"; return 1; }
  if [ -L "$destination" ]; then
    echo "기존 심볼릭 링크 환경은 수정하지 않습니다: $destination" >&2; return 1
  fi
  if [ ! -x "$destination/bin/python" ]; then
    uv venv --python "$python_version" "$destination"
  fi
  local options=(--python "$destination/bin/python" --no-deps --require-hashes -r "$locks/$engine.txt")
  uv pip install "${options[@]}"
  if [ -f "$locks/$engine-torch.txt" ]; then
    # PyTorch also hosts repackaged packages with the same public versions but
    # different hashes. Keep that index restricted to the torch family.
    uv pip install --python "$destination/bin/python" --no-deps --require-hashes \
      -r "$locks/$engine-torch.txt" --index-url https://download.pytorch.org/whl/cu128
  fi
}
