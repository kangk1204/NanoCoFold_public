"""Impossible saved measurements cannot become positive quality evidence."""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd
import pytest

from nanocofold import score_complex, specificity
from nanocofold.config import load_config


def observation():
    return {"ipsae": .8, "iptm": .9, "interface_plddt": 90.,
            "interface_pae": 3., "hotspot_contact_frac": 1., "n_clashes": 0}


@pytest.mark.parametrize("metric,invalid,flag", [
    ("ipsae", 1.01, "pass_ipsae"), ("iptm", 1.01, "pass_iptm"),
    ("interface_plddt", 100.01, "pass_plddt"),
    ("hotspot_contact_frac", 1.01, "pass_hotspot"),
    ("n_clashes", -1, "pass_clash"), ("n_clashes", .5, "pass_clash"),
    ("ipsae", np.bool_(True), "pass_ipsae"),
    ("n_clashes", np.bool_(False), "pass_clash"),
    ("iptm", 10 ** 1000, "pass_iptm"),
    ("hotspot_contact_frac", 10 ** 1000, "pass_hotspot"),
])
def test_impossible_measurement_never_passes_quality_gate(metric, invalid, flag):
    record = observation()
    record[metric] = invalid
    assert score_complex.pass_flags(record, load_config())[flag] is False
    assert not specificity.observation_metrics_complete(record)
    assert record[metric] is invalid  # No repair or clipping of the source record.


@pytest.mark.parametrize("metric,invalid", [
    ("ipsae", -.1), ("iptm", -.1), ("interface_plddt", -.1),
    ("interface_pae", -1.), ("hotspot_contact_frac", -.1),
    ("interface_pae", float("inf")), ("interface_pae", "corrupt"),
])
def test_specificity_requires_physically_possible_measurements(metric, invalid):
    record = observation()
    record[metric] = invalid
    assert not specificity.observation_metrics_complete(record)


@pytest.mark.parametrize("missing", [None, float("nan"), pd.NA])
def test_observed_zero_contacts_preserves_undefined_interface_values(missing):
    record = {**observation(), "n_contacts": 0, "interface_plddt": missing,
              "interface_pae": missing}
    assert specificity.observation_metrics_complete(record)
    assert score_complex.pass_flags(record, load_config())["pass_plddt"] is False


@pytest.mark.parametrize("metric,invalid", [
    ("interface_pae", -1), ("interface_pae", float("inf")),
    ("interface_plddt", 101), ("interface_plddt", True),
    ("interface_pae", "corrupt"),
])
def test_zero_contacts_does_not_hide_corrupt_interface_measurements(metric, invalid):
    record = {**observation(), "n_contacts": 0, "interface_plddt": None,
              "interface_pae": None, metric: invalid}
    assert not specificity.observation_metrics_complete(record)


@pytest.mark.parametrize("metric,invalid", [
    ("ipsae", 2), ("iptm", 2), ("interface_plddt", 200),
    ("interface_pae", -3), ("hotspot_contact_frac", 2), ("n_clashes", -1),
])
def test_saved_paired_comparison_holds_invalid_metrics_and_preserves_records(metric, invalid):
    cfg = load_config()
    cfg["specificity"].update(engine="boltz2", context="isolated")
    cfg["run"]["mock"] = True
    candidates = pd.DataFrame([{"design_id": "fixture", "sequence": "ACDE"}])
    records = pd.DataFrame([
        {**observation(), "design_id": "fixture", "engine": "boltz2",
         "status": "complete", "restraint": "none", "target_state": state,
         "seed": seed, "comparison_conditions": "a" * 64,
         "sequence_sha256": hashlib.sha256(b"ACDE").hexdigest(),
         "context": "isolated", "evidence_type": "mock"}
        for state in specificity.STATES for seed in [0, 1, 2]
    ])
    records.loc[0, metric] = invalid
    before = records.copy(deep=True)
    row = specificity.aggregate(candidates, records, cfg, [0, 1, 2],
                                expected_conditions="a" * 64).iloc[0]
    assert not row.specificity_complete and not row.positive_quality_pass
    assert row.comparison_status == "insufficient" and math.isnan(row.delta_ipsae)
    pd.testing.assert_frame_equal(records, before)


def test_valid_metric_boundaries_keep_the_existing_threshold_behavior():
    record = {"ipsae": 1., "iptm": 1., "interface_plddt": 100.,
              "interface_pae": 0., "hotspot_contact_frac": 1., "n_clashes": 0.}
    flags = score_complex.pass_flags(record, load_config())
    assert all(flags[key] for key in ("pass_ipsae", "pass_iptm", "pass_plddt", "pass_hotspot", "pass_clash"))
    assert specificity.observation_metrics_complete(record)
