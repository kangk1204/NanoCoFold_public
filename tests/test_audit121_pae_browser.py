"""A real crowded Plotly heatmap must zoom before committing residue picks."""
import os

import pytest

from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run

pytestmark = pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in actual Chromium")


def test_crowded_pae_zoom_then_exact_mouse_pick(tmp_path):
    from playwright.sync_api import sync_playwright

    run, _ = saved_run(tmp_path, count=1)
    reader = ResultReader(run)
    table = reader.candidates(limit=None)
    payload = reader._offline_payload(table, {}, {"summary":reader.summary(),**reader.provenance()})
    labels = [f"A:{i+1}" for i in range(199)]
    for structure in payload["structures"]:
        structure["pae"] = {"labels":labels,"values":[[float((i+j)%30) for i in range(199)] for j in range(199)]}
    output = tmp_path / "crowded-pae.html"
    output.write_bytes(reader._offline_document(payload))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width":1000,"height":850})
        errors = []
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto(output.as_uri())
        page.wait_for_function("() => document.getElementById('pae')?.data?.[0]?.x.length === 199")
        page.evaluate("window.pickedResidues=[]; focusStructure=async labels=>{if(Array.isArray(labels))window.pickedResidues.push(labels);};")
        def point():
            return page.locator("#pae").evaluate("""node => {
                node.scrollIntoView({block:'center',behavior:'instant'});
                const r=node.getBoundingClientRect(), a=node._fullLayout;
                return {x:r.x+a.xaxis._offset+a.xaxis.d2p('A:101'),y:r.y+a.yaxis._offset+a.yaxis.d2p('A:51')};
            }""")
        position = point();page.mouse.click(position["x"],position["y"])
        page.wait_for_function("() => Math.abs(document.getElementById('pae')._fullLayout.xaxis.range[1]-document.getElementById('pae')._fullLayout.xaxis.range[0])<=31.01")
        assert page.evaluate("window.pickedResidues") == []
        # A second intentional selection follows the zoom. Respect the actual
        # Plotly double-click gesture window before issuing the next click.
        delay = page.locator("#pae").evaluate("node => node._context.doubleClickDelay")
        page.wait_for_timeout(delay + 50)
        position = point();page.mouse.click(position["x"],position["y"])
        page.wait_for_function("() => window.pickedResidues.length===1")
        assert page.evaluate("window.pickedResidues") == [["A:101","A:51"]]
        page.locator("#pae-reset").click()
        page.wait_for_function("() => Math.abs(document.getElementById('pae')._fullLayout.xaxis.range[1]-document.getElementById('pae')._fullLayout.xaxis.range[0])>190")
        assert not errors
        browser.close()
