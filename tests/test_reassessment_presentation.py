"""Recorded scope, outcome, and original-coordinate contracts in supplied HTML."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from html.parser import HTMLParser
import shutil
import subprocess
from urllib.parse import unquote

import numpy as np
import pandas as pd
import pytest
import yaml

from nanocofold import mock, report
from nanocofold.platform.results import ResultReader


def test_scope_and_coordinate_javascript_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to execute the supplied presentation helpers")
    subprocess.run([node, "--test", str(Path(__file__).with_name("metric_scope_coordinate_contract.mjs"))], check=True)


@pytest.mark.parametrize("scopes,caption", [
    (["whole_complex"], "전체 복합체 ipTM"),
    (["selected_target_binder_pair"], "표적–VHH ipTM"),
    ([None], "ipTM · 적용 범위 미기록"),
    (["whole_complex", None], "ipTM · 적용 범위 혼합"),
])
def test_static_report_caption_preserves_values_and_unknown_scope(tmp_path, scopes, caption):
    rows = [{"candidate_id": f"m{i}", "predictor_engine": "boltz2", "evaluated": True,
             "iptm": .8123456789, "ipsae": .7123456789, **({"iptm_scope": scope} if scope else {})}
            for i, scope in enumerate(scopes)]
    frame = pd.DataFrame(rows); before = frame.copy(deep=True)
    path = report.generate_report(tmp_path, {"run": {"mode": "complex"}, "report": {"viewer": "none"}}, {}, scored=frame)
    document = path.read_text()
    class MetricHeaders(HTMLParser):
        def __init__(self):
            super().__init__()
            self.current = None
            self.headers = []

        def handle_starttag(self, tag, attrs):
            if tag == "th" and dict(attrs).get("data-column-key") == "iptm":
                assert dict(attrs).get("scope") == "col"
                self.current = []

        def handle_data(self, data):
            if self.current is not None:
                self.current.append(data)

        def handle_endtag(self, tag):
            if tag == "th" and self.current is not None:
                self.headers.append("".join(self.current).strip())
                self.current = None

    headers = MetricHeaders()
    headers.feed(document)
    assert headers.headers and all(value == caption for value in headers.headers)
    assert "0.8123456789" in document
    pd.testing.assert_frame_equal(frame, before)


def test_static_report_failure_has_priority_without_rewriting_saved_flag(tmp_path):
    rows = [{"candidate_id": "ok", "evaluated": True, "error": ""},
            {"candidate_id": "failed", "evaluated": True, "error": "평가 실패"},
            {"candidate_id": "waiting", "evaluated": False}]
    frame = pd.DataFrame(rows); before = frame.copy(deep=True)
    presentation = report._report_presentation(frame, None, {"run": {"mode": "complex"}})
    assert presentation["summary"]["evaluated_total"] == 1
    assert rows[1]["evaluated"] is True
    pd.testing.assert_frame_equal(frame, before)


def _payload(tmp_path, context=False):
    run = tmp_path / "synthetic"; (run / "03_score").mkdir(parents=True)
    coordinate = run / "colabfold_model_0.cif"
    structure = mock.build_complex_structure("ACDEFGHIKL", "ACCDEFGHIK", [], seed=42)
    if context:
        model = next(structure.get_models())
        model.add(mock._build_chain("C", "ACDEF", np.array([0., 12., 0.])))
        model.add(mock._build_chain("D", "FGHIK", np.array([0., 18., 0.])))
    mock.write_cif(structure, coordinate)
    n_residues = 30 if context else 20
    np.savez(run / "pae_colabfold_model_0.npz", pae=np.arange(n_residues**2, dtype=float).reshape(n_residues, n_residues) / 20)
    (run / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "screen", "mock": True}}))
    (run / "03_score/scored_candidates.csv").write_text(
        "design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,iptm,ipsae,final_evaluated,final_eligible\n"
        "one,ACCDEFGHIK,colabfold_model_0.cif,A,B,colabfold,0.8,0.7,true,false\n")
    reader = ResultReader(run)
    payload = reader._offline_payload(reader.candidates(limit=None), {}, {})
    assert payload["structures"]
    # These are explicit synthetic rendering fixtures, never native evidence.
    payload["observations"]["rows"] = []
    return payload


def _write_document(tmp_path, payload):
    document = tmp_path / "presentation.html"
    document.write_bytes(ResultReader._offline_document(payload))
    return document


browser_only = pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium presentation acceptance")


@browser_only
@pytest.mark.parametrize("mode,scope,caption", [
    ("complex", "whole_complex", "전체 복합체 ipTM"),
    ("screen", "selected_target_binder_pair", "표적–VHH ipTM"),
    ("design", "selected_target_binder_pair", "표적–VHH ipTM"),
    ("mature", None, "ipTM · 적용 범위 미기록"),
])
def test_offline_scope_surfaces_and_svg_export(tmp_path, mode, scope, caption):
    from playwright.sync_api import expect, sync_playwright
    payload = _payload(tmp_path, context=mode == "design")
    payload["summary"]["mode"] = mode
    row = payload["candidates"]["rows"][0]
    if scope: row["iptm_scope"] = scope
    else: row.pop("iptm_scope", None)
    if mode == "design":
        row["n_context_chains"] = 2  # Pair scope survives two actual auxiliary chains.
        assert len(payload["structures"][0]["chains"]) == 4
    payload["filters"] = {"sort": "iptm", "plot_x": "ipsae", "plot_y": "iptm"}
    original = copy.deepcopy(payload)
    document = _write_document(tmp_path, payload)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(); page.add_init_script("localStorage.setItem('nanocofold.language', 'ko')"); errors = []; external = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http**/*", lambda route: (external.append(route.request.url), route.abort()))
        page.goto(document.as_uri())
        expect(page.locator("#candidate-table tbody tr")).to_have_count(1)
        expect(page.locator('#sort-metric option[value="iptm"]')).to_have_text(caption)
        expect(page.locator("#candidate-sort-note")).to_contain_text(caption)
        expect(page.locator("#candidate-table thead")).to_contain_text(caption)
        page.locator("#plot-x").select_option("iptm")
        page.locator("#plot-y").select_option("ipsae")
        expect(page.locator("#scatter .xtitle")).to_have_text(caption)
        svg = page.evaluate("Plotly.toImage(document.getElementById('scatter'), {format:'svg'})")
        assert caption in unquote(svg.split(",", 1)[1])
        page.locator("#candidate-table tbody button").first.click()
        expect(page.locator("#candidate-detail dt").filter(has_text=caption)).to_have_count(1)
        actual = page.evaluate("JSON.stringify(window.NANOCOFOLD_OFFLINE)")
        assert json.loads(actual) == original  # JSON number spelling may change; recorded values do not.
        assert errors == [] and external == []
        browser.close()


@browser_only
def test_offline_success_failure_and_unevaluated_error_cells(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    payload = _payload(tmp_path); payload["summary"]["mode"] = "complex"
    base = payload["candidates"]["rows"][0]
    payload["candidates"]["rows"] = [{**base, "candidate_id": name, "iptm_scope": "whole_complex",
        "evaluated": evaluated, "error": error, "structure_available": False}
        for name, evaluated, error in [("ok", True, ""), ("blank-ok", True, "  "), ("failed", True, "평가 실패\n보존한 상세"), ("waiting", False, "")]]
    payload["structures"] = []
    document = _write_document(tmp_path, payload)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(); page.add_init_script("localStorage.setItem('nanocofold.language', 'ko')"); page.goto(document.as_uri())
        for name, caption in [("ok", "오류 없음"), ("blank-ok", "오류 없음"), ("failed", "평가 실패"), ("waiting", "미평가")]:
            expect(page.locator(f'#candidate-table tr[data-id="{name}"] td').last).to_have_text(caption)
        expect(page.locator("#result-summary")).to_contain_text("평가 완료 2")
        expect(page.locator("#result-summary")).to_contain_text("오류 1")
        page.locator('#candidate-table tr[data-id="failed"] button').click()
        expect(page.locator("#candidate-detail")).to_contain_text("보존한 상세")
        assert page.evaluate("window.NANOCOFOLD_OFFLINE.candidates.rows.find(r=>r.candidate_id==='failed').evaluated") is True
        browser.close()


@browser_only
@pytest.mark.parametrize("mode", ["complex", "screen", "mature", "design"])
def test_offline_coordinate_source_and_download_bytes(tmp_path, mode):
    from playwright.sync_api import expect, sync_playwright
    payload = _payload(tmp_path); payload["summary"]["mode"] = mode
    base_row = payload["candidates"]["rows"][0]; base_structure = payload["structures"][0]
    raw = base_structure["content"].replace("\n", "\r\n")
    raw_sha = hashlib.sha256(raw.encode()).hexdigest()
    payload["candidates"]["rows"] = []; payload["structures"] = []
    for relation in ["native_identical", "transformed_display", "unverified_legacy"]:
        payload["candidates"]["rows"].append({**base_row, "candidate_id": relation, "evaluated": True,
            "iptm_scope": "whole_complex" if mode == "complex" else "selected_target_binder_pair"})
        structure = copy.deepcopy(base_structure)
        structure.update(candidate_id=relation, observation_id=None, content=raw if relation == "native_identical" else base_structure["content"],
            raw_content=raw if relation != "unverified_legacy" else None, raw_format="cif",
            coordinate_source={"schema_version": 2, "display_relation": relation, "display_transform": "serialization · synthetic fixture",
                "raw_sha256": raw_sha, "raw_preserved": relation != "unverified_legacy",
                "verification": {"status": "verified" if relation != "unverified_legacy" else "unavailable"}})
        payload["structures"].append(structure)
    document = _write_document(tmp_path, payload)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(); page.add_init_script("localStorage.setItem('nanocofold.language', 'ko')"); errors=[]; external=[]
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http**/*", lambda route:(external.append(route.request.url),route.abort()))
        page.goto(document.as_uri())
        for relation in ["native_identical", "transformed_display", "unverified_legacy"]:
            page.locator(f'#candidate-table tr[data-id="{relation}"] button').click()
            page.wait_for_function("id=>state.structure?.candidate_id===id && Boolean(state.viewer?.viewer.plugin.canvas3d)", arg=relation)
            expect(page.locator("#save-result-structure")).to_have_text("선택한 표시 좌표 저장")
            if relation == "unverified_legacy":
                expect(page.locator("#save-native-structure")).to_be_disabled()
                expect(page.locator("#structure-source")).to_contain_text("검증된 원본 엔진 좌표가 없습니다")
                expect(page.locator("#structure-data-note")).to_contain_text("검증된 원본 엔진 좌표가 없습니다")
            else:
                expect(page.locator("#save-native-structure")).to_be_enabled()
                text = "동일합니다" if relation == "native_identical" else "변환된 표시 좌표"
                expect(page.locator("#structure-source")).to_contain_text(text)
                expect(page.locator("#structure-data-note")).to_contain_text(text)
                with page.expect_download() as downloaded:
                    page.locator("#save-native-structure").click()
                assert hashlib.sha256(Path(downloaded.value.path()).read_bytes()).hexdigest() == raw_sha
            with page.expect_download() as displayed:
                page.locator("#save-result-structure").click()
            expected = next(item for item in payload["structures"] if item["candidate_id"] == relation)["content"].encode()
            assert Path(displayed.value.path()).read_bytes() == expected
        assert errors == [] and external == []
        browser.close()
