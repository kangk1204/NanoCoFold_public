"""Real browser UI flow with labeled API fixtures: no GPU or MSA execution."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from test_supplied_html_boot import supplied_server

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1",reason="Real Chromium UI gate")
@pytest.mark.parametrize('language',['ko','en'])
@pytest.mark.parametrize('fresh',[False,True])
def test_variant_seed_and_visible_recompute_choice_reach_the_reviewed_request(supplied_server,language,fresh):
    from playwright.sync_api import expect,sync_playwright
    _,url=supplied_server;submitted=[];jobs=[]
    def route_api(route):
        path=urlsplit(route.request.url).path.removeprefix('/api/')
        if path=='environment/status':route.fulfill(json={'ready':True,'reason':'passed'});return
        if path=='jobs' and route.request.method=='POST':
            submitted.append(route.request.post_data_json)
            job={'id':'reuse-ui-fixture','name':'Synthetic UI only','kind':'analysis','mode':'mature','state':'running' if fresh else 'complete',
                'phase':'screen' if fresh else 'reused','progress':{'completed':0,'total':None},'elapsed_seconds':0,
                'reused_result_id':None if fresh else 'prior-fixture','source_elapsed_seconds':123,'mock':False,'result_id':None}
            jobs.append(job);route.fulfill(json=job);return
        if path=='jobs':route.fulfill(json={'jobs':jobs});return
        if path.startswith('jobs/'):
            route.fulfill(json={'text':'UI fixture only','next_offset':0} if path.endswith('/logs') else jobs[0]);return
        route.continue_()
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1366,'height':1000})
        page.on('dialog',lambda dialog:dialog.accept())
        page.route('**/api/**',route_api)
        page.goto(url+'?lang='+language);expect(page.locator('body')).to_have_attribute('data-startup','ready')
        page.locator('[data-purpose="mature"]').click() if page.locator('[data-purpose="mature"]').count() else page.locator('.novice-purpose-choice').nth(3).click()
        page.locator('#load-tutorial').click();page.locator('#novice-next').click()
        page.locator('#mutation-strategy').select_option('random')
        for field,value in [('mutation-count','100'),('mutation-min','2'),('mutation-max','2'),('mutation-seed','43')]:page.locator('#'+field).fill(value)
        page.locator('#mutation-region').select_option('cdr3');page.locator('#novice-msa').select_option('cache')
        expect(page.locator('#mutation-seed-current')).to_contain_text('43')
        force=page.locator('#force-rerun');expect(force).to_be_visible();force.set_checked(fresh)
        page.locator('#novice-next').click();page.locator('#review-input').click()
        expect(page.locator('#start-job')).to_be_enabled()
        cfg=json.loads(page.locator('#resolved-config').text_content())
        assert cfg['mature']['library']['seed']==43 and cfg['run']['seed']==42
        page.locator('#start-job').click();expect(page.locator('#view-jobs')).to_be_visible()
        assert len(submitted)==1 and submitted[0]['reuse_results'] is (not fresh)
        if not fresh:
            expect(page.locator('#job-detail')).to_contain_text('기존 결과 재사용' if language=='ko' else 'Previous result reused')
            expect(page.locator('#job-detail')).to_contain_text('새 계산 없음' if language=='ko' else 'No new computation')
            expect(page.locator('#job-detail')).not_to_contain_text('0/미기록')
        browser.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1",reason="Real Chromium UI gate")
def test_consent_keeps_requested_value_during_slow_save_and_reverts_on_failure(supplied_server):
    from playwright.sync_api import expect,sync_playwright
    _,url=supplied_server
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        page=browser.new_page();pending=[]
        def hold(route):
            if route.request.method=='POST':pending.append(route)
            else:route.continue_()
        page.route('**/api/msa-consent',hold)
        page.goto(url+'?lang=en')
        expect(page.locator('body')).to_have_attribute('data-startup','ready')
        page.locator('#open-environment').click()
        consent=page.locator('#saved-msa-consent')
        consent.check();expect(consent).to_be_checked();expect(consent).to_be_disabled()
        assert len(pending)==1
        pending.pop().fulfill(status=500,json={'detail':'Controlled save failure'})
        expect(consent).to_be_enabled();expect(consent).not_to_be_checked()
        consent.check();expect(consent).to_be_checked();expect(consent).to_be_disabled()
        pending.pop().fulfill(json={'allowed':True,'scope':'installation'})
        expect(consent).to_be_enabled();expect(consent).to_be_checked()
        consent.uncheck();expect(consent).not_to_be_checked();expect(consent).to_be_disabled()
        pending.pop().fulfill(json={'allowed':False,'scope':'installation'})
        expect(consent).to_be_enabled();expect(consent).not_to_be_checked()
        browser.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1",reason="Real Chromium UI gate")
@pytest.mark.parametrize("language",["ko","en"])
def test_environment_once_then_review_and_explicit_analysis(supplied_server,language):
    from playwright.sync_api import expect, sync_playwright
    _,url=supplied_server
    checked=False;jobs=[];posts=[];errors=[]
    def route_api(route):
        nonlocal checked
        request=route.request;path=urlsplit(request.url).path.removeprefix("/api/")
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
        page.locator("#open-environment").click()
        # Each language has its own installation; revocation is tested below.
        saved_consent = page.locator("#saved-msa-consent")
        saved_consent.uncheck()
        expect(saved_consent).to_be_enabled()
        page.locator("#diagnose-environment").click()
        expect(page.locator("#environment-status")).to_contain_text("완료" if language=="ko" else "ready",timeout=10000)
        expect(page.locator("#view-environment")).to_be_visible()
        assert posts.count("environment/check")==1
        page.locator("#environment-continue").click()
        page.locator("#load-tutorial").click()
        tutorial = json.loads((ROOT / "examples/tutorial/complex_batch.json").read_text())
        expect(page.locator("#job-name")).to_have_value(tutorial["name"])
        expect(page.locator("#load-tutorial")).to_be_enabled()
        page.locator("#novice-next").click()
        msa, consent = page.locator("#novice-msa"), page.locator("#novice-consent")
        expect(consent).to_be_visible()
        msa.select_option("cache")
        expect(consent).to_be_hidden()
        expect(page.locator("#msa-transfer-note")).to_contain_text("캐시" if language=="ko" else "cache")
        msa.select_option("local_mmseqs2")
        expect(page.locator("#msa-transfer-note")).to_contain_text("데이터베이스" if language=="ko" else "database")
        msa.select_option("public_api");consent.check();expect(consent).to_be_enabled();page.locator("#novice-next").click()
        page.locator("#review-input").click()
        expect(page.locator("#start-job")).to_be_enabled()
        assert posts.count("environment/check")==1 and "jobs" not in posts
        # Re-review a different run name: reuse the same check; no automatic job.
        page.locator("#novice-previous").click();page.locator("#novice-previous").click()
        page.locator("#job-name").fill("another-input")
        page.locator("#novice-next").click()
        expect(consent).to_be_checked()
        page.locator("#novice-next").click()
        page.locator("#review-input").click();expect(page.locator("#start-job")).to_be_enabled()
        assert posts.count("environment/check")==1 and "jobs" not in posts
        page.locator("#start-job").click();expect(page.locator("#view-jobs")).to_be_visible()
        assert posts.count("jobs")==1
        expect(page.locator("#job-detail")).to_contain_text("분석 중" if language=="ko" else "progress")
        assert not page.locator(".job-log-details").evaluate("e=>e.open")
        page.locator('[data-view="input"]').click()
        expect(page.locator(".novice-heading")).to_contain_text("1/4")
        page.locator('[data-view="jobs"]').click()
        page.locator("#jobs-next-analysis").click()
        expect(page.locator(".novice-heading")).to_contain_text("1/4")
        expect(page.locator("#job-name")).to_have_value("another-input")
        expect(consent).to_be_checked()
        page.locator(".novice-purpose-choice").first.click()
        page.locator('[data-view="jobs"]').click()
        page.locator('[data-view="input"]').click()
        expect(page.locator(".novice-heading")).to_contain_text("2/4")
        assert posts.count("jobs") == 1
        page.locator('[data-view="environment"]').click()
        page.locator("#shared-server-help summary").click()
        expect(page.locator("#shared-server-help")).to_contain_text("ssh -N")
        expect(page.locator("#shared-server-help")).to_contain_text("사용자 PC" if language == "ko" else "At each desk")
        page.locator('[data-view="jobs"]').click()
        # A real linked result is required before the UI offers Open results.
        jobs[0].update(state="complete",result_id="fixture-result")
        page.locator("#refresh-jobs").click()
        expect(page.locator("#job-actions")).to_contain_text("결과 열기" if language=="ko" else "Open results")
        page.locator('.job-history summary').click()
        page.locator('.job-history .job-row').click()
        expect(page.locator("#job-detail")).to_contain_text("환경 확인" if language=="ko" else "environment check")
        expect(page.locator("#job-actions")).not_to_contain_text("결과 열기" if language=="ko" else "Open results")
        page.reload()
        expect(page.locator("body")).to_have_attribute("data-startup","ready")
        page.locator('[data-view="environment"]').click()
        expect(saved_consent).to_be_checked()
        # Revocation is also persisted and clears the single and batch controls.
        saved_consent.uncheck()
        expect(saved_consent).to_be_enabled()
        page.reload()
        expect(page.locator("body")).to_have_attribute("data-startup","ready")
        expect(saved_consent).not_to_be_checked()
        expect(page.locator("#batch-api-consent")).not_to_be_checked()
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
