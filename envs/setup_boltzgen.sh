#!/usr/bin/env bash
# Isolated official BoltzGen 0.3.2 with pinned CUDA 12.8 dependencies.
# Candidate environment: installation/kernel checks do not certify model quality.
set -euo pipefail
cd "$(dirname "$0")/.."
source envs/install_locked.sh
install_locked boltzgen 3.12.13
