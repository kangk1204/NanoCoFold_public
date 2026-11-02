"""Beginner-facing diagnostics preserve the original records and readiness gates."""
import subprocess
from pathlib import Path


def test_diagnostics_bilingual_presentation():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['node', '--test', str(root/'tests/novice_diagnostics_contract.mjs')],
                   check=True, capture_output=True, text=True)
