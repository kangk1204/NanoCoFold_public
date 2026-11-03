"""Generic viewer scheduling contracts, without coordinates or predictions."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_inactive_layers_do_not_block_viewer_readiness():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the viewer scheduling contract")
    contract = Path(__file__).with_name("result_molstar_readiness_contract.mjs")
    subprocess.run([node, "--test", str(contract)], check=True, capture_output=True, timeout=20)
