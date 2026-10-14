"""Actual saved-report/UI contracts; synthetic coordinates do not prove prediction quality."""
import json
import os
import re
from html.parser import HTMLParser

import numpy as np
import pandas as pd
import pytest
import yaml

from nanocofold import mock, report
from nanocofold.platform.results import ResultReader


class FrameDocument(HTMLParser):
    document = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "iframe" and attributes.get("id") == "complete-result-explorer":
            self.document = attributes["srcdoc"]


@pytest.mark.parametrize("row", [
    {"candidate_id": "opendde__original"},
    {"candidate_id": "opendde__original", "engine": "opendde"},
    {"candidate_id": "custom__original", "engine": "custom"},
])
def test_source_identity_preserves_original_across_engine_alias_fields(row):
    assert report._source_identity(row) == "original"
    assert report._source_identity({**row, "source_sequence_id": "recorded-original"}) == "recorded-original"


def saved_run(tmp_path, count=8):
    run = tmp_path / "saved-models"
    (run / "03_score").mkdir(parents=True)
    target, binder = "ACDEFGHIKL", "ACCDEFGHIK"
    pdb = mock.write_pdb(mock.build_complex_structure(target, binder, [], seed=42), run / "prediction.pdb")
    cfg = {"run": {"mode": "screen", "mock": True}, "target": {"sequence": target},
           "report": {"viewer": "3dmol", "top_n_table": 2}}
    (run / "run_config.yaml").write_text(yaml.safe_dump(cfg))
    (run / "run_state.json").write_text(json.dumps({"phases": {"final": {}}}))
    rows = [{"design_id": f"model-{i}", "sequence": binder, "cif_path": pdb.name,
             "predictor_engine": "boltz2", "target_chain": "A", "binder_chain": "B",
             "ipsae": .2 + i / 100, "iptm": .4, "iptm_scope": "selected_target_binder_pair",
             "final_evaluated": True, "final_eligible": False,
             "ipsae_scope": "selected_chain_pair", "ipsae_selection": "explicit_primary_pair",
             "ipsae_chain_a": "A", "ipsae_chain_b": "B", "min_pair_ipsae": .18,
             "max_pair_ipsae": .28, "pair_ipsae_assessed_count": 1, "pair_ipsae_total_count": 3} for i in range(count)]
    pd.DataFrame(rows).to_csv(run / "03_score/scored_candidates.csv", index=False)
    np.savez(run / "pae_prediction.npz", pae=np.arange(400, dtype=np.float32).reshape(20, 20) / 20)
    return run, cfg


def test_general_report_uses_complete_saved_explorer_without_modifying_originals(tmp_path):
    run, cfg = saved_run(tmp_path)
    originals = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    document = report.generate_report(run, cfg, {}, viewer_max=1).read_text()
    frame = FrameDocument();frame.feed(document)
    assert frame.document
    match = re.search(r'<script id="result-data" type="application/json">(.*?)</script>', frame.document, re.S)
    payload = json.loads(match.group(1))
    assert len(payload["candidates"]["rows"]) == 8  # Both former viewer/table limits are bypassed here.
    expected = json.loads(re.search(r'<script id="result-data" type="application/json">(.*?)</script>',
                          ResultReader(run).export("html")[0].decode(), re.S).group(1))
    assert payload["candidates"] == expected["candidates"]
    assert payload["structures"] == expected["structures"]
    assert 'data-layer="contacts"' in frame.document and 'id="measure-result-distance"' in frame.document
    assert all(path.read_bytes() == data for path, data in originals.items())


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in actual Chromium")
def test_complete_report_mode_controls_and_rotated_zoomed_camera_survive_resize(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    run, cfg = saved_run(tmp_path)
    output = report.generate_report(run, cfg, {}, viewer_max=1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url) if request.url.startswith("http") else None)
        page.goto(output.as_uri())
        page.locator("#complete-result-explorer").scroll_into_view_if_needed()
        frame = page.frame_locator("#complete-result-explorer")
        expect(frame.locator("#candidate-table tbody tr")).to_have_count(8)
        frame.locator("#candidate-table tbody button").filter(has_text="model-7").click()
        frame.locator("#structure-view canvas").wait_for()
        inner = next(f for f in page.frames if f != page.main_frame)
        inner.wait_for_function("() => !!state.viewer?.viewer.plugin.canvas3d?.camera")
        original = inner.evaluate("JSON.stringify(state.rows)")
        inner.evaluate("() => {state.result.mode='complex';syncResultModeControls();renderCandidates();}")
        expect(frame.locator("#eligibility-basis")).to_be_hidden()
        expect(frame.locator("#hotspot-filter")).to_be_hidden()
        expect(frame.locator('[data-layer="cdr"]')).to_be_hidden()
        assert inner.evaluate("() => [...document.getElementById('sort-metric').options].filter(o=>!o.hidden && o.value!=='iptm').every(o=>!o.textContent.includes('VHH'))")
        assert inner.locator('html').get_attribute("data-renderer-version") == "nanocofold-results-v3"
        assert inner.locator('th[data-column-key="iptm"]').count() == 1
        frame.locator("#candidate-table tbody button").filter(has_text="model-7").click()
        inner.wait_for_function("() => !!state.viewer?.viewer.plugin.canvas3d?.camera")
        frame.locator("#candidate-details-panel > summary").click()
        scope = frame.locator("#candidate-detail details").filter(has_text="ipSAE·ipTM 적용 체인과 출처").first
        scope.locator("summary").click()
        expect(scope).to_contain_text("요약 쌍 선택: 명시한 체인쌍")
        expect(scope).to_contain_text("평가 쌍 1 / 전체 쌍 3")
        expect(scope).to_contain_text("최솟값 0.180 / 최댓값 0.280")
        # Explicit scope stays authoritative even when the general-mode fixture is toggled.
        expect(frame.locator('th[data-column-key="iptm"]')).to_contain_text("표적–VHH ipTM")
        pose = inner.evaluate("""() => {
            const camera=state.viewer.viewer.plugin.canvas3d.camera, before=camera.getSnapshot();
            const offset=before.position.map((v,i)=>v-before.target[i]);
            camera.setState({position:[before.target[0]+offset[2]*1.23,before.target[1]+offset[1]*1.23,before.target[2]-offset[0]*1.23]},0);
            return camera.getSnapshot();
        }""")
        page.set_viewport_size({"width": 900, "height": 750})
        inner.wait_for_function("() => {const c=state.viewer.viewer.plugin.canvas3d.camera;return c.viewport.width>0 && c.viewport.height>0;}")
        after = inner.evaluate("state.viewer.viewer.plugin.canvas3d.camera.getSnapshot()")
        for field in ("position", "target", "up"):
            assert after[field] == pytest.approx(pose[field], abs=1e-5)
        assert inner.evaluate("JSON.stringify(state.rows)") == original
        frame.locator("#fit-result-structure").click()  # Fitting remains an explicit user action.
        inner.evaluate("() => {state.result.mode='screen';syncResultModeControls();renderCandidates();}")
        expect(frame.locator("#eligibility-basis")).to_be_visible()
        expect(frame.locator('[data-layer="cdr"]')).to_be_visible()
        assert not errors and not external
        browser.close()
