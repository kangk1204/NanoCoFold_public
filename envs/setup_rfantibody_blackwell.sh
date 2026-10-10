#!/usr/bin/env bash
# Tested local compatibility profile; does not modify RFantibody source/.venv.
set -euo pipefail
cd "$(dirname "$0")/.."
repo_root="$PWD"
rf_root="$repo_root/tools/RFantibody"
rf_env="$repo_root/envs/rfantibody_blackwell"
rf_revision=8fe311415754e0276d1a39c87c57e69c88927a2d

for command_name in uv git timeout sha256sum; do
  command -v "$command_name" >/dev/null || { echo "$command_name 필요"; exit 1; }
done
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  echo "이 호환 프로필은 Linux x86_64에서만 검증됐습니다."; exit 1
fi
# Never replace a real environment directory or mutate a symlinked env.
if { [ -e envs/rfantibody ] && [ ! -L envs/rfantibody ]; } || [ -L "$rf_env" ]; then
  echo "기존 환경 경로가 예상과 다릅니다. 디렉터리/심볼릭 링크를 덮어쓰지 않습니다."; exit 1
fi
mkdir -p tools envs data/frameworks
if [ ! -d "$rf_root" ]; then
  rf_stage="$(mktemp -d "$repo_root/tools/.RFantibody-clone-XXXXXX")"
  echo "[설치] RFantibody 임시 checkout: $rf_stage"
  git clone https://github.com/RosettaCommons/RFantibody.git "$rf_stage"
  git -C "$rf_stage" checkout --detach "$rf_revision"
  if [ "$(git -C "$rf_stage" rev-parse HEAD)" != "$rf_revision" ]; then
    echo "임시 checkout 버전 불일치. 확인용 파일을 보존했습니다: $rf_stage"; exit 1
  fi
  mv "$rf_stage" "$rf_root"
fi
if [ "$(git -C "$rf_root" rev-parse HEAD)" != "$rf_revision" ]; then
  echo "검증한 RFantibody revision은 $rf_revision 입니다. 기존 checkout은 변경하지 않았습니다."; exit 1
fi

# Hashes identify the locally validated public weight snapshot (not a publisher
# signature). Never replace a nonempty unrecognized user weight automatically.
verify_weight() {
  local weight_path="$1" expected_hash="$2" observed_hash
  observed_hash="$(sha256sum "$weight_path")"
  if [ "${observed_hash%% *}" != "$expected_hash" ]; then
    echo "가중치 해시 불일치: $weight_path (기존 파일 보존)" >&2
    return 1
  fi
}
# Upstream's download script skips an existing weights directory; check each
# file and promote downloads only after their expected hash matches.
mkdir -p "$rf_root/weights"
while read -r expected_hash weight; do
  # NanoCoFold runs the nanobody RF2_ab model. The separate TCR checkpoint is
  # optional and must not block installation of an unrelated VHH workflow.
  if [ "$weight" = RFab_noframework-nosidechains-5-10-23_trainingparamsadded.pt ] &&
     [ "${NANOCOFOLD_INSTALL_RFANTIBODY_TCR:-0}" != 1 ]; then
    continue
  fi
  if [ -s "$rf_root/weights/$weight" ]; then
    verify_weight "$rf_root/weights/$weight" "$expected_hash"
  else
    reused=false
    IFS=: read -r -a model_sources <<< "${NANOCOFOLD_MODEL_SOURCE_ROOTS:-}"
    for model_source in "${model_sources[@]}"; do
      candidate="$model_source/tools/RFantibody/weights/$weight"
      if [ -s "$candidate" ] && verify_weight "$candidate" "$expected_hash"; then
        ln -s "$candidate" "$rf_root/weights/$weight"
        reused=true; break
      fi
    done
    if "$reused"; then continue; fi
    command -v curl >/dev/null || { echo "가중치 다운로드에 curl이 필요합니다."; exit 1; }
    weight_url="https://files.ipd.uw.edu/pub/RFantibody/$weight"
    if [ "$weight" = RFab_noframework-nosidechains-5-10-23_trainingparamsadded.pt ]; then
      weight_url="https://zenodo.org/records/17488258/files/$weight?download=1"
    fi
    curl -fL --retry 3 "$weight_url" -o "$rf_root/weights/$weight.partial"
    verify_weight "$rf_root/weights/$weight.partial" "$expected_hash"
    mv "$rf_root/weights/$weight.partial" "$rf_root/weights/$weight"
  fi
done < "$repo_root/nanocofold/_data/rfantibody-weights.sha256"
source envs/install_locked.sh
install_locked rfantibody 3.10.20 "$rf_env"
# Bypass upstream's incompatible torch 2.3 / CUDA 11.8 metadata only here.
# No model code patches, torch.load monkeypatches, or unsafe-load env flags.
uv pip install --python "$rf_env/bin/python" --no-deps --no-sources -e "$rf_root"
DGLBACKEND=pytorch timeout 180 "$rf_env/bin/python" nanocofold/scripts/check_rfantibody_runtime.py
for binary in rfdiffusion proteinmpnn rf2 qvscorefile qvextractspecific; do
  timeout 30 "$rf_env/bin/$binary" --help >/dev/null
done
for framework in h-NbBCII10.pdb hu-4D5-8_Fv.pdb; do
  if [ ! -e "data/frameworks/$framework" ]; then
    cp "$rf_root/scripts/examples/example_inputs/$framework" "data/frameworks/$framework"
  fi
done
# Publish the default path only after CUDA/DGL checks pass. Old .venv remains.
ln -sfn "$rf_env" envs/rfantibody
echo "[완료] envs/rfantibody → envs/rfantibody_blackwell (기존 tools/RFantibody/.venv 보존)"
echo "[호환 프로필] upstream의 torch 2.3/cuda-python 11.8 metadata pin을 우회했습니다. uv pip check 경고 2개는 예상됩니다."
echo "전체 모델 검증: nanocofold doctor -c config/rfantibody_smoke.yaml --smoke --json"
