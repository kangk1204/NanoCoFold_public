"""Production viewer continuations must keep the same owning candidate."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_pending_focus_cannot_measure_a_different_candidate():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the actual viewer continuation contract")
    contract = Path(__file__).with_name("result_viewer_async_contract.mjs")
    subprocess.run([node, "--test", str(contract)], check=True, capture_output=True, timeout=20)
