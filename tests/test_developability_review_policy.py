"""Chemical categories, review holds and historical-record compatibility."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nanocofold import developability as dev, rank_select, handoff
from nanocofold.config import developability_patterns, _validate, load_config
from nanocofold.handoff_validation import _validate_row_eligibility

RANGES = {"CDR1": (1, 4), "CDR2": (5, 8), "CDR3": (9, 12)}


@pytest.fixture
def cfg(tmp_path):
    settings = load_config("config/default.yaml")
    settings["run"].update(mock=True, outdir=str(tmp_path / "runs"))
    settings["structure"]["provider"] = "mock"
    settings["report"].update(viewer="none", pymol_sessions=False)
    return settings


@pytest.mark.parametrize("motif,category", [("NG", "deamidation"), ("NS", "deamidation"), ("DG", "isomerization"), ("DS", "isomerization"), ("DP", "cleavage")])
@pytest.mark.parametrize("legacy", [False, True])
def test_chemical_sites_are_classified_once(cfg, motif, category, legacy):
    if legacy:
        cfg["developability"].update(deamidation=["NG", "NS", "DG", "DS"], isomerization=["DP", "DG"])
        cfg["developability"].pop("cleavage", None)
    before = copy.deepcopy(cfg)
    row = dev.sequence_liabilities("A" + motif + "Q" * 9, cfg, RANGES)
    assert cfg == before
    assert row["dev_severe"] == 1
    assert row["dev_warnings"] == category + "_cdr"
    assert row["dev_assessment_status"] == "screen_pass"
    evidence = json.loads(row["dev_motif_evidence"])
    chemical = [hit for hit in evidence if hit["type"] in {"deamidation", "isomerization", "cleavage"}]
    assert len(chemical) == 1
    assert chemical[0]["pos"] == 2 and chemical[0]["cdr_regions"] == ["CDR1"]
    if legacy and motif == "DG":
        assert chemical[0]["configured_types"] == ["deamidation", "isomerization"]


def test_custom_regex_overlapping_hits_and_disabled_patterns(cfg):
    cfg["developability"].update(deamidation=["D[GS]", "N[GS]"], isomerization=["DG"], cleavage=[], oxidation=[], glycosylation="")
    row = dev.sequence_liabilities("ADGDGQQQQQQQ", cfg, RANGES)
    hits = json.loads(row["dev_motif_evidence"])
    assert [(h["type"], h["pos"]) for h in hits] == [("isomerization", 2), ("isomerization", 4)]
    assert row["dev_severe"] == 1  # categories, not number of sites
    cfg["developability"].update(deamidation=[], isomerization=[])
    assert dev.sequence_liabilities("ADGDGQQQQQQQ", cfg, RANGES)["dev_severe"] == 0


def test_cleavage_pattern_validation(cfg):
    cfg["developability"]["cleavage"] = "DP"
    with pytest.raises(ValueError, match="cleavage"):
        developability_patterns(cfg)


def test_boundary_overlap_is_preserved_not_redefined_to_make_candidates_pass(cfg):
    row = dev.sequence_liabilities("GVAALYQQQQQQ", cfg, {"CDR1": (5, 6), "CDR2": (8, 9), "CDR3": (11, 12)})
    assert row["dev_hydrophobic_patch_cdr"] is True
    assert json.loads(row["dev_hydrophobic_evidence"])[0]["cdr_overlap"] == ["CDR1"]
    assert row["dev_evidence_level"] == "sequence_screen"


def test_missing_cdr_is_unassessed_not_a_zero_risk_pass(cfg):
    row = dev.sequence_liabilities("ADGQQQQQQQQQ", cfg)
    assert row["dev_assessment_status"] == "unassessed"
    assert row["dev_severe_unevaluated"] == 4
    assert dev.selection_held(row)


def held_row(cfg):
    row = dev.sequence_liabilities("ADGQDPQQQQQQ", cfg, RANGES)
    assert cfg["rank"]["max_dev_severe"] == 1
    assert row["dev_severe"] == 2
    return {**row, "design_id": "candidate", "sequence": "ADGQDPQQQQQQ", "composite_score": 1.0,
            "rank": 1, "cluster_id": "c1", "final_eligible": False, "ipsae": .8, "iptm": .8}


@pytest.mark.parametrize("identifier", ["WT", "candidate", "another_candidate"])
def test_common_or_wt_findings_do_not_grant_exemptions(cfg, identifier):
    row = {**held_row(cfg), "design_id": identifier}
    assert dev.gate_reason(row, 1) == "developability_review_required"
    assert row["dev_assessment_status"] == "review_required"
    assert dev.selection_held(row)


@pytest.mark.parametrize("fallback", [False, True])
def test_review_hold_survives_fallback_and_zero_rank_weight(cfg, fallback):
    cfg["rank"]["weights"] = {"ipsae": 1, "dev_flags": 0}
    row = held_row(cfg)
    # Even a contradictory eligibility flag cannot bypass the recorded hold.
    row["final_eligible"] = True
    selected = rank_select.select_final(pd.DataFrame([row]), cfg, allow_fallback=fallback, pre_ranked=True)
    assert selected.empty


def test_passed_screen_still_needs_structural_eligibility(cfg):
    cfg["rank"]["weights"] = {"ipsae": 1}
    row = {**held_row(cfg), **dev.sequence_liabilities("ADGQQQQQQQQQ", cfg, RANGES)}
    assert not dev.selection_held(row)
    assert rank_select.select_final(pd.DataFrame([row]), cfg, pre_ranked=True).empty
    row["final_eligible"] = True
    assert len(rank_select.select_final(pd.DataFrame([row]), cfg, pre_ranked=True)) == 1


@pytest.mark.parametrize("count", [None, np.nan, np.inf, -1, 1.5, True, "no"])
def test_invalid_counts_do_not_pass(count):
    assert dev.gate_reason({"dev_severe": count, "dev_assessment_policy": dev.ASSESSMENT_POLICY}, 1) == "developability"


def test_legacy_records_are_not_reinterpreted(cfg):
    old = {"dev_severe": 2, "dev_warnings": "deamidation_cdr;hydrophobic_patch_cdr"}
    before = copy.deepcopy(old)
    assert dev.gate_reason(old, 1) == "developability"
    assert not dev.selection_held(old)
    assert old == before
    assert not dev.selection_held({**old, "dev_assessment_policy": pd.NA})


def test_handoff_rejects_review_hold_even_if_eligibility_is_forged(cfg, tmp_path):
    row = {**held_row(cfg), "final_eligible": True}
    with pytest.raises(ValueError, match="Candidates awaiting developability review"):
        handoff.write_synthesis_sheet(pd.DataFrame([row]), cfg, tmp_path / "handoff")
    assert not (tmp_path / "handoff").exists()
    errors = []
    _validate_row_eligibility(pd.DataFrame([row]), errors)
    assert "developability_review_required" in {item["code"] for item in errors}


@pytest.mark.parametrize("replacement", [{"dev_screen_limit": None}, {"dev_assessment_status": None}, {"dev_assessment_status": "screen_pass"}, {"dev_cdr_evaluated": False}])
def test_incomplete_or_inconsistent_policy_receipts_are_held(cfg, replacement):
    assert dev.selection_held({**held_row(cfg), **replacement})


def test_new_defaults_validate_and_do_not_relax_thresholds(cfg):
    _validate(cfg)
    assert cfg["rank"]["max_dev_severe"] == 1
    assert cfg["developability"]["hydrophobic_mean_kd"] == 1.6
    assert cfg["developability"]["hydrophobic_window"] == 5
    assert cfg["developability"]["max_cys"] == 2


def test_bilingual_review_reason_and_evidence_are_present():
    root = Path(__file__).parents[1] / "nanocofold"
    js = (root / "templates/platform.js").read_text()
    i18n = (root / "templates/platform-i18n.js").read_text()
    assert 'developability_review_required:' in js
    assert 'Sequence review required · Automatic selection held' in i18n
    assert 'Surface hydrophobicity, degradation and aggregation require separate assessment.' in i18n
    assert 'v2 sequence review holds' in i18n


@pytest.mark.parametrize("fallback", [False, True])
def test_finalize_records_review_hold_with_strong_structure_scores(cfg, tmp_path, monkeypatch, fallback):
    from nanocofold import cli
    from nanocofold.state import RunState
    cfg['rank']['weights'] = {'ipsae': 1, 'dev_flags': 0}
    cfg['rank']['fallback_to_ineligible'] = fallback
    cfg['rank']['pose_assessment']['enabled'] = False
    root = tmp_path / 'run'
    root.mkdir()
    candidates = pd.DataFrame([{'design_id': 'review', 'sequence': 'ADGQDPQQQQQQ'}])
    metrics = pd.DataFrame([{'design_id': 'review', 'ipsae': .9, 'iptm': .9,
                            'pass_clash': True, 'pass_ipsae': True, 'pass_iptm': True, 'pass_plddt': True,
                            'interface_plddt': 95, 'n_clashes': 0}])
    monkeypatch.setattr(cli, '_score_candidates', lambda *a, **k: metrics)
    monkeypatch.setattr(cli, '_run_sequence_scores', lambda *a, **k: pd.DataFrame({'design_id':['review'], 'esm_mean_logprob':[0.]}))
    cli._finalize(cfg, root, RunState(root), candidates, {'hotspot_labels':[], 'hotspot_seq_labels':[]}, cdr_ranges=RANGES)
    saved = pd.read_csv(root / '03_score/scored_candidates.csv')
    assert saved.iloc[0]['exclusion_reason'] == 'developability_review_required'
    assert not bool(saved.iloc[0]['final_eligible'])
    assert pd.read_csv(root / '05_final/selected_candidates.csv').empty
    assert pd.read_csv(root / '05_final/synthesis_plate_list.csv').empty
    assert '서열 검토 보류' in (root / 'run_state.json').read_text()
    assert '서열 검토 필요' in (root / 'report.html').read_text()


def test_recorded_review_hold_is_not_removed_by_a_different_current_limit(cfg):
    row = held_row(cfg)
    assert dev.gate_reason(row, 100) == 'developability_review_required'


def test_review_caption_does_not_hide_structural_failure_or_relabel_history(cfg):
    row = {**held_row(cfg), 'exclusion_reason':'developability_review_required'}
    assert dev.review_only_hold(row)
    assert not dev.review_only_hold({**row, 'exclusion_reason':'clash;developability_review_required'})
    assert not dev.review_only_hold({**row, 'dev_assessment_policy':None})
