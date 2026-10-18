"""The shipped local cancellation dialog and handlers, exercised without jobs/GPU."""
import subprocess
from pathlib import Path


def test_local_cancellation_confirmation_handlers():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "--test", str(root / "tests/cancel_confirmation_contract.mjs")],
                   check=True, capture_output=True, text=True)
