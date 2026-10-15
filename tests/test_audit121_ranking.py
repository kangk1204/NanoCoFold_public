"""Optional, globally unmeasured priors must not erase a valid candidate set."""
import warnings

import numpy as np
import pandas as pd
import pytest

from nanocofold.config import load_config
from nanocofold.rank_select import rank_candidates, ranking_policy, ranking_warnings, select_final


def population():
    return pd.DataFrame([
        {"design_id": str(i), "sequence": "ACDEFG", "n_contacts": 5,
         "final_eligible": True, "iptm": .8 - i * .02, "esm_score": -float(i),
         "antifold": -float(i), "hotspot_contact_frac": .6 + i * .05}
        for i in range(6)
    ])


def config():
    cfg = load_config()
    cfg["rank"].update(weights={"iptm": 1., "esm_score": .3, "antifold": .2,
                                  "hotspot_contact_frac": .5}, final_n=10)
    cfg["rank"]["cdr3"]["per_cluster"] = 10
    return cfg


@pytest.mark.parametrize("metric", ["esm_score", "antifold", "hotspot_contact_frac", "interface_pae"])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, None, "not measured"])
def test_globally_unmeasured_metric_matches_absent_column(metric, value):
    cfg, frame = config(), population()
    if metric == "interface_pae":
        cfg["rank"]["weights"][metric] = -1.
    frame[metric] = value
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        expected = rank_candidates(frame.drop(columns=metric), cfg)
        actual = rank_candidates(frame, cfg)
    pd.testing.assert_series_equal(actual.composite_score, expected.composite_score)
    assert actual["rank"].notna().sum() == len(frame)
    assert len(select_final(actual, cfg, pre_ranked=True)) == len(frame)
    policy = ranking_policy(cfg, actual)
    assert metric not in policy["applied_weights"]
    assert policy["ignored_weights"][metric] == cfg["rank"]["weights"][metric]
    assert any(metric in notice for notice in ranking_warnings(actual, cfg))


def test_partial_missing_stays_held_and_failed_rows_never_supply_measurements():
    cfg, frame = config(), population()
    frame.loc[0, "esm_score"] = np.nan
    ranked = rank_candidates(frame, cfg).set_index("design_id")
    assert pd.isna(ranked.loc["0", "rank"])
    assert ranked.loc["0", "ranking_unavailable_reason"] == "weighted_metrics_unavailable"
    assert "0" not in select_final(ranked.reset_index(), cfg, pre_ranked=True).design_id.tolist()
    frame["esm_score"] = np.nan
    frame.loc[0, ["esm_score", "error"]] = [-1., "prediction failed"]
    ranked = rank_candidates(frame, cfg)
    assert len(select_final(ranked, cfg, pre_ranked=True)) == 5
    assert "esm_score" in ranking_policy(cfg, frame)["ignored_weights"]


def test_missing_optional_priors_do_not_bypass_contact_quality_or_failure_gates():
    cfg, frame = config(), population()
    frame[["esm_score", "antifold", "hotspot_contact_frac"]] = np.nan
    frame.loc[0, "n_contacts"] = 0
    frame.loc[1, "final_eligible"] = False
    frame.loc[2, "error"] = "prediction failed"
    ranked = rank_candidates(frame, cfg)
    selected = select_final(ranked, cfg, pre_ranked=True)
    assert set(selected.design_id) == {"3", "4", "5"}
    assert ranked.set_index("design_id").loc["0", "ranking_unavailable_reason"] == "interface_metrics_unavailable"
