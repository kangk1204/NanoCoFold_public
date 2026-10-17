"""Small mock runs, truthful CDR guidance and counted scoring; no native tools."""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest
import yaml

from nanocofold import cli, config, console_progress, pose_integration, score_complex
from nanocofold.state import RunState
from nanocofold.utils.shell import ExecutionCancelled


@pytest.mark.parametrize("cdr_weight, expected", [(-.2, "held"), (0., "ranked")])
@pytest.mark.parametrize("fallback", [False, True])
def test_cdr_guidance_distinguishes_ranking_weight_from_final_selection(tmp_path, monkeypatch, cdr_weight, expected, fallback):
    cfg = config.load_config()
    cfg["run"].update(mock=True, mode="screen")
    cfg["rank"]["weights"] = {"iptm": 1., "dev_flags": cdr_weight}
    cfg["rank"]["fallback_to_ineligible"] = fallback
    cfg["report"].update(viewer="none", pymol_sessions=False)
    source = pd.DataFrame([{"design_id": "q1", "sequence": "ACDEFG", "engine": "mock"}])
    monkeypatch.setattr(cli, "_score_candidates", lambda *a, **k: pd.DataFrame([
        {"design_id": "q1", "iptm": .8, "pass_iptm": True, "pass_ipsae": True, "pass_clash": True, "pass_plddt": True}]))
    monkeypatch.setattr(cli, "_run_sequence_scores", lambda *a, **k: source[["design_id"]].copy())
    monkeypatch.setattr(cli.developability, "triage", lambda rows, *a, **k: rows.assign(dev_cdr_evaluated=False, dev_severe=0, dev_flags=0))
    monkeypatch.setattr(pose_integration, "annotate_frame", lambda rows, *a, **k: (rows, {}))
    monkeypatch.setattr(cli, "_final_exports", lambda *a, **k: {"synthesis": {}, "pymol_script": None, "handoff_gate": {}, "gate_blocks": False})
    state = RunState(tmp_path)
    result = cli._finalize(cfg, tmp_path, state, source, {}, publish_report=False)
    row = result["scored"].iloc[0]
    assert row.ranking_status == expected
    assert not row.final_eligible and "developability_unassessed" in row.exclusion_reason
    assert result["selected"].empty is not (fallback and cdr_weight == 0.)
    if not result["selected"].empty:
        assert not result["selected"].final_eligible.any()
    message = next(value for value in state.warnings if "CDR 의존 liability" in value)
    assert "rank.weights.dev_flags가 0이 아니면 CDR 미평가로 종합 순위를 보류" in message
    assert "rank.weights.dev_flags=0으로 바꾸어도 CDR 평가가 없으면 최종 적격은 통과하지 않으며, 기본 선별은 계속 보류" in message
    assert any("부적격 진단 목록은 발주 승인이 아닙니다" in notice for notice in state.warnings) is fallback


def test_mature_mock_plan_reports_actual_count_and_preserves_explicit_configuration(tmp_path, monkeypatch, capsys):
    cfg = config.load_config()
    cfg["run"]["mock"] = True
    cfg["screen"]["engine"] = "chai1"
    cfg["mature"]["library"]["max_variants"] = 17
    cfg["mature"]["prefilter"]["top_n"] = 9
    before = copy.deepcopy(cfg)
    candidates = pd.DataFrame([{"design_id": f"q{i}", "sequence": "ACDE"} for i in range(9)])
    def predict(applied, _root, _target, actual, **kwargs):
        assert applied == before and len(actual) == 9
        assert "평가 예정 후보: 9개" in capsys.readouterr().out
        return actual[["design_id"]].assign(pred_dir="owned", engine="mock", target_chain="A", binder_chain="B")
    monkeypatch.setattr(cli.screen, "predict_complexes", predict)
    monkeypatch.setattr(cli, "_finalize", lambda *a, **k: {})
    assert cli._mature_flow(cfg, tmp_path, RunState(tmp_path), {}, candidates, {}) == 0
    assert cfg == before
    defaults = config.load_config()
    assert defaults["mature"]["prefilter"]["top_n"] == 200
    assert defaults["mature"]["library"]["max_variants"] is None


@pytest.mark.parametrize("signal", [None, ExecutionCancelled("cancel fixture")])
def test_each_candidate_uses_current_total_activity_and_restores_outer_context(tmp_path, monkeypatch, capsys, signal):
    model = tmp_path / "synthetic-coordinate.cif"
    model.write_text("fixture path; native parser is replaced")
    source = pd.DataFrame([
        {"design_id": "first", "sequence": "ACDE", "pred_dir": str(model)},
        {"design_id": "failed", "sequence": "ACDE", "prediction_status": "failed", "prediction_error": "fixture failure"},
    ])
    monkeypatch.setattr(cli.scorer, "find_prediction_files", lambda *a, **k: score_complex.PredictionFiles(model))
    monkeypatch.setattr(cli, "_prediction_hotspot_labels", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_validate_candidate_prediction_sequences", lambda *a, **k: None)
    observed = []
    def scoring(*a, **kwargs):
        observed.append(console_progress._ACTIVE.get().activity[0])
        if signal:
            raise signal
        return {"iptm": .8}
    monkeypatch.setattr(cli.scorer, "score_complex", scoring)
    with console_progress.ConsoleProgress("owned synthetic scoring", interval=60) as monitor:
        with console_progress.activity("outer activity"):
            if signal:
                with pytest.raises(ExecutionCancelled):
                    cli._score_candidates({}, source, {})
            else:
                result = cli._score_candidates({}, source, {})
                assert result.iloc[0].iptm == .8
                assert result.iloc[1].error == "fixture failure"
            assert monitor.activity[0] == "outer activity"
        assert monitor.activity is None
    assert observed == ["후보 1/2 점수 계산 중 · first"]
    displayed = capsys.readouterr().err
    assert "후보 1/2 점수 계산 중 · first" in displayed
    assert ("후보 2/2 점수 계산 중 · failed" in displayed) is not bool(signal)
    assert console_progress._ACTIVE.get() is None


def test_bilingual_readmes_share_same_explicit_small_mature_command():
    root = Path(cli.__file__).resolve().parents[1]
    marker = "nanocofold run --mode mature --mock"
    commands = []
    for name in ("README.md", "README.ko.md"):
        text = (root / name).read_text()
        command = text[text.index(marker):].split("```", 1)[0]
        assert "--set mature.library.max_variants=3" in command
        assert "--set mature.prefilter.top_n=2" in command
        assert "--wt-fasta examples/tutorial/wt_vhh.fasta" in command
        assert "--set target.fasta=examples/tutorial/target.fasta" in command
        commands.append(command)
    assert commands[0] == commands[1]


@pytest.mark.parametrize("entry", ["run", "mature"])
def test_both_mature_entry_points_announce_library_and_prefilter_activities(tmp_path, monkeypatch, capsys, entry):
    cfg = config.load_config()
    cfg["run"].update(mode="mature", mock=True, name="activity_" + entry, outdir=str(tmp_path / "runs"))
    cfg["target"].update(sequence="ACDEFGHIKLMNPQRSTVWY", fasta=None, pdb=None)
    cfg["mature"].update(wt_sequence="ACDEFGHIKLMNPQRSTVWY" * 6, wt_fasta=None)
    cfg["mature"]["cdr"]["source"] = "config"
    cfg["mature"]["library"]["max_variants"] = 3
    cfg["mature"]["prefilter"]["top_n"] = 2
    path = tmp_path / "owned-config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    original = path.read_bytes()
    observed = []
    monkeypatch.setattr(cli, "_target_phase", lambda *a, **k: {"sequence": cfg["target"]["sequence"]})
    monkeypatch.setattr(cli, "_mature_flow", lambda applied, _root, _state, _target, candidates, _cdr:
                        observed.append((copy.deepcopy(applied), candidates.copy())) or 0)
    args = [entry, "--config", str(path), "--mock"]
    if entry == "run":
        args.extend(["--mode", "mature"])
    assert cli.main(args) == 0
    assert len(observed) == 1 and len(observed[0][1]) <= 2
    assert observed[0][0]["mature"]["library"]["max_variants"] == 3
    assert observed[0][0]["mature"]["prefilter"]["top_n"] == 2
    assert path.read_bytes() == original
    displayed = capsys.readouterr().err
    assert "성숙화 변이 라이브러리 준비 중" in displayed
    assert "성숙화 라이브러리 4개에서 평가 후보 선별 중" in displayed
