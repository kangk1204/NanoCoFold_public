"""Run every shipped Node contract, including contracts without a dedicated wrapper."""
from pathlib import Path
import shutil
import subprocess


def test_all_node_contracts():
    node = shutil.which("node")
    assert node, "Node.js is required for the shipped UI contracts"
    root = Path(__file__).resolve().parents[1]
    contracts = sorted((root / "tests").glob("*_contract.mjs"))
    assert contracts
    result = subprocess.run([node, "--test", *map(str, contracts)], cwd=root,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
