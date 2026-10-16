"""Read efficiency, scope-isolated Plotly tools, and truthful finalization labels."""
import os
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest

from nanocofold import report
from test_bilingual_report import ReportText
from test_reassessment_presentation import _payload, _write_document
from test_supplied_html_boot import supplied_server


def test_job_polling_and_finalization_javascript_contract():
    node=shutil.which("node")
    if not node: pytest.skip("Node is needed to execute browser logic")
    subprocess.run([node,"--test",str(Path(__file__).with_name("job_refresh_state_contract.mjs"))],check=True)


def test_static_rank_labels_and_blank_exclusions_preserve_fallback_reasons(tmp_path):
    rows=[{"design_id":"selected-ok","ranking_status":"ranked","ranking_unavailable_reason":None,
           "final_eligible":True,"exclusion_reason":"","selection_rank":1,"composite_score":.8},
          {"design_id":"held","ranking_status":"held","ranking_unavailable_reason":"weighted_cdr_assessment_unavailable",
           "final_eligible":None,"exclusion_reason":"","composite_score":None},
          {"design_id":"selected-fallback","ranking_status":"ranked","ranking_unavailable_reason":None,
           "final_eligible":False,"exclusion_reason":"ipsae","selection_rank":2,"composite_score":.1}]
    frame=pd.DataFrame(rows);before=frame.copy(deep=True)
    path=report.generate_report(tmp_path,{"run":{"mode":"screen"},"report":{"viewer":"none"}}, {},scored=frame,selected=frame.iloc[[0,2]])
    text=path.read_text()
    cells = ReportText(text).cells
    assert "순위 산정" in cells and "보류" in cells
    assert "해당 없음" in cells and "가중 CDR 개발가능성 미평가" in cells
    assert "없음" in cells and "ipSAE 기준 미통과" in cells
    assert "0.8" in cells and "0.1" in cells
    pd.testing.assert_frame_equal(frame,before)


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST")!="1",reason="Actual Plotly hover/keyboard acceptance")
@pytest.mark.parametrize("width",[1280,390])
def test_actual_modebar_hover_keeps_compact_light_controls(tmp_path,width,supplied_server):
    from playwright.sync_api import expect,sync_playwright
    document=_write_document(tmp_path,_payload(tmp_path))
    _,url=supplied_server
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page=browser.new_page(viewport={"width":width,"height":900}); page.add_init_script("localStorage.setItem('nanocofold.language', 'ko')");errors=[];external=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.route("http**/*",lambda route:route.continue_() if route.request.url.startswith(url) else (external.append(route.request.url),route.abort()))
        page.goto(url)
        expect(page.locator("body")).to_have_attribute("data-startup","ready")
        expect(page.locator(".novice-purpose-choice")).to_have_count(4)
        cards=page.locator(".novice-purpose-choice").evaluate_all("nodes=>nodes.map(n=>({background:getComputedStyle(n).backgroundColor,color:getComputedStyle(n).color}))")
        assert all(c["background"]=="rgb(255, 255, 255)" and c["color"]=="rgb(33, 55, 68)" for c in cards)
        page.screenshot(path=str(tmp_path/f"home-purpose-cards-{width}.png"),full_page=True)
        page.goto(document.as_uri())
        expect(page.locator("#viewer-candidate-select")).to_be_visible()
        navigation=page.locator(".candidate-navigation :is(button,select)").evaluate_all("nodes=>nodes.map(n=>({minHeight:getComputedStyle(n).minHeight,height:n.getBoundingClientRect().height,background:getComputedStyle(n).backgroundColor,tag:n.tagName}))")
        # Result navigation keeps 48px hit targets; Plotly tools remain compact below.
        assert navigation and all(n["minHeight"]=="48px" and 48<=n["height"]<52 for n in navigation)
        assert all(n["background"]=="rgb(255, 255, 255)" for n in navigation if n["tag"]=="BUTTON")
        page.locator(".candidate-navigation").screenshot(path=str(tmp_path/f"candidate-navigation-{width}.png"))
        expect(page.locator("#scatter .modebar-btn").first).to_be_attached()
        page.locator("#scatter").hover()
        expect(page.locator("#scatter .modebar")).to_be_visible()
        controls=page.locator("#scatter .modebar-btn").evaluate_all("nodes=>nodes.map(n=>{const s=getComputedStyle(n);return {tag:n.tagName,height:n.getBoundingClientRect().height,minHeight:s.minHeight,background:s.backgroundColor,color:s.color,icon:getComputedStyle(n.querySelector('path')).fill};})")
        assert controls and all(c["tag"]=="BUTTON" and c["height"]==pytest.approx(32,abs=.1) and c["minHeight"]=="32px" for c in controls)
        assert all(c["background"]!="rgb(51, 51, 51)" and c["icon"] in {"rgb(52, 79, 96)","rgb(23, 105, 95)"} for c in controls)
        assert page.locator('#scatter .modebar-btn[data-title*="Chart Studio" i]').count()==0
        assert page.locator('#scatter .modebar-btn[data-title*="Share chart" i]').count()==0
        page.locator("#scatter .modebar-btn").first.focus();page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.classList.contains('modebar-btn')")
        assert page.evaluate("getComputedStyle(document.activeElement).outlineColor") == "rgb(133, 96, 30)"
        page.locator("#scatter").screenshot(path=str(tmp_path/f"modebar-hover-focus-{width}.png"))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
        assert errors==[] and external==[]
        browser.close()
