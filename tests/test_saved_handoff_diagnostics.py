"""Generic saved diagnostic documents only; no sequences, prediction or ordering."""
import hashlib
import io
import json
import re
import zipfile

import pytest

from nanocofold.handoff_diagnostics import SOURCE, MAX_BYTES, saved_handoff_diagnostics
from nanocofold.platform.results import ResultReader


def gate():
    fields = [("missing_expression_vector", "expression.vector"),
              ("missing_experiment_assay", "experiment.assay")]
    confirmations = [("vendor_constraints_not_reviewed", "expression.vendor_constraints_reviewed"),
                     ("construct_not_reviewed", "expression.construct_reviewed"),
                     ("antigen_qc_not_confirmed", "experiment.antigen_qc_confirmed"),
                     ("controls_not_confirmed", "experiment.controls_confirmed")]
    errors = [{"code": code, "path": path, "message": f"Required handoff config must be an explicit string: {path}"}
              for code, path in fields]
    errors += [{"code": code, "path": path, "message": f"Required handoff confirmation must be true: {path}"}
               for code, path in confirmations]
    return {"ok": False, "errors": errors, "warnings": [{"code": "draft_codon_substitution",
        "message": "construct_dna is preferred-codon substitution draft DNA, not confirmed codon optimization."}]}


RECORDED = {"ok": False, "n_errors": 6, "n_warnings": 1}


def write_gate(root, value):
    path = root / SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return path


def test_saved_six_errors_and_warning_are_read_only_and_bilingual(tmp_path):
    original = gate()
    path = write_gate(tmp_path, original)
    before = path.read_bytes()
    result = saved_handoff_diagnostics(tmp_path, RECORDED)
    assert result["status"] == "blocked" and result["detail_status"] == "available"
    assert (result["n_errors"], result["n_warnings"]) == (6, 1)
    assert result["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["revalidated"] is False and result["readonly"] is True
    for key in ("errors", "warnings"):
        for raw, item in zip(original[key], result[key]):
            assert {k: item[k] for k in raw} == raw
            assert re.search("[가-힣]", item["message_i18n"]["ko"])
            assert not re.search("[가-힣]", item["message_i18n"]["en"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("recorded", [None, RECORDED, {"ok": True, "n_errors": 0, "n_warnings": 0}])
def test_missing_details_do_not_fabricate_zero_or_pass(tmp_path, recorded):
    result = saved_handoff_diagnostics(tmp_path, recorded)
    assert result["detail_status"] == "missing" and result["status"] != "passed"
    assert result["errors"] is None and result["n_errors"] is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [None, [], {}, {"ok": "true", "errors": [], "warnings": []},
    {"ok": True, "errors": [{"code": "x", "message": "saved"}], "warnings": []},
    {"ok": False, "errors": [], "warnings": []}, {"ok": True, "errors": [], "warnings": "unknown"},
    {"ok": False, "errors": [None], "warnings": []}])
def test_invalid_schema_is_unknown_not_an_approval(tmp_path, value):
    path = write_gate(tmp_path, value)
    before = path.read_bytes()
    result = saved_handoff_diagnostics(tmp_path)
    assert result["detail_status"] == "invalid" and result["status"] == "unknown"
    assert result["ok"] is None and result["errors"] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("raw", [b"{", b"\xff\xfe", b"x" * (MAX_BYTES + 1),
    b'{"ok":false,"ok":true,"errors":[],"warnings":[]}',
    b'{"ok":true,"errors":[],"warnings":[],"unknown":NaN}'])
def test_corrupt_and_oversized_documents_are_bounded_and_preserved(tmp_path, raw):
    path = tmp_path / SOURCE
    path.parent.mkdir()
    path.write_bytes(raw)
    assert saved_handoff_diagnostics(tmp_path)["detail_status"] == "invalid"
    assert path.read_bytes() == raw


def test_external_symlink_is_not_read(tmp_path):
    root = tmp_path / "run"
    (root / "05_final").mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"ok":true,"errors":[],"warnings":[]}')
    (root / SOURCE).symlink_to(outside)
    result = saved_handoff_diagnostics(root)
    assert result["reason"] == "receipt_outside_run" and result["source_sha256"] is None


def test_summary_disagreement_never_turns_into_pass(tmp_path):
    write_gate(tmp_path, {"ok": True, "errors": [], "warnings": []})
    result = saved_handoff_diagnostics(tmp_path, RECORDED)
    assert result["detail_status"] == "inconsistent" and result["status"] == "unknown"
    assert result["recorded_summary"] == RECORDED


def test_saved_pass_is_presented_without_any_validation_or_rewrite(tmp_path):
    path = write_gate(tmp_path, {"ok": True, "errors": [], "warnings": []})
    before = path.read_bytes()
    result = saved_handoff_diagnostics(tmp_path, {"ok": True, "n_errors": 0, "n_warnings": 0})
    assert result["status"] == "passed" and result["revalidated"] is False
    assert path.read_bytes() == before


def test_unknown_diagnostic_text_and_metadata_are_preserved(tmp_path):
    original = {"ok": False, "errors": [{"code": "future_code", "message": "<b>opaque</b> {0}", "row": 7}], "warnings": []}
    write_gate(tmp_path, original)
    result = saved_handoff_diagnostics(tmp_path)
    assert result["errors"][0]["message_i18n"] == {"ko": "<b>opaque</b> {0}", "en": "<b>opaque</b> {0}"}
    assert result["errors"][0]["row"] == 7


@pytest.mark.parametrize("detail", ["available", "missing", "invalid"])
def test_reader_and_delivery_keep_completion_separate_and_preserve_source(tmp_path, monkeypatch, detail):
    run = tmp_path / "saved-fixture"
    (run / "03_score").mkdir(parents=True)
    (run / "run_config.yaml").write_text('run:\n  mode: screen\n  mock: true\n')
    state = {"execution": {"state": "complete"}, "warnings": ["opaque saved warning"],
             "phases": {"final": {"handoff_gate": RECORDED}}}
    (run / "run_state.json").write_text(json.dumps(state))
    # IDs and ledger flags only; no biological sequence or coordinate fixture.
    (run / "03_score/scored_candidates.csv").write_text('design_id,final_evaluated,final_eligible\nsample-1,True,False\nsample-2,True,False\n')
    if detail == "available": write_gate(run, gate())
    elif detail == "invalid":
        path = run / SOURCE
        path.parent.mkdir()
        path.write_bytes(b"{")
    before = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob('*') if p.is_file()}
    from nanocofold import report, handoff
    monkeypatch.setattr(handoff, "validate_handoff", lambda *a, **k: pytest.fail("must not revalidate"))
    monkeypatch.setattr(report, "_resolve_viewer_asset", lambda *a, **k: (b"/* fixture */", {"ok": True}))
    reader = ResultReader(run)
    summary = reader.summary()
    assert summary["complete"] is True and summary["warnings"] == state["warnings"]
    assert summary["handoff_diagnostics"]["status"] == "blocked"
    data, _, _ = reader.export("report_zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        diagnostic = json.loads(archive.read("handoff_diagnostics.json"))
        assert diagnostic["detail_status"] == detail and diagnostic["status"] == "blocked"
        payload = json.loads(re.search(rb'<script id="result-data" type="application/json">(.*?)</script>', archive.read("report.html"), re.S)[1])
        assert payload["summary"]["handoff_diagnostics"] == diagnostic
        assert len(payload["candidates"]["rows"]) == 2
        manifest = json.loads(archive.read("delivery_manifest.json"))
        entry = next(f for f in manifest["files"] if f["path"] == "handoff_diagnostics.json")
        assert entry["sha256"] == hashlib.sha256(archive.read(entry["path"])).hexdigest()
    assert {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob('*') if p.is_file()} == before
