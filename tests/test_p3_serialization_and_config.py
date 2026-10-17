"""CPU regressions for generated reference metadata, config reads and strict JSON."""
from __future__ import annotations

import copy
import json
import warnings

import numpy as np
import pytest

from nanocofold import config, score_complex
from nanocofold.utils.atomic import write_json
from nanocofold.utils.misc import to_native


def test_generated_reference_selection_metadata_survives_strict_config_validation():
    cfg = config.load_config()
    receipt = {"kind": "upload", "model_index": 2, "original_models": 3,
               "original_filename": "reference.cif"}
    cfg["complex"] = {"reference_model_selection": receipt}
    original = copy.deepcopy(cfg)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        config._validate(cfg)
    assert cfg == original


def test_reference_metadata_allowance_does_not_silence_misspelled_options():
    cfg = config.load_config()
    cfg["complex"] = {"reference_model_selectoin": {"model_index": 2}}
    with pytest.warns(UserWarning, match=r"complex\.reference_model_selectoin"):
        config._validate(cfg)


@pytest.mark.parametrize("cascade,engines,expected", [
    (True, None, True), (False, ["boltz2", "chai1"], False),
    (False, ["colabfold", "boltz2"], True),
])
def test_multichain_iptm_warning_uses_only_supported_engine_settings(monkeypatch, cascade, engines, expected):
    cfg = config.load_config(overrides=["run.mode=screen", "screen.engine=colabfold"])
    cfg["screen"]["cascade"]["enabled"] = cascade
    if engines:
        cfg["screen"]["comparison"].update(enabled=True, engines=engines)
    config._validate(cfg)
    missing = object()
    def known_setting(mapping, dotted, default=None):
        value = config.cfg_get(mapping, dotted, missing)
        assert value is not missing, "Warning lookup is outside the supported settings: " + dotted
        return value
    monkeypatch.setattr(score_complex, "cfg_get", known_setting)
    messages = score_complex.iptm_availability_warnings(cfg, target_chain_count=2)
    assert len(messages) == int(expected)
    if expected:
        assert "complex_iptm" in messages[0] and "ColabFold" in messages[0]
    assert score_complex.iptm_availability_warnings(cfg, target_chain_count=1) == []


@pytest.mark.parametrize("values,expected", [
    (np.array(np.nan), None),
    (np.array([1.25, np.nan, np.inf, -np.inf]), [1.25, None, None, None]),
    (np.array([[np.inf, 2.5], [np.nan, -np.inf]]), [[None, 2.5], [None, None]]),
    (np.array([True, False]), [True, False]),
    (np.array(7, dtype=np.int64), 7),
])
def test_nested_arrays_publish_strict_json_without_mutating_measurements(tmp_path, values, expected):
    before = values.copy()
    output = tmp_path / "receipt.json"
    payload = {"observations": [{"values": values}], "finite": np.float64(.123456789012345)}
    write_json(output, to_native(payload))
    decoded = json.loads(output.read_text(), parse_constant=lambda token: pytest.fail("Non-JSON value: " + token))
    assert decoded == {"observations": [{"values": expected}], "finite": .123456789012345}
    np.testing.assert_array_equal(values, before)
