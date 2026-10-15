"""Real Plotly point hits must select the saved model on desktop and mobile."""
import os

import pandas as pd
import pytest

from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run


pytestmark = pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in actual Chromium")


@pytest.mark.parametrize("width,height", [(1440, 950), (390, 844)])
def test_real_scatter_mouse_point_selects_matching_candidate_and_structure(tmp_path, width, height):
    from playwright.sync_api import expect, sync_playwright

    run, _ = saved_run(tmp_path, count=3)
    rows = pd.read_csv(run / "03_score/scored_candidates.csv")
    rows["iptm"] = [.2, .5, .8]
    rows.to_csv(run / "03_score/scored_candidates.csv", index=False)
    output = tmp_path / "scatter.html"
    output.write_bytes(ResultReader(run).export("html")[0])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": width, "height": height})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url) if request.url.startswith("http") else None)
        page.goto(output.as_uri())
        page.locator("#plot-y").select_option("iptm")
        page.wait_for_function("() => document.getElementById('scatter').data?.[0]?.customdata?.length === 3")
        original = page.evaluate("JSON.stringify(state.rows)")
        for identifier in ("model-0", "model-2"):
            page.locator("#scatter").evaluate("node => node.scrollIntoView({block:'center'})")
            point = page.locator("#scatter").evaluate("""(node, identifier) => {
                const row = node.data[0], index = row.customdata.indexOf(identifier), r = node.getBoundingClientRect(), a = node._fullLayout;
                return {x:r.x+a.xaxis._offset+a.xaxis.d2p(row.x[index]),
                        y:r.y+a.yaxis._offset+a.yaxis.d2p(row.y[index])};
            }""", identifier)
            page.mouse.click(point["x"], point["y"])
            expect(page.locator("#candidate-title")).to_have_text(identifier)
            expect(page.locator("#candidate-table tbody tr.selected")).to_have_attribute("data-id", identifier)
            page.wait_for_function("id => state.structure?.candidate_id === id && !!state.viewer?.viewer.plugin.canvas3d", arg=identifier)
        assert page.evaluate("JSON.stringify(state.rows)") == original
        assert not errors and not external
        browser.close()
