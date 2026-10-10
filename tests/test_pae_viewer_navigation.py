"""Actual heatmap mouse clicks must reveal the selected MolStar residues."""
import os

import numpy as np
import pytest
import yaml

pytestmark = pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in real Chromium acceptance")


@pytest.fixture
def pae_report(tmp_path):
    from nanocofold import mock
    from nanocofold.platform.results import ResultReader

    run = tmp_path / "pae-navigation"
    (run / "03_score").mkdir(parents=True)
    structure = mock.build_complex_structure("ACDEFGHIKL", "ACCDEFGHIK", [], seed=42)
    structure[0]["A"].id = "X"
    structure[0]["B"].id = "Y"
    mock.write_cif(structure, run / "colabfold_model_0.cif")
    np.savez(run / "pae_colabfold_model_0.npz", pae=np.arange(400, dtype=float).reshape(20, 20) / 20)
    (run / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "screen", "mock": True}}))
    (run / "run_state.json").write_text('{"phases":{"final":{}}}')
    (run / "03_score/scored_candidates.csv").write_text(
        "design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,ipsae,final_eligible\n"
        "one,ACCDEFGHIK,colabfold_model_0.cif,X,Y,colabfold,0.4,false\n")
    output = tmp_path / "report.html"
    output.write_bytes(ResultReader(run).export("html")[0])
    return output


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_real_pae_cell_click_selects_residues_and_reveals_viewer(pae_report, width, height):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": width, "height": height})
        errors, external = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: external.append(r.url) if r.url.startswith("http") else None)
        page.goto(pae_report.as_uri())
        page.locator("#candidate-table tbody button").click()
        page.wait_for_function("()=>!!state.viewer&&document.getElementById('pae').data?.[0]?.z?.length===20")
        original = page.evaluate("JSON.stringify(window.NANOCOFOLD_OFFLINE)")
        page.locator("#pae").scroll_into_view_if_needed()
        page.evaluate("document.getElementById('pae').scrollIntoView({block:'center'})")
        page.wait_for_function("()=>{const r=document.getElementById('structure-view').getBoundingClientRect();return r.bottom<0;}")
        # Use Plotly's real categorical axes to click the middle of X:8/Y:4.
        cell = page.evaluate("""()=>{
            const p=document.getElementById('pae'),r=p.getBoundingClientRect(),a=p._fullLayout;
            return {x:r.x+a.xaxis._offset+a.xaxis.l2p(13),y:r.y+a.yaxis._offset+a.yaxis.l2p(7)};
        }""")
        page.mouse.click(cell["x"], cell["y"])
        labels = ["Y:4", "X:8"]
        page.wait_for_function("labels=>JSON.stringify(state.viewer.selectedLabels)===JSON.stringify(labels)", arg=labels)
        page.wait_for_function("()=>{const r=document.getElementById('structure-view').getBoundingClientRect();return Math.abs(r.y+r.height/2-innerHeight/2)<3;}")
        assert page.evaluate("""labels=>{
            const s=state.viewer.structure.obj.data,L=molstar.lib.structure.StructureElement.Loci;
            const expected=L.fromSchema(s,{items:labels.map(v=>({auth_asym_id:v.split(':')[0],auth_seq_id:Number(v.split(':')[1])}))});
            return L.areEqual(state.viewer.viewer.plugin.managers.structure.selection.getLoci(s),expected);
        }""", labels)
        expect(page.locator("#structure-note")).to_contain_text("PAE 7.65 Å · Y:4 / X:8")
        # The camera must also center these actual saved residues.
        page.wait_for_function("""labels=>{
            const s=state.viewer.structure.obj.data,L=molstar.lib.structure.StructureElement.Loci;
            const loci=L.fromSchema(s,{items:labels.map(v=>({auth_asym_id:v.split(':')[0],auth_seq_id:Number(v.split(':')[1])}))});
            const center=L.getBoundingSphere(loci).center,target=state.viewer.viewer.plugin.canvas3d.camera.state.target;
            return center.every((v,i)=>Math.abs(v-target[i])<0.01);
        }""", arg=labels)
        assert page.evaluate("JSON.stringify(window.NANOCOFOLD_OFFLINE)") == original
        # No point, unavailable viewer and a disposed/replaced viewer must never scroll.
        assert page.evaluate("""async()=>{
            const start=scrollY,v=state.viewer,d=state.structure;let finish;
            state.viewer={focus:()=>new Promise(r=>finish=r)};
            const pending=focusStructure(['X:1'],'stale',true);
            state.viewer=v;state.structure=d;finish();await pending;
            document.getElementById('pae').emit('plotly_click',{points:[]});
            state.viewer=null;
            try{await focusStructure(['X:1'],'unavailable',true);}catch(e){}
            state.viewer=v;
            return scrollY===start;
        }""")
        assert not errors and not external
        browser.close()
