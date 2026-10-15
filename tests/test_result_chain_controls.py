"""Exercise chain selection and enlarged canvases with the actual offline MolStar."""
import json
import os

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in real Chromium acceptance",
)


@pytest.fixture
def report(tmp_path):
    from nanocofold import mock
    from nanocofold.platform.results import ResultReader

    run = tmp_path / "chain-controls"
    (run / "03_score").mkdir(parents=True)
    pdb = mock.write_pdb(mock.build_complex_structure("ACDEFGHIKL", "ACCDEFGHIK", [], seed=42), run / "prediction.pdb")
    # Metadata, not A/B assumptions, must drive the controls.
    pdb.write_text("".join(line[:21] + {"A": "X", "B": "Y"}.get(line[21], line[21]) + line[22:]
                           if line.startswith(("ATOM  ", "HETATM", "TER   ")) and len(line) > 21 else line
                           for line in pdb.read_text().splitlines(keepends=True)))
    (run / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "screen", "mock": True}}))
    (run / "run_state.json").write_text(json.dumps({"phases": {"final": {}}}))
    (run / "03_score/scored_candidates.csv").write_text(
        "design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,ipsae,final_eligible\n"
        "one,ACCDEFGHIK,prediction.pdb,X,Y,boltz2,0.4,false\n"
        "two,ACCDEFGHIK,prediction.pdb,X,Y,chai1,0.2,false\n")
    output = tmp_path / "report.html"
    output.write_bytes(ResultReader(run).export("html")[0])
    return output


@pytest.mark.parametrize("width,height,expected_height", [(1440, 900, 680), (1440, 1100, 840), (1440, 700, 520), (390, 900, 720)])
def test_chain_buttons_select_saved_chain_and_canvas_resizes(report, width, height, expected_height):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": width, "height": height}); page.add_init_script("localStorage.setItem('nanocofold.language', 'ko')")
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url) if request.url.startswith("http") else None)
        page.goto(report.as_uri())
        page.locator("#candidate-table tbody button").filter(has_text="one").click()
        page.wait_for_function("() => !!state.viewer?.viewer.plugin.canvas3d")
        expect(page.locator("#select-result-target")).to_be_enabled()
        before = page.evaluate("JSON.stringify(state.rows)")
        assert page.locator("#structure-view").bounding_box()["height"] == pytest.approx(expected_height, abs=2)
        # Verify MolStar's actual selection, as well as saved residue labels.
        for role, chain in [("target", "X"), ("binder", "Y")]:
            page.locator(f'[data-layer="{role}"]').uncheck()
            page.locator(f"#select-result-{role}").click()
            page.wait_for_function("chain => state.viewer.selectedLabels?.length === 10 && state.viewer.selectedLabels.every(label => label.startsWith(chain + ':'))", arg=chain)
            expect(page.locator(f'[data-layer="{role}"]')).to_be_checked()
            assert page.evaluate("""chain => {
                const plugin = state.viewer.viewer.plugin, structure = state.viewer.structure.obj.data;
                const Loci = molstar.lib.structure.StructureElement.Loci;
                return Loci.areEqual(plugin.managers.structure.selection.getLoci(structure), Loci.fromSchema(structure, {auth_asym_id: chain}));
            }""", chain)
            expect(page.locator("#structure-note")).to_contain_text(f"체인 {chain}")
        page.locator("#clear-result-selection").click()
        page.wait_for_function("() => state.viewer.selectedLabels?.length === 0")
        assert page.evaluate("state.viewer.viewer.plugin.managers.structure.selection.stats.elementCount") == 0
        page.locator("#fit-result-structure").click()
        for viewport in ({"width": 390, "height": 844}, {"width": 1440, "height": 900}):
            page.set_viewport_size(viewport)
            page.wait_for_function("""() => {
                const host=document.getElementById('structure-view'), canvas=host.querySelector('canvas');
                const view=state.viewer.viewer.plugin.canvas3d.camera.viewport, rect=canvas.getBoundingClientRect();
                return Math.abs(view.width/view.height-rect.width/rect.height)<.002;
            }""")
            page.wait_for_timeout(3000)
            # Resize preserves manual orientation/zoom. Fitting is an explicit
            # action; independently project every saved C-alpha afterwards.
            page.locator("#fit-result-structure").click()
            page.wait_for_function("""() => {
                const camera=state.viewer.viewer.plugin.canvas3d.camera, v=camera.viewport;
                return state.structure.chains.flatMap(c=>c.residues).filter(r=>r.ca).every(r=>{
                    const point=camera.project([0,0,0],r.ca);
                    return point[0]>=v.x && point[0]<=v.x+v.width && point[1]>=v.y && point[1]<=v.y+v.height;
                });
            }""")
        page.locator("#next-candidate").click()
        page.wait_for_function("() => state.structure?.candidate_id === 'two' && !!state.viewer")
        page.locator("#select-result-binder").click()
        page.wait_for_function("() => state.viewer.selectedLabels?.length === 10")
        page.evaluate("() => {state.result.mode = 'complex'; syncResultChainControls();}")
        expect(page.locator("#select-result-target")).to_have_text("기준 체인 선택")
        expect(page.locator("#select-result-binder")).to_have_text("파트너 선택")
        # An absent chain never silently selects a different chain.
        page.evaluate("() => {state.structure.binder_chain = 'absent'; syncResultChainControls();}")
        expect(page.locator("#select-result-binder")).to_be_disabled()
        assert page.evaluate("JSON.stringify(state.rows)") == before
        page.locator("#candidate-search").fill("missing")
        expect(page.locator("#select-result-target")).to_be_disabled()
        expect(page.locator("#select-result-binder")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        assert not errors and not external
        browser.close()
