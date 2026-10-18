"""Reused run folders keep prior artifacts separate from current references."""
import hashlib
import json
import os
import re

import pandas as pd
import pytest

from nanocofold import report
from test_bilingual_report import ReportText


def _write(root, relative, content="preserved bytes\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _paths(group):
    return {entry["path"] for entry in group["files"]}


def _digests(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_reused_run_lists_prior_candidate_only_in_other_retained_group(tmp_path):
    active = "02_screen/NEW_CAND_X/pred.model_idx_0.cif"
    old = "02_screen/OLD_CAND/pred.model_idx_0.cif"
    failed = "02_screen/FAILED/prediction_error.txt"
    shared = "01_target/target.pdb"
    for path in (active, old, failed, shared):
        _write(tmp_path, path)
    cfg = {"run": {"mode": "screen"}, "target": {"hotspots": []}, "report": {"viewer": "none"}}
    _write(tmp_path, "run_config.yaml", "run:\n  mode: screen\ntarget:\n  hotspots: []\n")
    frame = pd.DataFrame([
        {"design_id": "NEW_CAND_X", "sequence": "ACDE", "cif_path": active,
         "pred_dir": "02_screen/NEW_CAND_X", "final_evaluated": True},
        {"design_id": "FAILED", "sequence": "ACDF", "pred_dir": "02_screen/FAILED",
         "final_evaluated": False, "error": "prediction failed"},
    ])
    score_path = tmp_path / "03_score/scored_candidates.csv"
    score_path.parent.mkdir()
    frame.to_csv(score_path, index=False)
    before = _digests(tmp_path)

    document = report.generate_report(tmp_path, cfg, {}, scored=frame).read_text()
    tables = {table["caption"].split(" (")[0]: table["rows"] for table in ReportText(document).tables}
    current = {row[0] for row in tables["현재 결과에서 참조한 파일"]}
    other = {row[0] for row in tables["추가 보존 파일"]}
    assert {active, failed, "run_config.yaml", "03_score/scored_candidates.csv"} <= current
    assert {old, shared} <= other
    assert old not in current and not current.intersection(other)
    assert all(_digests(tmp_path)[path] == digest for path, digest in before.items())
    # The inventory remains a path list; it does not promise download links.
    for scope in ("referenced", "other"):
        table = re.search(r'<table data-file-scope="' + scope + r'">(.*?)</table>', document, re.S)[1]
        assert "href=" not in table and "download=" not in table


def test_references_use_full_safe_paths_preserve_failed_and_unevaluated_files(tmp_path):
    active = "02_screen/NEW/model.cif"
    confidence = "02_screen/NEW/confidence.json"
    unevaluated = "02_design/unscored/structure.cif"
    old = "02_screen/OLD/model.cif"
    receipt = "03_score/scored_candidates.csv"
    for relative in (active, confidence, unevaluated, old, receipt):
        _write(tmp_path, relative)
    groups = report._file_groups(tmp_path, [
        {"cif_path": "/source/run/" + active, "pred_dir": "02_screen/NEW"},
        {"structure_file": unevaluated, "model_record": False},
    ], [{"path": receipt}])
    assert _paths(groups[0]) == {active, confidence, unevaluated, receipt}
    assert _paths(groups[1]) == {old}


def test_invalid_broad_and_archived_references_cannot_promote_preserved_outputs(tmp_path):
    active = "02_screen/CURRENT/model.cif"
    old = "02_screen/OLD/model.cif"
    archived = "source_snapshot/02_screen/CURRENT/model.cif"
    nested_archive = "02_screen/CURRENT/.history/model.cif"
    for relative in (active, old, archived, nested_archive):
        _write(tmp_path, relative)
    outside = _write(tmp_path.parent, "outside-audit130.cif")
    (tmp_path / "external-link.cif").symlink_to(outside)
    groups = report._file_groups(tmp_path, [
        {"cif_path": active, "pred_dir": "02_screen"},
        {"pred_dir": "02_screen/CURRENT"},
        {"structure_file": archived, "pred_dir": "."},
        {"cif_path": str(outside), "source_table": "../outside-audit130.cif"},
        {"cif_path": "external-link.cif"},
    ], [])
    assert _paths(groups[0]) == {active}
    assert _paths(groups[1]) == {old, archived, nested_archive}


def test_symlink_loop_is_skipped_without_changing_the_link_or_valid_files(tmp_path):
    valid = _write(tmp_path, "02_screen/CURRENT/model.cif")
    loop = tmp_path / "loop.cif"
    loop.symlink_to(loop.name)
    before = valid.read_bytes()
    groups = report._file_groups(tmp_path, [{"cif_path": "loop.cif"}, {"cif_path": str(valid)}], [])
    assert _paths(groups[0]) == {"02_screen/CURRENT/model.cif"}
    assert not groups[1]["files"]
    assert loop.is_symlink() and os.readlink(loop) == loop.name and valid.read_bytes() == before


def test_each_scope_has_its_own_display_limit_and_complete_count(tmp_path):
    for index in range(205):
        _write(tmp_path, f"02_screen/CURRENT/{index:03}.json")
        _write(tmp_path, f"02_screen/OLD/{index:03}.json")
    groups = report._file_groups(tmp_path, [{"pred_dir": "02_screen/CURRENT"}], [])
    assert [(group["total"], len(group["files"]), group["omitted"]) for group in groups] == [
        (205, 200, 5), (205, 200, 5)]


def test_inventory_captions_include_english_catalog_without_marking_artifact_paths(tmp_path):
    literal_id = "현재 결과에서 참조한 파일"
    _write(tmp_path, f"02_screen/{literal_id}/model.cif")
    cfg = {"run": {"mode": "screen"}, "target": {"hotspots": []}, "report": {"viewer": "none"}}
    frame = pd.DataFrame([{"design_id": literal_id, "cif_path": f"02_screen/{literal_id}/model.cif"}])
    document = report.generate_report(tmp_path, cfg, {}, scored=frame).read_text()
    catalog = json.loads(re.search(r'const NCF_TRANSLATIONS = Object.freeze\((\{.*?\})\);', document, re.S)[1])
    assert catalog[literal_id] == "Files referenced by the current result"
    assert catalog["추가 보존 파일"] == "Other retained files"
    assert f"<code>02_screen/{literal_id}/model.cif</code>" in document
    assert 'data-file-scope="referenced"' in document and 'data-file-scope="other"' in document


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Actual bilingual inventory acceptance")
@pytest.mark.parametrize("width", [390, 1400])
def test_inventory_language_roundtrip_preserves_paths_and_grouping(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright
    active, old = "02_screen/NEW/model.cif", "02_screen/OLD/model.cif"
    for relative in (active, old):
        _write(tmp_path, relative)
    cfg = {"run": {"mode": "screen"}, "target": {"hotspots": []}, "report": {"viewer": "none"}}
    path = report.generate_report(tmp_path, cfg, {}, scored=pd.DataFrame([{"design_id": "NEW", "cif_path": active}]))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("http**/*", lambda route: (requests.append(route.request.url), route.abort()))
        page.goto(path.as_uri())
        current = page.locator('[data-file-scope="referenced"]')
        retained = page.locator('[data-file-scope="other"]')
        for language, current_label, other_label in [
            ("en", "Files referenced by the current result", "Other retained files"),
            ("ko", "현재 결과에서 참조한 파일", "추가 보존 파일"),
            ("en", "Files referenced by the current result", "Other retained files"),
        ]:
            page.locator("#language-select").select_option(language)
            expect(current.locator("caption")).to_contain_text(current_label)
            expect(retained.locator("caption")).to_contain_text(other_label)
            expect(current.locator("code")).to_have_text([active])
            expect(retained.locator("code")).to_have_text([old])
        assert errors == [] and requests == []
        browser.close()
