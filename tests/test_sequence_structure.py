"""Sequence-to-coordinate mapping and navigation lifetime contracts (CPU only)."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_sequence_structure_navigation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser logic contracts")
    subprocess.run([node, "--test", str(Path(__file__).with_name("sequence_structure_contract.mjs"))],
                   check=True, capture_output=True, timeout=20)
