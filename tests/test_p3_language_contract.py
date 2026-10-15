"""Keep the focused browser-JavaScript language regression in the pytest gate."""
import subprocess
from pathlib import Path


def test_empty_pae_language_contract():
    contract = Path(__file__).with_name("p3_empty_pae_language_contract.mjs")
    result = subprocess.run(["node", "--test", str(contract)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
