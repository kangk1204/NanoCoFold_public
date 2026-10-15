"""Reported scope and export recovery must preserve scientific receipts."""
from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from nanocofold import __version__, cli, config, hotspots, mature, rank_select, report, score_complex, truncate
from nanocofold.state import RunState
from nanocofold.publication import table_hashes


def _saved_run(tmp_path, export_status="pending"):
    cfg = config.load_config()
    cfg["run"].update(mode="screen", mock=False)
    cfg["report"].update(viewer="none", pymol_sessions=False)
    cfg["score"]["thresholds"]["ipsae"] = .6
    config.dump_resolved(cfg, tmp_path / "run_config.yaml")
    (tmp_path / "03_score").mkdir()
    (tmp_path / "05_final").mkdir()
    frame = pd.DataFrame([{"design_id": "candidate", "sequence": "ACDE", "ipsae": .7,
        "pass_ipsae": True, "final_evaluated": True, "final_eligible": True, "rank": 1,
        "composite_score": .25, "dev_cdr_evaluated": True, "selection_rank": 1}])
    frame.drop(columns="selection_rank").to_csv(tmp_path / "03_score/scored_candidates.csv", index=False)
    frame.to_csv(tmp_path / "05_final/selected_candidates.csv", index=False)
    (tmp_path / "03_score/ranking_policy.json").write_text('{"method":"saved-policy","weights":{"ipsae":1}}')
    state = RunState(tmp_path)
    state.data["execution"] = {"state": "complete", "command": "screen"}
    state.set_phase("final", {"tables_committed": True, "table_sha256": table_hashes(tmp_path),
        "exports_status": export_status})
    return cfg


@pytest.mark.parametrize("export_status", ["pending", "failed", "complete"])
def test_report_recovery_preserves_config_tables_and_ranking_policy(tmp_path, monkeypatch, export_status):
    _saved_run(tmp_path, export_status)
    protected = [tmp_path / name for name in ("run_config.yaml", "03_score/ranking_policy.json",
        "03_score/scored_candidates.csv", "05_final/selected_candidates.csv")]
    before = {p: p.read_bytes() for p in protected}
    observed = []
    monkeypatch.setattr(cli.handoff, "write_synthesis_sheet", lambda frame, cfg, *a, **k:
        observed.append(cfg["score"]["thresholds"]["ipsae"]) or {})
    monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
    args = cli.build_parser().parse_args(["report", "--run-dir", str(tmp_path),
        "--set", "score.thresholds.ipsae=0.95", "--set", "rank.weights.dev_flags=-2"])
    assert cli.cmd_report(args) == 0
    assert all(p.read_bytes() == before[p] for p in protected)
    assert observed == ([] if export_status == "complete" else [.6])
    document = (tmp_path / "report.html").read_text()
    assert "&gt;= 0.6" in document and "&gt;= 0.95" not in document


def test_report_export_recovery_requires_original_config(tmp_path, monkeypatch):
    _saved_run(tmp_path)
    (tmp_path / "run_config.yaml").unlink()
    args = cli.build_parser().parse_args(["report", "--run-dir", str(tmp_path)])
    with pytest.raises(ValueError, match="계산 당시 run_config"):
        cli.cmd_report(args)


@pytest.mark.parametrize("failure", ["export", "policy"])
def test_new_score_commit_records_actual_config_before_export_failure(tmp_path, monkeypatch, failure):
    cfg = _saved_run(tmp_path)
    previous_policy = (tmp_path / "03_score/ranking_policy.json").read_bytes()
    cfg["score"]["thresholds"]["ipsae"] = .8
    cfg["rank"]["weights"] = {"ipsae": 1}
    state = RunState(tmp_path)
    candidate = pd.DataFrame([{"design_id": "candidate", "sequence": "ACDE", "pred_dir": "prediction"}])
    monkeypatch.setattr(cli, "_score_candidates", lambda *a, **k: pd.DataFrame([
        {"design_id": "candidate", "ipsae": .9, "pass_ipsae": True, "pass_iptm": True,
         "pass_plddt": True, "pass_clash": True, "pass_hotspot": True}]))
    monkeypatch.setattr(cli, "_run_sequence_scores", lambda *a, **k: pd.DataFrame([{"design_id": "candidate"}]))
    def triage(frame, *a, **k):
        return frame.assign(dev_cdr_evaluated=True, dev_severe=0, dev_flags=0)
    monkeypatch.setattr(cli.developability, "triage", triage)
    from nanocofold import pose_integration
    monkeypatch.setattr(pose_integration, "annotate_frame", lambda frame, *a, **k: (frame, {}))
    def broken(*a, **k):
        raise OSError("injected export failure")
    if failure == "export":
        monkeypatch.setattr(cli, "_final_exports", broken)
    else:
        monkeypatch.setattr(rank_select, "ranking_policy", broken)
    with pytest.raises(OSError, match="injected"):
        cli._finalize(cfg, tmp_path, state, candidate, {}, {"CDR1": (1, 1), "CDR2": (2, 2), "CDR3": (3, 4)})
    assert yaml.safe_load((tmp_path / "run_config.yaml").read_text())["score"]["thresholds"]["ipsae"] == .8
    final = RunState(tmp_path).get_phase("final")
    assert final["tables_committed"] is True and final["exports_status"] == "failed"
    assert state.data["scoring_version"] == __version__
    if failure == "export":
        assert json.loads((tmp_path / "03_score/ranking_policy.json").read_text())["weights"] == {"ipsae": 1}
        assert final["ranking_policy_status"] == "complete"
    else:
        assert final["ranking_policy_status"] == "failed"
        assert (tmp_path / "03_score/ranking_policy.json").read_bytes() == previous_policy
        protected = {tmp_path / name: (tmp_path / name).read_bytes() for name in (
            "run_config.yaml", "03_score/scored_candidates.csv", "05_final/selected_candidates.csv")}
        monkeypatch.setattr(cli.handoff, "write_synthesis_sheet", lambda *a, **k: {})
        monkeypatch.setattr(cli.handoff, "validate_handoff", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
        # ranking_policy still raises: recovery must use saved tables, not rerank
        # or associate an older recipe with a newer committed calculation.
        assert cli.cmd_report(cli.build_parser().parse_args(["report", "--run-dir", str(tmp_path)])) == 0
        assert all(path.read_bytes() == contents for path, contents in protected.items())
        policy = json.loads((tmp_path / "03_score/ranking_policy.json").read_text())
        assert policy["method"] == "historical_policy_not_recorded"
        assert [p.read_bytes() for p in (tmp_path / "03_score/.history").rglob("ranking_policy.json")] == [previous_policy]
        assert RunState(tmp_path).get_phase("final")["ranking_policy_status"] == "not_recorded"
        assert "적용 순위 정책은 미기록" in (tmp_path / "report.html").read_text()


def test_original_tutorial_dms_size_warns_without_changing_generation_policy(tmp_path):
    from nanocofold.platform.inputs import InputManager, normalized_config
    path = Path(__file__).parents[1] / "examples/tutorial/mature_batch.json"
    original = path.read_bytes()
    entry = json.loads(original)["entries"][0]
    raw = copy.deepcopy(entry["config"])
    raw["run"]["mode"] = "mature"
    before = copy.deepcopy(raw)
    with pytest.warns(UserWarning, match="strategy=dms에서는.*size.*사용하지"):
        cfg = normalized_config(tmp_path, raw)
    assert raw == before and path.read_bytes() == original
    assert cfg["mature"]["library"]["size"] == 3
    assert cfg["mature"]["library"]["max_variants"] is None
    assert cfg["mature"]["prefilter"] == config._deep_merge(config.load_config()["mature"]["prefilter"], raw["mature"]["prefilter"])
    manager = InputManager(tmp_path)
    folder = manager.root / "dms-review"
    folder.mkdir()
    try:
        saved = manager._save(folder, raw, {"warnings": []})
    finally:
        manager.close()
    assert any("strategy=dms에서는" in value for value in saved["review"]["warnings"])
    assert json.loads((folder / "platform_input.json").read_text())["review"]["warnings"] == saved["review"]["warnings"]
    assert saved["config"]["mature"]["library"]["size"] == 3
    assert raw == before and path.read_bytes() == original
    # A small CPU library proves the advisory cannot silently cap DMS at size=3.
    library = mature.generate_library("ACDEFG", {"CDR1": (2, 3)}, cfg)
    assert len(library) > 3 and (library.variant_id == "WT").sum() == 1
    cfg["mature"]["library"]["max_variants"] = 2
    assert len(mature.generate_library("ACDEFG", {"CDR1": (2, 3)}, cfg)) == 3


@pytest.mark.parametrize("mode,strategy,expected", [
    ("mature", "dms", True), ("mature", "random", False), ("screen", "dms", False)])
def test_dms_size_advisory_is_specific_to_dms_maturation(mode, strategy, expected):
    import warnings
    cfg = config.load_config()
    cfg["run"]["mode"] = mode
    cfg["mature"]["library"].update(strategy=strategy, size=3)
    cfg["mature"]["prefilter"]["mode"] = "none"
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always")
        config._validate(cfg)
    assert any("strategy=dms에서는" in str(item.message) for item in observed) is expected


def test_allowed_korean_and_parenthesized_ids_complete_real_derived_exports(tmp_path):
    """Synthetic saved models exercise actual PML, gate, HTML and delivery writers."""
    from nanocofold import mock
    cfg = _saved_run(tmp_path, "failed")
    cfg["run"]["mock"] = True
    cfg["report"].update(pymol_sessions=True, viewer="3dmol")
    config.dump_resolved(cfg, tmp_path / "run_config.yaml")
    rows = []
    for index, identifier in enumerate(["Nb_GFP_1", "나노바디2", "VHH(A3)"], 1):
        prediction = mock.make_mock_boltz_output(tmp_path / "02_screen/predictions" / identifier,
            identifier, "A" * 20, "ACDE", [10], quality=.9)
        rows.append({"design_id": identifier, "sequence": "ACDE", "ipsae": .9,
            "pass_ipsae": True, "pass_iptm": True, "pass_plddt": True, "pass_clash": True,
            "final_evaluated": True, "final_eligible": True, "dev_cdr_evaluated": True,
            "dev_severe": 0, "dev_flags": 0, "rank": index, "selection_rank": index,
            "composite_score": .5, "engine": "boltz2", "target_chain": "A", "binder_chain": "B",
            "cif_path": str(prediction["cif"].relative_to(tmp_path)),
            "pred_dir": str(prediction["cif"].parent.relative_to(tmp_path))})
    selected = pd.DataFrame(rows)
    selected.drop(columns="selection_rank").to_csv(tmp_path / "03_score/scored_candidates.csv", index=False)
    selected.to_csv(tmp_path / "05_final/selected_candidates.csv", index=False)
    state = RunState(tmp_path)
    state.set_phase("final", {"tables_committed": True, "table_sha256": table_hashes(tmp_path), "exports_status": "failed"})
    protected = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.suffix in {".cif", ".npz"}}
    assert cli.main(["report", "--run-dir", str(tmp_path)]) == 0
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "05_final/top_candidates.pml").is_file()
    assert (tmp_path / "05_final/handoff_gate.json").is_file()
    delivery = tmp_path / "report_delivery.zip"
    assert delivery.is_file()
    with zipfile.ZipFile(io.BytesIO(delivery.read_bytes())) as archive:
        manifest = json.loads(archive.read("delivery_manifest.json"))
        assert manifest["kind"] == "presentation_only"
        assert "report.html" in archive.namelist()
    assert all(p.read_bytes() == value for p, value in protected.items())
    final = RunState(tmp_path).get_phase("final")
    assert final["exports_status"] == final["report_status"] == final["delivery_status"] == "complete"


@pytest.mark.parametrize("engine,comparison,aux,mode,expected", [
    ("colabfold", False, 1, "screen", True), ("boltz2", False, 1, "screen", False),
    ("boltz2", True, 1, "mature", True), ("colabfold", False, 0, "screen", False),
    ("colabfold", False, 1, "complex", False)])
def test_known_colabfold_multichain_limitation_warns_before_run(engine, comparison, aux, mode, expected):
    cfg = {"run": {"mode": mode}, "target": {"aux_chains": [{"sequence": "ACDE"}] * aux},
        "screen": {"engine": engine, "comparison": {"enabled": comparison, "engines": ["boltz2", "colabfold"]}}}
    warnings = score_complex.iptm_availability_warnings(cfg)
    assert bool(warnings) is expected
    if expected:
        assert "3체인 이상" in warnings[0] and "대체하지" in warnings[0]


def test_missing_pair_iptm_never_uses_global_value_and_reason_is_visible(tmp_path):
    value, source, reason = score_complex.target_binder_iptm({"iptm": .86}, "A", "B", ["A", "B", "C"])
    assert np.isnan(value) and source == "unavailable"
    row = {"design_id": "cf", "iptm": value, "complex_iptm": .86, "iptm_source": source,
           "iptm_unavailable_reason": reason, "pass_iptm": False}
    assert score_complex.pass_flags(row, {"score": {"thresholds": {"iptm": .7}}})["pass_iptm"] is False
    document = report.generate_report(tmp_path, {"run": {"mode": "screen"}, "report": {"viewer": "none"}}, {},
        scored=pd.DataFrame([row])).read_text()
    assert "쌍 ipTM 미평가 1개 모델" in document and reason in document and "0.86" in document


def test_main_report_joins_only_recorded_selection_ranks(tmp_path):
    rows = [{"design_id": "selected", "rank": 2}, {"design_id": "other", "rank": 1}]
    selected = [{"design_id": "selected", "selection_rank": 1}]
    originals = copy.deepcopy(rows)
    display = report._selection_display_records(rows, selected)
    assert display[0]["selection_rank"] == 1 and display[1]["selection_rank"] == "미선별"
    assert rows == originals
    assert report._selection_display_records(rows, [{"design_id": "selected"}])[0]["selection_rank"] == "선별 순위 미기록·대응 모호"


def test_engine_shared_ids_do_not_inherit_another_engines_selection_rank():
    rows = [{"design_id": "same", "predictor_engine": e} for e in ["boltz2", "colabfold"]]
    display = report._selection_display_records(rows, [{"design_id": "same", "predictor_engine": "colabfold", "selection_rank": 1}])
    assert display[0]["selection_rank"] == "미선별" and display[1]["selection_rank"] == 1
    ambiguous = report._selection_display_records(rows, [{"design_id": "same", "selection_rank": 1}])
    assert all(row["selection_rank"] == "선별 순위 미기록·대응 모호" for row in ambiguous)


def test_all_unassessed_cdr_rankings_and_fallback_are_explicitly_held(tmp_path):
    cfg = {"run": {"mode": "screen"}, "report": {"viewer": "none"},
        "rank": {"weights": {"dev_flags": -.4, "ipsae": 1}, "fallback_to_ineligible": True}}
    frame = pd.DataFrame([{"design_id": f"d{i}", "sequence": "ACDE", "ipsae": .6 + i * .1,
        "dev_flags": i, "dev_cdr_evaluated": False, "final_eligible": False} for i in range(3)])
    ranked = rank_select.rank_candidates(frame, cfg)
    assert ranked["composite_score"].isna().all() and ranked["rank"].isna().all()
    with pytest.warns(RuntimeWarning, match="종합점수·기록 순위 보류"):
        assert rank_select.select_final(ranked, cfg, pre_ranked=True).empty
    assert ranked["ranking_status"].eq("held").all()
    document = report.generate_report(tmp_path, cfg, {}, scored=ranked, selected=pd.DataFrame()).read_text()
    assert "종합점수·기록 순위 보류 3/3개 모델" in document
    assert "종합점수가 있는 부적격 후보에만 적용" in document


@pytest.mark.parametrize("value", [0, 1.5, -1, True, "0.85", ".nan", ".inf"])
def test_actual_cdr_cluster_identity_is_checked_before_any_inference(value):
    override = str(value).lower() if not isinstance(value, str) else json.dumps(value) if value == "0.85" else value
    with pytest.raises(ValueError, match="rank.cdr3.cluster_identity"):
        config.load_config(overrides=["rank.cdr3.cluster_identity=" + override])


def test_prepared_sequences_are_not_counted_as_predicted_models():
    rows = [{"design_id": f"source{i}", "sequence": "ACDE", "model_record": False,
             "evaluated": False, "final_eligible": None} for i in range(50)]
    rows += [{"design_id": f"model{i}", "sequence": "ACDE", "model_record": True,
              "evaluated": True, "final_eligible": i == 0} for i in range(5)]
    summary = report._report_presentation(pd.DataFrame(rows), None, {"run": {"mode": "mature"}})["summary"]
    assert summary["candidate_record_total"] == 55 and summary["preprediction_record_total"] == 50
    assert summary["model_total"] == summary["evaluated_total"] == 5
    assert summary["eligible_total"] == 1 and summary["evaluation_missing"] == 0


def test_saved_mature_report_counts_and_shows_models_before_unpredicted_library(tmp_path):
    cfg = {"run": {"mode": "mature"}, "report": {"viewer": "none", "top_n_table": 5},
        "rank": {"weights": {"dev_flags": -.4}}}
    config.dump_resolved(cfg, tmp_path / "run_config.yaml")
    library = pd.DataFrame([{"variant_id": f"v{i}", "sequence": "ACDE", "pass_llr": i >= 50} for i in range(55)])
    phase = tmp_path / "02_mature"
    phase.mkdir()
    library.to_csv(phase / "esm2_scored_library.csv", index=False)
    scored = library.iloc[-5:].assign(final_evaluated=True, final_eligible=[True, False, False, False, False],
        composite_score=[.5, .4, .3, .2, .1], rank=[1, 2, 3, 4, 5], dev_cdr_evaluated=True)
    selected = scored.iloc[:1].assign(selection_rank=1)
    for folder, name, frame in [("03_score", "scored_candidates.csv", scored), ("05_final", "selected_candidates.csv", selected)]:
        (tmp_path / folder).mkdir()
        frame.to_csv(tmp_path / folder / name, index=False)
    state = RunState(tmp_path)
    state.data["application_version"] = "0.4.1"
    state.set_phase("mature", {"evaluated": True})
    protected = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    document = report.generate_report(tmp_path, cfg, {}, scored=scored, selected=selected).read_text()
    assert '<div class="k">전체 모델 기록</div><div class="v">5</div>' in document
    assert '<div class="k">평가 완료 모델</div><div class="v">5</div>' in document
    assert "예측 전 준비·미선정 50" in document
    assert "예측 전 미선정 후보는 모델 평가 미완료 수에 합산하지" in document
    assert "서열 prior 사전필터 기준 미통과" in document
    assert "v50" in document and 'id="report-models"' in document
    assert "종합점수·기록 순위 보류 50" not in document
    assert "0.4.1" in document and __version__ in document
    assert all(p.read_bytes() == value for p, value in protected.items())


@pytest.mark.parametrize("chains", [["A", "B", "C"], ["C", "A", "B"], ["A", "B", "C", "D"]])
def test_legacy_complex_ipsae_scope_follows_saved_first_pair_without_aggregation(chains):
    pairs = [{"chain_a": chains[0], "chain_b": chains[1], "ipsae": .9},
             {"chain_a": chains[0], "chain_b": chains[2], "ipsae": .1}]
    row = {"ipsae": .9, "iptm": .86, "pair_metrics": pairs}
    display = report.metric_scope_records([row], "complex")[0]
    assert display["ipsae"] == .9 and display["iptm"] == .86
    assert [display["ipsae_chain_a"], display["ipsae_chain_b"]] == chains[:2]
    assert display["ipsae_scope_source"] == "legacy_first_recorded_pair"
    assert "iptm_scope" not in display  # Legacy mode alone cannot establish metric scope.
    assert "ipsae_scope" not in row


def test_legacy_complex_different_pair_value_does_not_get_an_invented_scope():
    display = report.metric_scope_records([{"ipsae": .8,
        "pair_metrics": [{"chain_a": "A", "chain_b": "B", "ipsae": .9}]}], "complex")[0]
    assert display["ipsae_scope"] == "not_recorded" and "ipsae_chain_a" not in display


@pytest.mark.parametrize("conservation,fill", [[np.nan, .5], [.2, .2]])
def test_hotspot_imputation_and_equal_score_order_are_recorded(monkeypatch, conservation, fill):
    table = pd.DataFrame([{"label": f"A:{i}", "seq_position": i, "relative_sasa": .7,
        "kyte_doolittle": 0., "conservation": conservation if j == 0 else np.nan,
        "plddt": 90., "dist_to_glycan": np.nan} for j, i in enumerate([3, 1, 2])])
    monkeypatch.setattr(hotspots.pdb_io, "load_structure", lambda *a: None)
    monkeypatch.setattr(hotspots.pdb_io, "get_chain", lambda *a: None)
    monkeypatch.setattr(hotspots.pdb_io, "chain_coords_by_label", lambda *a: {f"A:{i}": np.zeros(3) for i in [3, 1, 2]})
    sets = hotspots.auto_hotspot_sets(table, {"exclude_terminal": 0, "residues_per_set": [1, 2], "num_sets": 1}, "unused", "A")
    assert sets[0]["residues"] == ["A:3", "A:1"]
    parameters = sets[0]["heuristic_parameters"]
    assert parameters["conservation_imputation_value"] == fill
    assert parameters["conservation_imputed_surface_residues"] == (3 if np.isnan(conservation) else 2)
    assert parameters["residue_tie_break"] == "stable_recorded_input_residue_order"
    assert sets[0]["score"] == round(.7 + .3 + .3 * fill, 3)


def test_mature_equal_prior_scores_use_documented_library_order(tmp_path, monkeypatch):
    class TiedRng:
        def normal(self, loc, scale, size):
            return np.zeros(size)
    monkeypatch.setattr(mature.np.random, "default_rng", lambda *a: TiedRng())
    library = pd.DataFrame([{"variant_id": f"v{i}", "sequence": "ACDE"} for i in range(25)])
    cfg = {"mature": {"prefilter": {"mode": "esm2", "top_n": 5}}, "rank": {"include_wt_control": False}}
    selected, info = mature.run_prefilter(library, "ACDE", cfg, tmp_path, mock=True)
    assert list(selected.variant_id) == ["v0", "v1", "v2", "v3", "v4"]
    assert info["ranking_tie_break"] == "stable_recorded_library_row_order"


def test_internal_locks_and_outputs_regenerated_after_the_index_are_not_given_stale_sizes(tmp_path):
    for name in [".run.lock", "gpu-UUID.lock", "report.html", "report_delivery.zip", "input.fasta"]:
        (tmp_path / name).write_bytes(b"12345")
    entries = report._file_index(tmp_path)
    assert entries == [{"path": "input.fasta", "size_kb": 0., "size_bytes": 5, "kind": "fasta"}]


def test_direct_cli_version_reports_current_package_without_required_subcommand(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.build_parser().parse_args(["--version"])
    assert stop.value.code == 0 and f"NanoCoFold {__version__}" in capsys.readouterr().out


@pytest.mark.parametrize("table_state,expected", [
    ("missing", "hotspot_table.csv 기록 파일이 없습니다"),
    ("header_only", "평가 표가 0행입니다(헤더만 보존)"),
    ("corrupt", "hotspot_table.csv을 읽지 못했습니다")])
def test_automatic_hotspot_missing_empty_and_unreadable_records_are_distinct(tmp_path, table_state, expected):
    table = tmp_path / "01_target/hotspot_table.csv"
    table.parent.mkdir()
    if table_state == "header_only":
        table.write_text("label,aa\n")
    elif table_state == "corrupt":
        table.write_text('label,aa\n"unterminated')
    cfg = {"run": {"mode": "screen"}, "target": {"hotspots": "auto"}, "report": {"viewer": "none"}}
    document = report.generate_report(tmp_path, cfg, {}, scored=pd.DataFrame()).read_text()
    assert expected in document
    if table_state == "corrupt":
        assert "헤더만 보존" not in document


def test_manual_hotspots_do_not_claim_a_missing_automatic_assessment(tmp_path):
    cfg = {"run": {"mode": "screen"}, "target": {"hotspots": ["A:1"]}, "report": {"viewer": "none"}}
    document = report.generate_report(tmp_path, cfg, {}, scored=pd.DataFrame()).read_text()
    assert "자동 결합 부위를 요청했지만" not in document
    automatic_cfg = {**cfg, "target": {"hotspots": "auto"}}
    automatic = report.generate_report(tmp_path, automatic_cfg, {}, scored=pd.DataFrame()).read_text()
    assert "자동 결합 부위를 요청했지만" in automatic


def test_backtracking_author_number_rejection_explains_the_actual_boundary_without_mutation(tmp_path):
    from nanocofold import mock
    from nanocofold.utils import pdb_io
    path = mock.make_mock_target_only("AAAAAA", tmp_path / "descending.pdb")
    structure = pdb_io.load_structure(path)
    residues = list(pdb_io.iter_residues(pdb_io.get_chain(structure, "A")))
    for index, residue in enumerate(residues):
        residue.id = (" ", 20000 + index, " ")
    for number, residue in zip(range(15, 9, -1), residues):
        residue.id = (" ", number, " ")
    pdb_io.save_structure(structure, path)
    before = path.read_bytes()
    out = tmp_path / "truncated.pdb"
    with pytest.raises(ValueError, match="author 잔기 번호가 역행한 경계 5곳") as error:
        truncate.truncate_target(path, "A", ["A:15"], out, radius=100, min_segment=2)
    assert "현재 번호 정책은 역행 경계를 연결하지 않습니다" in str(error.value)
    assert "radius를 키우거나" not in str(error.value)
    assert path.read_bytes() == before and not out.exists()
