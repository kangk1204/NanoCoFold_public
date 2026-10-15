"""Report publication regressions; mock structures verify software behavior only."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import unquote

import pandas as pd
import pytest

from nanocofold import report
from nanocofold.platform.results import ResultReader
from nanocofold.publication import table_hashes
from test_audit21_presentation import saved_run


class ExplorerLink(HTMLParser):
    href = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "a" and attributes.get("id") == "complete-result-explorer":
            self.href = attributes["href"]


def payload(document):
    match = re.search(r'<script id="result-data" type="application/json">(.*?)</script>', document, re.S)
    assert match
    return json.loads(match.group(1))


def publishing_run(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    (run / "05_final").mkdir()
    frame = pd.read_csv(run / "03_score/scored_candidates.csv")
    frame.iloc[:0].to_csv(run / "05_final/selected_candidates.csv", index=False)
    state = {"execution": {"state": "running"}, "phases": {"final": {
        "tables_committed": True, "table_sha256": table_hashes(run),
        "exports_status": "complete", "report_status": "pending", "delivery_status": "pending"}}}
    (run / "run_state.json").write_text(json.dumps(state))
    return run, cfg, state


def test_report_entry_stays_small_and_separate_explorer_preserves_all_48_models(tmp_path):
    run, cfg = saved_run(tmp_path, count=48)
    # Real engine file naming is part of the association contract. A generic
    # prediction.pdb intentionally has no inferred companion PAE.
    (run / "prediction.pdb").rename(run / "colabfold_model_0.pdb")
    (run / "pae_prediction.npz").rename(run / "pae_colabfold_model_0.npz")
    rows = pd.read_csv(run / "03_score/scored_candidates.csv")
    rows["cif_path"] = "colabfold_model_0.pdb"
    rows["predictor_engine"] = "colabfold"
    rows.to_csv(run / "03_score/scored_candidates.csv", index=False)
    original = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    output = report.generate_report(run, cfg, {}, out_path=run / "review copy.html", viewer_max=1)
    document = output.read_text()
    link = ExplorerLink(); link.feed(document)
    assert link.href == "review%20copy_explorer.html"
    assert output.stat().st_size < 100_000
    assert "<iframe" not in document and "srcdoc=" not in document
    explorer = output.parent / unquote(link.href)
    complete = payload(explorer.read_text())
    assert len(complete["candidates"]["rows"]) == 48
    assert {row["candidate_id"] for row in complete["candidates"]["rows"]} == {f"model-{i}" for i in range(48)}
    assert len([s for s in complete["structures"] if s["observation_id"] is None]) == 48
    assert {s["candidate_id"] for s in complete["structures"]} == {f"model-{i}" for i in range(48)}
    assert all(len(s["pae"]["values"]) == 20 and s["content"] for s in complete["structures"])
    assert all(p.read_bytes() == data for p, data in original.items())
    # Relative link plus a self-contained explorer survives moving both files.
    portable = tmp_path / "copied elsewhere"; portable.mkdir()
    output.rename(portable / output.name)
    explorer.rename(portable / explorer.name)
    assert (portable / unquote(link.href)).read_bytes()


def test_report_snapshot_omits_its_pending_publication_but_preserves_source_state(tmp_path):
    run, cfg, state = publishing_run(tmp_path)
    before = (run / "run_state.json").read_bytes()
    assert ResultReader(run).summary()["complete"] is False
    output = report.generate_report(run, cfg, {})
    link = ExplorerLink(); link.feed(output.read_text())
    snapshot = payload((output.parent / unquote(link.href)).read_text())
    assert snapshot["summary"]["status"] == "complete"
    assert snapshot["summary"]["complete"] is True
    assert snapshot["summary"]["report_artifact"] is True
    assert snapshot["summary"]["source_report_status"] == "pending"
    assert not any("산출물 발행 미완료" in issue for issue in snapshot["summary"]["issues"])
    assert snapshot["summary"]["state"] == state
    assert snapshot["provenance"]["summary"]["complete"] is True
    assert (run / "run_state.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["exports", "report", "tables", "hash", "execution", "delivery", "model"])
def test_report_snapshot_never_promotes_failed_or_uncommitted_results(tmp_path, failure):
    run, _, state = publishing_run(tmp_path)
    final = state["phases"]["final"]
    if failure in {"exports", "report", "delivery"}:
        final[failure + "_status"] = "failed"
    elif failure == "tables":
        final["tables_committed"] = False
    elif failure == "hash":
        final["table_sha256"]["03_score/scored_candidates.csv"] = "0" * 64
    elif failure == "execution":
        state["execution"]["state"] = "interrupted"
    else:
        frame = pd.read_csv(run / "03_score/scored_candidates.csv")
        frame.loc[0, "prediction_error"] = "recorded prediction failure"
        frame.to_csv(run / "03_score/scored_candidates.csv", index=False)
        final["table_sha256"] = table_hashes(run)
    (run / "run_state.json").write_text(json.dumps(state))
    summary = ResultReader(run).summary(report_artifact=True)
    assert summary["complete"] is False
    assert summary["status"] != "complete"


def test_viewer_none_has_no_viewer_limit_notice_and_truncation_is_readable(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    cfg["report"]["viewer"] = "none"
    context = {"target_qc": {"truncation": {
        "enabled": True, "original_residues": 250, "kept_residues": 80,
        "kept_other_chain_residues": 10, "kept_ranges": ["3-82"],
        "missing_hotspots": [], "out_path": "/Users/private/research/target_prepared.pdb"}}}
    document = report.generate_report(run, cfg, context).read_text()
    assert "대표 구조 최대" not in document
    assert "설정에 따라 구조 뷰어를 표시하지 않습니다" in document
    assert "표적 구조 절단" in document and "원본 잔기 250개" in document
    assert "유지 잔기 80개" in document and "유지 구간: 3-82" in document
    truncation_row = document.split('scope="row">표적 구조 절단', 1)[1].split('</tr>', 1)[0]
    assert "Truncation" not in document and "out_path" not in truncation_row and "/Users/private" not in truncation_row
    assert not (run / "report_explorer.html").exists()


def test_linked_explorer_publication_refuses_symlink_and_preserves_previous_report(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    output = run / "report.html"; output.write_text("previous report")
    protected = tmp_path / "protected.html"; protected.write_text("keep")
    (run / "report_explorer.html").symlink_to(protected)
    with pytest.raises(OSError, match="심볼릭 링크"):
        report.generate_report(run, cfg, {})
    assert output.read_text() == "previous report"
    assert protected.read_text() == "keep"


def test_expanded_metric_scope_labels_keep_raw_record_in_tooltip_and_source(tmp_path):
    rows = pd.DataFrame([{"design_id": "one", "ipsae_scope": "selected_chain_pair",
                          "iptm_scope": "selected_target_binder_pair",
                          "iptm_source": "native_global_iptm_two_chain_complex", "iptm": .6}])
    before = rows.copy(deep=True)
    document = report.generate_report(tmp_path, {"report": {"viewer": "none"}}, {}, scored=rows).read_text()
    assert '<span title="selected_chain_pair">선택한 체인쌍</span>' in document
    assert '<span title="native_global_iptm_two_chain_complex">두 체인 복합체의 원본 전체 ipTM</span>' in document
    assert report.metric_record_label("unavailable") == "미평가"
    pd.testing.assert_frame_equal(rows, before)
