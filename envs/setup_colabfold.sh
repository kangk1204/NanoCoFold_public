#!/usr/bin/env bash
# Optional AlphaFold2-Multimer screening engine, isolated from PyTorch tools.
set -euo pipefail
cd "$(dirname "$0")/.."
source envs/install_locked.sh
install_locked colabfold 3.12.13
