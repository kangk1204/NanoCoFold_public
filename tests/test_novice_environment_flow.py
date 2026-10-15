"""Real browser UI flow with labeled API fixtures: no GPU or MSA execution."""
import json
import os
from pathlib import Path

import pytest

from test_supplied_html_boot import supplied_server

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1",reason="Real Chromium UI gate")
@pytest.mark.parametrize("language",["ko","en"])
def test_environment_once_then_review_and_explicit_analysis(supplied_server,language):
    from playwright.sync_api import expect, sync_playwright
    _,url=supplied_server
    checked=False;jobs=[];posts=[];errors=[]
    def route_api(route):
        nonlocal checked
        request=route.request;path=request.url.split("/api/",1)[1]
        if request.method=="POST":posts.append(path)
        if path=="environment/status":
            route.fulfill(json={"ready":checked,"reason":"passed" if checked else "not_checked"});return
        if path=="environment/check":
            checked=True
            job={"id":"ui-environment-fixture","name":"diagnostics","kind":"diagnostics","mode":"complex",
                 "state":"complete","mock":False,"diagnostics":{"ok":True,"checks":[]},"progress":{},"result_id":None}
            jobs.insert(0,job);route.fulfill(json=job);return
        if path=="jobs" and request.method=="GET":route.fulfill(json={"jobs":jobs});return
        if path=="jobs" and request.method=="POST":
            job={"id":"ui-analysis-fixture","name":"UI fixture · no inference","kind":"analysis","mode":"complex",
                 "state":"running","mock":False,"progress":{},"result_id":None}
            jobs.insert(0,job);route.fulfill(json=job);return
        if path.startswith("jobs/"):
            if "/logs" in path:route.fulfill(json={"text":"UI fixture only; no inference executed.\n","next_offset":1});return
            route.fulfill(json=next(j for j in jobs if j["id"]==path.split("/")[1]));return
        if path=="diagnostics":pytest.fail("Input review submitted a new environment job")
        route.continue_()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page=browser.new_page(viewport={"width":1366,"height":900})
        page.add_init_script(f"localStorage.setItem('nanocofold.language','{language}')")
        page.route("**/api/**",route_api);page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto(url);expect(page.locator("body")).to_have_attribute("data-startup","ready")
        page.locator("#open-environment").click();page.locator("#diagnose-environment").click()
        expect(page.locator("#environment-status")).to_contain_text("완료" if language=="ko" else "ready",timeout=10000)
        expect(page.locator("#view-environment")).to_be_visible()
        assert posts.count("environment/check")==1
        page.locator("#environment-continue").click()
        page.locator("#load-tutorial").click()
        page.locator("#novice-next").click();page.locator("#novice-consent").check();page.locator("#novice-next").click()
        page.locator("#review-input").click()
        expect(page.locator("#start-job")).to_be_enabled()
        assert posts.count("environment/check")==1 and "jobs" not in posts
        # Re-review a different run name: reuse the same check; no automatic job.
        page.locator("#novice-previous").click();page.locator("#novice-previous").click()
        page.locator("#job-name").fill("another-input")
        page.locator("#novice-next").click();page.locator("#novice-consent").check();page.locator("#novice-next").click()
        page.locator("#review-input").click();expect(page.locator("#start-job")).to_be_enabled()
        assert posts.count("environment/check")==1 and "jobs" not in posts
        page.locator("#start-job").click();expect(page.locator("#view-jobs")).to_be_visible()
        assert posts.count("jobs")==1
        expect(page.locator("#job-detail")).to_contain_text("분석 중" if language=="ko" else "progress")
        assert not page.locator(".job-log-details").evaluate("e=>e.open")
        # A real linked result is required before the UI offers Open results.
        jobs[0].update(state="complete",result_id="fixture-result")
        page.locator("#refresh-jobs").click()
        expect(page.locator("#job-actions")).to_contain_text("결과 열기" if language=="ko" else "Open results")
        page.locator('.job-history summary').click()
        page.locator('.job-history .job-row').click()
        expect(page.locator("#job-detail")).to_contain_text("환경 확인" if language=="ko" else "environment check")
        expect(page.locator("#job-actions")).not_to_contain_text("결과 열기" if language=="ko" else "Open results")
        assert errors==[]
        browser.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1",reason="Real Chromium UI gate")
def test_bilingual_header_report_navigation_and_axis_controls(supplied_server):
    from playwright.sync_api import expect,sync_playwright
    workspace,url=supplied_server
    # An explicit synthetic saved result makes the actual explorer visible.
    import numpy as np
    from nanocofold import mock
    run=workspace/"runs/layout-synthetic";(run/"03_score").mkdir(parents=True,exist_ok=True)
    mock.write_cif(mock.build_complex_structure("ACDEFGHIKL","ACCDEFGHIK",[],seed=42),run/"model.cif")
    (run/"run_config.yaml").write_text("run: {mode: screen, mock: true}\n")
    (run/"run_state.json").write_text('{"phases":{"final":{}}}')
    (run/"03_score/scored_candidates.csv").write_text("design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,ipsae,final_eligible\none,ACCDEFGHIK,model.cif,A,B,boltz2,0.4,false\n")
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page=browser.new_page()
        for width in [1440,1280,1024,768,390]:
            heights=[]
            for language in ["ko","en"]:
                page.set_viewport_size({"width":width,"height":900});page.goto(url)
                expect(page.locator("body")).to_have_attribute("data-startup","ready")
                page.locator("#language-select").select_option(language)
                page.locator('[data-view="results"]').click()
                page.locator('#result-path').fill("runs/layout-synthetic");page.locator('#open-result').click()
                expect(page.locator('#scatter')).to_be_visible()
                heights.append(page.locator('header').bounding_box()["height"])
                # Every label belongs to its own field. Y cannot wrap apart from its select.
                for axis in ["x","y"]:
                    field=page.locator(f'.axis-field:has(#plot-{axis})')
                    assert field.locator(f'label[for="plot-{axis}"]').count()==1
                    label=field.locator("label").bounding_box();select=field.locator("select").bounding_box()
                    assert abs(label["x"]-select["x"])<1 and label["y"]<select["y"]
                boxes=[a.bounding_box() for a in page.locator('.report-nav a').all()]
                assert abs(boxes[0]["y"]-boxes[1]["y"])<1
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            assert abs(heights[0]-heights[1])<=22
        browser.close()
