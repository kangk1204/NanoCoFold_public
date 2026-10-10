#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h || $# -eq 0 ]]; then
  echo '사용법: bash run_example.sh complex|screen|mature|design [--allow-public-msa] [--review-only] [--new-run] [--workspace DIR]'
  echo '설치가 끝난 뒤 실행하세요. screen/mature/design은 공개 MSA API 전송에 동의하는 --allow-public-msa가 필요합니다.'
  exit 0
fi
workspace=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == --workspace ]]; then
    ((i+=1))
    if [[ $i -ge ${#args[@]} || -z "${args[i]}" || "${args[i]}" == --* ]]; then
      echo '[오류] --workspace 뒤에 경로가 필요합니다.' >&2; exit 2
    fi
    workspace="${args[i]}"
  fi
done
location="$repo_dir/.nanocofold/install-location.json"
if [[ -z "$workspace" ]]; then
  if [[ ! -f "$location" ]]; then
    echo '[오류] 먼저 bash install.sh를 완료하세요.' >&2; exit 1
  fi
  workspace="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workspace"])' "$location")"
fi
if [[ -f "$location" ]]; then
  saved_gpu="$(python3 -c 'import json,pathlib,sys; d=json.load(open(sys.argv[1])); print((d.get("gpu") or "") if pathlib.Path(d["workspace"]).resolve()==pathlib.Path(sys.argv[2]).expanduser().resolve() else "")' "$location" "$workspace")"
  if [[ -n "$saved_gpu" ]]; then
    export NANOCOFOLD_GPU="$saved_gpu"
    export CUDA_VISIBLE_DEVICES="$saved_gpu"
  fi
fi
if [[ ! -x "$workspace/.venv-dev/bin/python" ]]; then
  echo '[오류] 설치된 Python 환경이 없습니다. bash install.sh를 완료하세요.' >&2; exit 1
fi
exec "$workspace/.venv-dev/bin/python" "$repo_dir/scripts/run_example.py" "${args[@]}" --workspace "$workspace"
