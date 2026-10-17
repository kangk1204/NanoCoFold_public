"""Engine-specific calculation receipts and partial-rescore isolation, CPU only."""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from nanocofold import cli, engine_comparison, rank_select
from nanocofold.config import load_config
from nanocofold.state import RunState


def options(comparison=True):
    cfg = load_config()
    cfg["run"].update(mode="screen", mock=True)
    cfg["screen"].update(engine="chai1", boltz2_rescore_top=2, tier2_n=4)
    cfg["screen"]["comparison"]["enabled"] = comparison
    cfg["rank"].update(weights={"iptm": 1., "ipsae": 1.}, final_n=1, diversity_pool=100)
    cfg["rank"]["cdr3"]["per_cluster"] = 10
    cfg["report"].update(viewer="none", pymol_sessions=False)
    return cfg


def frame():
    return pd.DataFrame([
        {"design_id": "c1", "sequence": "ACDE", "predictor_engine": "chai1", "iptm": .9, "ipsae": None, "final_eligible": True},
        {"design_id": "c2", "sequence": "ACDF", "predictor_engine": "chai1", "iptm": .8, "ipsae": None, "final_eligible": True},
        {"design_id": "b1", "sequence": "YYYY", "predictor_engine": "boltz2", "iptm": .4, "ipsae": .8, "final_eligible": True},
        {"design_id": "b2", "sequence": "YYYA", "predictor_engine": "boltz2", "iptm": .3, "ipsae": .6, "final_eligible": True},
    ])


def test_each_saved_engine_policy_matches_actual_weights_and_not_global_union():
    cfg, source = options(), frame()
    original = source.copy(deep=True)
    with pytest.warns(RuntimeWarning, match="ipsae"):
        ranked, selected = engine_comparison.rank_and_select(source, cfg)
    policy = rank_select.ranking_policy(cfg, ranked)
    chai, boltz = (policy["per_engine_policies"][engine] for engine in ("chai1", "boltz2"))
    assert chai["applied_weights"] == {"iptm": 1.}
    assert chai["ignored_weights"] == {"ipsae": 1.}
    assert chai["ignored_weight_reasons"]["ipsae"] == "no_finite_measurement_in_successful_prediction_cohort"
    assert boltz["applied_weights"] == {"iptm": 1., "ipsae": 1.}
    assert boltz["ignored_weights"] == {}
    assert policy["applied_weights"] == {"iptm": 1.}  # Explicit common subset, never a union.
    assert policy["weight_summary_scope"].startswith("common_to_all_engine_cohorts")
    policy = rank_select.ranking_policy(cfg, ranked, selected=selected)
    for engine, actual in ranked.groupby("predictor_engine"):
        own_cfg = copy.deepcopy(cfg)
        own_cfg["rank"]["weights"] = policy["per_engine_policies"][engine]["applied_weights"]
        expected = rank_select.rank_candidates(source[source.predictor_engine.eq(engine)], own_cfg)
        pd.testing.assert_series_equal(actual.set_index("design_id").composite_score.sort_index(),
                                       expected.set_index("design_id").composite_score.sort_index())
        assert policy["per_engine_policies"][engine]["selected_observation_count"] == 1
    assert policy["selected_observations_total"] == 2 > cfg["rank"]["final_n"]
    assert policy["final_n_scope"].startswith("per_prediction_engine_cohort")
    assert policy["combined_synthesis_order_authorized"] is False
    assert policy["cross_engine_scores_combined"] is False
    json.dumps(policy, allow_nan=False)
    pd.testing.assert_frame_equal(source, original)


def test_success_availability_normalization_and_control_counts_are_distinct():
    cfg = options()
    cfg["rank"]["weights"] = {"iptm": 1., "ipsae": 1., "dev_flags": -1., "absent": 0.}
    source = pd.DataFrame([
        {"design_id": "WT", "sequence": "ACDE", "iptm": .99, "ipsae": .99, "dev_flags": 0., "dev_cdr_evaluated": True, "final_eligible": False},
        {"design_id": "ok", "sequence": "YYYY", "iptm": .8, "ipsae": .7, "dev_flags": 1., "dev_cdr_evaluated": True, "final_eligible": True},
        {"design_id": "partial", "sequence": "ACDF", "iptm": .7, "ipsae": None, "dev_flags": 1., "dev_cdr_evaluated": True, "final_eligible": True},
        {"design_id": "cdr", "sequence": "ACDG", "iptm": .6, "ipsae": .8, "dev_flags": 1., "dev_cdr_evaluated": False, "final_eligible": True},
        {"design_id": "failed", "sequence": "ACDH", "iptm": 100., "ipsae": 100., "dev_flags": 0., "dev_cdr_evaluated": True, "final_eligible": True, "error": "failed"},
    ]).assign(predictor_engine="boltz2")
    ranked, selected = engine_comparison.rank_and_select(source, cfg)
    policy = rank_select.ranking_policy(cfg, ranked, selected=selected)["per_engine_policies"]["boltz2"]
    assert policy["cohort_counts"] == {"input_observations": 5, "prediction_failed": 1, "successful_predictions": 4,
        "normalization_observations": 2, "held_observations": 3, "control_observations": 1,
        "normalization_control_observations": 1, "quality_eligible_observations": 3, "rank_available_eligible_observations": 1}
    assert policy["normalization_observation_ids"] == ["WT", "ok"]
    assert policy["cohort_observation_ids"] == sorted(source.design_id)
    assert policy["metric_observation_counts"]["ipsae"]["finite_successful_observations"] == 3
    assert policy["metric_observation_counts"]["ipsae"]["finite_normalization_observations"] == 2
    assert policy["metric_observation_counts"]["absent"]["contribution_status"] == "zero_weight"
    assert policy["ignored_weight_reasons"]["absent"] == "metric_column_absent"
    assert selected.design_id.tolist() == ["ok"]
    held = ranked.set_index("design_id")
    assert held.loc[["partial", "cdr", "failed"], "rank"].isna().all()
    assert policy["held_reason_counts"]["prediction_failed"] == 1


def test_failed_only_measurement_does_not_activate_weight_and_all_failed_engine_survives():
    cfg, source = options(), frame()
    source.loc[0, ["ipsae", "error"]] = [100., "failed"]
    source = pd.concat([source, pd.DataFrame([{"design_id": "all-failed", "sequence": "ACDE", "predictor_engine": "opendde",
                                            "iptm": 100., "ipsae": 100., "error": "failed", "final_eligible": True}])], ignore_index=True)
    with pytest.warns(RuntimeWarning):
        ranked, selected = engine_comparison.rank_and_select(source, cfg)
    policy = rank_select.ranking_policy(cfg, ranked, selected=selected)
    assert "ipsae" in policy["per_engine_policies"]["chai1"]["ignored_weights"]
    failed = policy["per_engine_policies"]["opendde"]
    assert failed["applied_weights"] == {}
    assert failed["cohort_counts"]["successful_predictions"] == 0
    assert failed["cohort_counts"]["normalization_observations"] == 0
    assert failed["selected_observation_count"] == 0
    assert set(ranked.design_id) == set(source.design_id)
    assert ranked.loc[ranked.design_id.eq("all-failed"), "ranking_unavailable_reason"].eq("prediction_failed").all()
    assert "all-failed" not in set(selected.design_id)


def test_stable_cohort_membership_retains_duplicate_ids_and_zero_weight_missingness():
    cfg, source = options(), frame()
    cfg["rank"]["weights"] = {"iptm": 1., "ipsae": 0.}
    source.loc[1, "design_id"] = "c1"
    ranked, selected = engine_comparison.rank_and_select(source, cfg)
    repeated, picks = engine_comparison.rank_and_select(source.iloc[::-1], cfg)
    one = rank_select.ranking_policy(cfg, ranked, selected=selected)
    two = rank_select.ranking_policy(cfg, repeated, selected=picks)
    for engine in ("chai1", "boltz2"):
        assert one["per_engine_policies"][engine]["cohort_id"] == two["per_engine_policies"][engine]["cohort_id"]
        assert one["per_engine_policies"][engine]["prediction_engine_id"] == two["per_engine_policies"][engine]["prediction_engine_id"]
    assert one["per_engine_policies"]["chai1"]["cohort_observation_ids"] == ["c1", "c1"]
    assert ranked["rank"].notna().all() and len(ranked) == len(source)
    shortened = rank_select.ranking_policy(cfg, ranked[ranked.design_id.eq("c1")].head(1).drop(columns="ranking_scope"))
    assert one["per_engine_policies"]["chai1"]["cohort_id"] != shortened["cohort_id"]


@pytest.mark.parametrize("missing", [None, "", "  ", "future_unknown_engine"])
def test_unrecorded_or_unrecognized_engine_is_preserved_held_without_inference(missing):
    cfg, source = options(False), frame().rename(columns={"predictor_engine": "engine"})
    source.loc[0, "engine"] = missing
    assert engine_comparison.separate_ranking_required(source, cfg)
    with pytest.warns(RuntimeWarning, match="ipsae"):
        ranked, selected = engine_comparison.rank_and_select(source, cfg)
    row = ranked.set_index("design_id").loc["c1"]
    assert pd.isna(row["rank"]) and pd.isna(row["composite_score"]) and pd.isna(row["ranking_scope"])
    assert row["ranking_unavailable_reason"] == ("prediction_engine_unrecognized" if missing == "future_unknown_engine" else "prediction_engine_unrecorded")
    assert "c1" not in set(selected.design_id) and len(ranked) == len(source)
    policy = rank_select.ranking_policy(cfg, ranked, selected=selected)
    assert policy["unrecorded_engine_observations"] + policy["unrecognized_engine_observations"] == 1
    assert "future_unknown_engine" not in policy["per_engine_policies"]


def test_other_engine_values_never_change_partial_rescore_scores_ranks_or_selection():
    cfg, source = options(False), frame().rename(columns={"predictor_engine": "engine"})
    source["ipsae"] = source.ipsae.fillna(.5)
    before, before_picks = engine_comparison.rank_and_select(source, cfg)
    changed = source.copy()
    changed.loc[changed.engine.eq("boltz2"), "iptm"] = [.99, .98]
    after, after_picks = engine_comparison.rank_and_select(changed, cfg)
    pd.testing.assert_frame_equal(before[before.engine.eq("chai1")].reset_index(drop=True),
                                  after[after.engine.eq("chai1")].reset_index(drop=True))
    assert before_picks[before_picks.engine.eq("chai1")].design_id.tolist() == after_picks[after_picks.engine.eq("chai1")].design_id.tolist()
    assert set(before_picks.plate_id) == {"boltz2__plate_01", "chai1__plate_01"}
    policy = rank_select.ranking_policy(cfg, after, selected=after_picks)
    assert policy["method"] == "per_engine_batch_weighted_zscore_v2"
    assert "candidate subsets differ" in policy["partial_rescore_limitation"]
    assert policy["cross_engine_scores_combined"] is False


@pytest.mark.parametrize("function", [rank_select.rank_candidates, rank_select.composite_score, rank_select.select_final])
def test_combined_legacy_ranking_cannot_be_reintroduced_by_direct_call(function):
    with pytest.raises(ValueError, match="Mixed prediction engines require separate ranking cohorts"):
        function(frame(), options(False))


def test_pre_ranked_flag_cannot_authorize_combined_selection():
    ranked, _ = engine_comparison.rank_and_select(frame(), options(False))
    with pytest.raises(ValueError, match="Mixed prediction engines require separate ranking cohorts"):
        rank_select.select_final(ranked, options(False), pre_ranked=True)


def _synthetic_finalization(monkeypatch):
    def score(_cfg, candidates, _target, id_column="design_id"):
        return pd.DataFrame([{id_column: row[id_column], "iptm": .95 - int(str(row[id_column])[-1]) * .1,
                              "ipsae": .9 - int(str(row[id_column])[-1]) * .05, "n_contacts": 10,
                              "pass_iptm": True, "pass_ipsae": True, "pass_clash": True,
                              "pass_plddt": True, "pass_hotspot": True} for _, row in candidates.iterrows()])
    monkeypatch.setattr(cli, "_score_candidates", score)
    monkeypatch.setattr(cli, "_run_sequence_scores", lambda _cfg, _root, candidates, *a, **k: candidates[["design_id"]].copy())
    monkeypatch.setattr(cli.developability, "triage", lambda source, *a, **k: source.assign(dev_cdr_evaluated=True, dev_severe=0, dev_flags=0))
    from nanocofold import pose_integration
    monkeypatch.setattr(pose_integration, "annotate_frame", lambda source, *a, **k: (source, {}))
    monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
    def predict(_cfg, root, _target, candidates, *, engine, **kwargs):
        return candidates[["design_id"]].assign(pred_dir=str(root), engine=engine, target_chain="A", binder_chain="B")
    monkeypatch.setattr(cli.screen, "predict_complexes", predict)
    def render(root, *a, **k):
        path = root / "report.html"
        path.write_text("CPU synthetic fixture report; no inference")
        return path
    monkeypatch.setattr(cli.report, "generate_report", render)


@pytest.mark.parametrize("path", ["screen", "tier2"])
@pytest.mark.parametrize("rescore_top", [0, 2, 4])
def test_both_legacy_entry_points_save_actual_policy_and_safe_engine_exports(tmp_path, monkeypatch, path, rescore_top):
    _synthetic_finalization(monkeypatch)
    cfg = options(False)
    cfg["screen"]["boltz2_rescore_top"] = rescore_top
    state = RunState(tmp_path)
    # Deliberately conflicting old model provenance must be retained as input
    # history, never substituted for the actual new prediction engine.
    source = pd.DataFrame([{"design_id": f"seq{i}", "sequence": "ACDEFGHIK", "predictor_engine": "colabfold",
                            "input_predictor_engine": "opendde"} for i in range(4)])
    previous = tmp_path / "unrelated-existing-result.txt"
    previous.write_bytes(b"existing results must stay intact")
    if path == "screen":
        assert cli._screen_flow(cfg, tmp_path, state, {}, source) == 0
    else:
        cli._tier2_and_finalize(cfg, tmp_path, state, source, {}, None, "design_id", "sequence")
    assert previous.read_bytes() == b"existing results must stay intact"
    policy = json.loads((tmp_path / "03_score/ranking_policy.json").read_text())
    scored = pd.read_csv(tmp_path / "03_score/scored_candidates.csv")
    selected = pd.read_csv(tmp_path / "05_final/selected_candidates.csv")
    assert len(scored) == 4 and set(scored.design_id) == set(source.design_id)
    assert scored.predictor_engine.equals(scored.engine)
    assert scored.previous_input_predictor_engine.eq("colabfold").all()
    assert scored.input_predictor_engine.eq("opendde").all()
    assert policy["cross_engine_scores_combined"] is False
    if rescore_top == 2:
        assert policy["method"] == "per_engine_batch_weighted_zscore_v2"
        assert set(policy["per_engine_policies"]) == {"boltz2", "chai1"}
        assert all(group["cohort_counts"]["normalization_observations"] == 2 for group in policy["per_engine_policies"].values())
        assert policy["selected_observations_total"] == 2 and len(selected) == 2
        assert pd.read_csv(tmp_path / "05_final/synthesis_plate_list.csv").empty
        receipt = json.loads((tmp_path / "05_final/comparison_selection.json").read_text())
        assert receipt["requires_researcher_selection"] is True
        for engine in ("boltz2", "chai1"):
            engine_selected = pd.read_csv(tmp_path / f"05_final/engine_{engine}/selected_candidates.csv")
            assert len(engine_selected) == 1 and engine_selected.predictor_engine.eq(engine).all()
        assert any("rank.final_n은 엔진별 상한" in notice for notice in state.warnings)
    else:
        # No rescore and full replacement remain ordinary one-engine runs.
        assert policy["method"] == "within_engine_batch_weighted_zscore_v1"
        assert policy["recorded_prediction_engines"] == (["chai1"] if rescore_top == 0 else ["boltz2"])
        assert len(selected) == 1 and not pd.read_csv(tmp_path / "05_final/synthesis_plate_list.csv").empty
        assert not (tmp_path / "05_final/comparison_selection.json").exists()


def test_prediction_record_provenance_precedes_older_candidate_or_mock_backend():
    source = pd.DataFrame([{"design_id": "q1", "engine": "mock", "predictor_engine": "boltz2", "pred_dir": "mock-fixture"}])
    actual = cli._predictions_columns(source, "design_id")
    assert actual.engine.tolist() == ["mock"]
    assert actual.predictor_engine.tolist() == ["boltz2"]
    without_declared = cli._predictions_columns(source.drop(columns="predictor_engine"), "design_id")
    assert without_declared.predictor_engine.tolist() == ["mock"]


def test_mixed_finalization_never_publishes_legacy_combined_zscore(tmp_path, monkeypatch):
    _synthetic_finalization(monkeypatch)
    cfg = options(False)
    state = RunState(tmp_path)
    candidates = pd.DataFrame([{"design_id": f"seq{i}", "sequence": "ACDEFGHIK",
                                "engine": "boltz2" if i < 2 else "chai1"} for i in range(4)])
    cli._finalize(cfg, tmp_path, state, candidates, {}, publish_report=False)
    policy = json.loads((tmp_path / "03_score/ranking_policy.json").read_text())
    assert policy["cross_engine_scores_combined"] is False
    assert policy["method"] == "per_engine_batch_weighted_zscore_v2"
    assert set(policy["per_engine_policies"]) == {"boltz2", "chai1"}
    assert pd.read_csv(tmp_path / "05_final/synthesis_plate_list.csv").empty


@pytest.mark.parametrize("engines", [["chai1", "boltz2"], ["chai1", None], ["chai1", "future_engine"], [None, None], ["future_engine", "future_engine"]])
def test_historical_export_recovery_preserves_raw_tables_and_explicitly_holds_handoff(tmp_path, monkeypatch, engines):
    cfg = options(False)
    state = RunState(tmp_path)
    scored = pd.DataFrame([{"design_id": f"seq{i}", "sequence": "ACDEFGHIK", "engine": engine,
                            "composite_score": .9 - i * .1, "rank": i + 1} for i, engine in enumerate(engines)])
    selected = scored.head(1).assign(selection_rank=1)
    for path, table in [(tmp_path / "03_score/scored_candidates.csv", scored),
                        (tmp_path / "05_final/selected_candidates.csv", selected)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(path, index=False)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.csv")}
    # Even a permissive external gate cannot authorize the historical mixed
    # aggregate without an actual engine-specific recomputation.
    monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
    exports = cli._final_exports(cfg, tmp_path, state, scored, selected)
    assert exports["handoff_gate"]["ok"] is False
    assert exports["synthesis"]["requires_reanalysis"] is True
    assert exports["synthesis"]["raw_tables_preserved"] is True
    assert pd.read_csv(tmp_path / "05_final/synthesis_plate_list.csv").empty
    assert not list((tmp_path / "05_final").glob("engine_*"))
    assert all(path.read_bytes() == value for path, value in before.items())
    gate = json.loads((tmp_path / "05_final/handoff_gate.json").read_text())
    assert gate["ok"] is False
    assert any(error["code"] == "historical_engine_ranking_requires_reanalysis" for error in gate["errors"])
    assert any("구형 혼합·미확인 엔진 순위의 발주 전달을 보류" in notice for notice in state.warnings)


def test_mismatched_saved_scope_cannot_relabel_historical_global_selection(tmp_path, monkeypatch):
    cfg = options(False)
    cfg["screen"]["comparison"]["enabled"] = True
    scored = frame().assign(ranking_scope="chai1", composite_score=.1, rank=1)
    selected = scored.head(1).assign(selection_rank=1)
    monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
    exports = cli._final_exports(cfg, tmp_path, RunState(tmp_path), scored, selected)
    assert exports["handoff_gate"]["ok"] is False
    assert exports["synthesis"]["requires_reanalysis"] is True
    assert pd.read_csv(tmp_path / "05_final/synthesis_plate_list.csv").empty


def test_valid_empty_engine_selection_stays_engine_scoped_not_historical(tmp_path, monkeypatch):
    cfg, source = options(False), frame()
    source["final_eligible"] = False
    ranked, selected = engine_comparison.rank_and_select(source, cfg)
    assert selected.empty
    monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": False, "errors": [], "warnings": []})
    exports = cli._final_exports(cfg, tmp_path, RunState(tmp_path), ranked, selected)
    assert exports["synthesis"]["requires_researcher_selection"] is True
    assert "requires_reanalysis" not in exports["synthesis"]
