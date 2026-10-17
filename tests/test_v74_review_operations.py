"""The v74 operational/precision fixes preserve recorded data and deletion guards."""
from pathlib import Path
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from nanocofold import __version__, rank_select, score_complex
from nanocofold.config import load_config
from nanocofold.platform.retention import RetentionError, _checked_directory


def test_metric_precision_receipt_distinguishes_recorded_pae_from_composite():
    values = np.array([[0., 1.234567], [2.345678, 0.]])
    saved = values.copy()
    metric = score_complex.interface_pae(values, {"A:1": 0, "B:1": 1}, [{"a": "A:1", "b": "B:1"}])
    assert metric == 1.79
    np.testing.assert_array_equal(values, saved)
    frame = pd.DataFrame([{"design_id": "recorded", "iptm": .8, "interface_pae": metric}])
    policy = rank_select.ranking_policy(load_config(), frame)
    assert "composite_unrounded_float64" in policy["score_precision"]
    assert "input metric precision as recorded" in policy["score_precision"]
    assert policy["source_metric_precision"]["interface_pae"] == "stored_rounded_3_decimal_angstrom"
    assert "not asserted universally unrounded" in policy["source_metric_precision"]["other_metrics"]


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_symlink_guidance_keeps_rejection_and_does_not_follow_target(tmp_path, kind):
    root = tmp_path.resolve()
    target = root / "private-sentinel"
    if kind == "directory":
        target.mkdir()
        (target / "sentinel.txt").write_text("preserved")
    else:
        target.write_text("preserved")
    alias = root / "alias"
    alias.symlink_to(target, target_is_directory=kind == "directory")
    with pytest.raises(RetentionError) as caught:
        _checked_directory(alias)
    assert "pwd -P" in str(caught.value)
    assert "/private/var/folders" in str(caught.value)
    assert str(target) not in str(caught.value)
    assert alias.is_symlink()
    assert (target / "sentinel.txt" if kind == "directory" else target).read_text() == "preserved"


def test_real_directory_still_passes_and_file_is_not_relabelled_as_a_symlink(tmp_path):
    _checked_directory(tmp_path.resolve())
    path = tmp_path / "plain-file"
    path.write_text("preserved")
    with pytest.raises(RetentionError, match="실제 디렉터리"):
        _checked_directory(path)
    assert path.read_text() == "preserved"


def test_documented_module_entry_uses_checkout_even_with_an_older_installed_path(tmp_path):
    old = tmp_path / "older-install/nanocofold"
    old.mkdir(parents=True)
    (old / "__init__.py").write_text('__version__ = "older-installed-wheel"\n')
    (old / "__main__.py").write_text('print("older-installed-wheel")\n')
    result = subprocess.run(
        [sys.executable, "-m", "nanocofold", "--version"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONPATH": str(old.parent), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "NanoCoFold " + __version__
    assert "older-installed-wheel" not in result.stdout
