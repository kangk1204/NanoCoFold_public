"""Run supplied HTML/JS/CSS together; DOM stubs cannot satisfy this gate."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_INPUT_IDS = {"batch-content-details", "batch-format-help", "batch-next-step", "batch-tutorial-summary",
    "input-next-step", "load-tutorial", "save-native-structure", "save-pae", "structure-data-note",
    "structure-source", "tutorial-mode", "tutorial-status", "candidate-extra-columns"}


def expect_public_tutorial_ready(page, mode):
    """The input-step heading is present before the async example is loaded."""
    from playwright.sync_api import expect
    payload = json.loads((ROOT / "examples/tutorial" / f"{mode}_batch.json").read_text())
    entry = payload["entries"][0]
    expect(page.locator("#tutorial-status")).to_have_text(
        "공개 입력을 불러왔습니다. 아직 계산은 시작하지 않았습니다.")
    expect(page.locator("#load-tutorial")).to_be_enabled()
    expect(page.locator("#job-name")).to_have_value(payload["name"])
    if mode == "complex":
        fasta = "\n".join(f'>{protein["name"]}\n{protein["sequence"]}' for protein in entry["proteins"])
        expect(page.locator("#complex-fasta")).to_have_value(fasta)
        expect(page.locator("#novice-complex-copies input")).to_have_count(len(entry["proteins"]))
        for index, protein in enumerate(entry["proteins"]):
            expect(page.locator(f"#complex-copies-{index}")).to_have_value(str(protein["copies"]))
    else:
        expect(page.locator("#target-fasta")).to_have_value(entry["content"])
        if mode == "screen":
            expect(page.locator("#vhh-fasta")).to_have_value(entry["vhh_fasta"])
        if mode == "mature":
            expect(page.locator("#wt-fasta")).to_have_value(entry["wt_fasta"])
    expect(page.locator(".novice-heading")).to_contain_text("2/4")


def test_supplied_dom_and_generated_offline_contract():
    import re
    html = (ROOT / "nanocofold/templates/platform.html").read_text()
    ids = re.findall(r'id="([^"]+)"', html)
    assert REQUIRED_INPUT_IDS <= set(ids)
    assert len(ids) == len(set(ids)), "Duplicated DOM ids break the supplied UI"
    assert 'aria-label="전체 VHH FASTA 파일"' in html
    assert 'aria-current="step"' in (ROOT / "nanocofold/templates/platform.css").read_text()
    offline = (ROOT / "nanocofold/templates/platform_offline.html").read_text()
    for name in ["structure-source", "structure-data-note", "save-pae", "save-native-structure", "candidate-extra-columns"]:
        assert f'id="{name}"' in offline


@pytest.fixture(scope="module")
def supplied_server(tmp_path_factory):
    from nanocofold.install import init_workspace
    workspace = tmp_path_factory.mktemp("supplied-html")
    init_workspace(workspace)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    launch_environment = dict(os.environ)
    if os.environ.get("NANOCOFOLD_SUPPLIED_INSTALLED") == "1":
        launch_environment.pop("PYTHONPATH", None)
    else:
        # Source acceptance must run this checkout in the actual CLI child,
        # even when the development environment has an older wheel installed.
        inherited = launch_environment.get("PYTHONPATH", "")
        launch_environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + inherited if inherited else "")
    process = subprocess.Popen([sys.executable, "-m", "nanocofold", "serve", "--workspace", str(workspace),
        "--port", str(port), "--no-browser"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=launch_environment, cwd=workspace)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(200):
            try:
                if urllib.request.urlopen(url, timeout=.5).status == 200: break
            except OSError: time.sleep(.1)
        else: raise AssertionError("Supplied package server did not start")
        yield workspace, url
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium release gate")
@pytest.mark.parametrize("mode,title", [("complex","단백질 복합체 예측"),("design","새 나노바디 설계"),
    ("screen","보유 나노바디 비교"),("mature","기존 나노바디 변이 평가")])
def test_supplied_app_four_purposes_steps_and_menus(supplied_server,mode,title):
    from playwright.sync_api import expect, sync_playwright
    workspace,url = supplied_server
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page=browser.new_page();errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.goto(url);expect(page.locator("body")).to_have_attribute("data-startup","ready")
        expect(page.locator(".novice-purpose-choice")).to_have_count(4)
        for name in ["배치 분석","작업·로그","결과 탐색","환경·설치","입력·실행"]:
            page.get_by_role("button",name=name,exact=True).click()
        page.get_by_role("button",name=title+" · 입력하기",exact=True).click()
        page.get_by_role("button",name="이 목적의 공개 예제 불러오기",exact=True).click()
        expect_public_tutorial_ready(page, mode)
        page.get_by_role("button",name="입력 확인 후 다음",exact=True).click()
        expect(page.locator(".novice-heading")).to_contain_text("3/4")
        page.locator("#novice-msa").focus()
        assert page.locator("#novice-msa").evaluate("e=>getComputedStyle(e).outlineColor") == "rgb(133, 96, 30)"
        assert page.locator('.workflow-steps [aria-current="step"]').evaluate("e=>getComputedStyle(e).borderTopWidth") == "2px"
        page.screenshot(path=str(workspace.parent / ("supplied-"+mode+"-conditions.png")),full_page=True)
        # Native public inputs require an explicit new consent, before review.
        page.get_by_role("button",name="조건 확인 후 다음",exact=True).click()
        expect(page.locator("#novice-blocker")).to_contain_text("동의")
        page.locator("#novice-consent").check()
        page.get_by_role("button",name="조건 확인 후 다음",exact=True).click()
        expect(page.locator(".novice-heading")).to_contain_text("4/4")
        expect(page.locator("#start-job")).to_be_disabled()
        assert errors == []
        browser.close()
    assert not list((workspace/".nanocofold/jobs").glob("*/config.yaml")), "Viewing steps must not submit compute"


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium release gate")
def test_public_tutorial_pending_response_blocks_empty_input_then_advances(supplied_server):
    from playwright.sync_api import expect, sync_playwright
    workspace, url = supplied_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(); held = []; errors = []; compute_requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: compute_requests.append(request.url)
                if request.method == "POST" and "/api/jobs" in request.url else None)
        page.goto(url); expect(page.locator("body")).to_have_attribute("data-startup", "ready")
        page.get_by_role("button", name="단백질 복합체 예측 · 입력하기", exact=True).click()
        page.locator("#input-kind").select_option("fasta")
        page.locator("#complex-fasta").fill("")
        page.route("**/api/tutorials/complex", lambda route: held.append(route))
        with page.expect_request("**/api/tutorials/complex"):
            page.get_by_role("button", name="이 목적의 공개 예제 불러오기", exact=True).click()
        expect(page.locator("#load-tutorial")).to_be_disabled()
        assert len(held) == 1
        expect(page.locator("#complex-fasta")).to_have_value("")
        page.get_by_role("button", name="입력 확인 후 다음", exact=True).click()
        expect(page.locator(".novice-heading")).to_contain_text("2/4")
        expect(page.locator("#novice-blocker")).to_contain_text("빈 서열은 사용할 수 없습니다")
        expect(page.locator("#complex-fasta")).to_have_attribute("aria-invalid", "true")
        assert compute_requests == []
        # Release the real server response after exercising the pending state;
        # no arbitrary sleep, replacement example, or relaxed input validation.
        held[0].fulfill(response=held[0].fetch())
        expect_public_tutorial_ready(page, "complex")
        page.get_by_role("button", name="입력 확인 후 다음", exact=True).click()
        expect(page.locator(".novice-heading")).to_contain_text("3/4")
        assert compute_requests == [] and errors == []
        browser.close()
    assert not list((workspace / ".nanocofold/jobs").glob("*/config.yaml"))


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium release gate")
def test_supplied_offline_structure_pae_columns_and_focus(tmp_path):
    from nanocofold import mock
    from nanocofold.platform.results import ResultReader
    from playwright.sync_api import expect, sync_playwright
    run=tmp_path/"synthetic";(run/"03_score").mkdir(parents=True)
    mock.write_cif(mock.build_complex_structure("ACDEFGHIKL","ACCDEFGHIK",[],seed=42),run/"colabfold_model_0.cif")
    np.savez(run/"pae_colabfold_model_0.npz",pae=np.arange(400,dtype=float).reshape(20,20)/20)
    (run/"run_config.yaml").write_text(yaml.safe_dump({"run":{"mode":"screen","mock":True}}))
    (run/"run_state.json").write_text('{"phases":{"final":{}}}')
    (run/"03_score/scored_candidates.csv").write_text(
        "design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,ipsae,final_eligible\n"
        "one,ACCDEFGHIK,colabfold_model_0.cif,A,B,colabfold,0.4,false\n")
    report=tmp_path/"report.html";report.write_bytes(ResultReader(run).export("html")[0])
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page=browser.new_page(reduced_motion="reduce");errors=[];external=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.route("http**/*",lambda route:(external.append(route.request.url),route.abort()))
        page.goto(report.as_uri())
        page.wait_for_function("()=>Boolean(state.viewer?.viewer.plugin.canvas3d)",timeout=30000)
        expect(page.locator("#structure-note")).to_contain_text("충돌 잔기쌍")
        assert page.locator("#candidate-table th").count()==11
        expect(page.locator("#candidate-table")).to_contain_text("부적격")
        page.locator("#candidate-extra-columns").check();assert page.locator("#candidate-table th").count()>11
        assert page.locator("#pae").evaluate("e=>e.data[0].zmin===0 && e.data[0].zmax===30 && e._context.displayModeBar===false")
        assert page.locator("#pae").evaluate("e=>e._fullLayout.shapes.length") == 2
        original=page.evaluate("JSON.stringify(window.NANOCOFOLD_OFFLINE)")
        page.locator("#pae").evaluate("e=>e.emit('plotly_click',{points:[{x:e.data[0].x[1],y:e.data[0].y[2],z:e.data[0].z[2][1]}]})")
        page.wait_for_function("()=>state.viewer.selectedLabels?.length===2")
        assert page.evaluate("JSON.stringify(window.NANOCOFOLD_OFFLINE)")==original
        assert errors==[] and external==[]
        browser.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium release gate")
def test_cpu_demo_review_needs_no_public_msa_consent(supplied_server):
    from playwright.sync_api import expect, sync_playwright
    _, url = supplied_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(); errors=[]
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url); expect(page.locator("body")).to_have_attribute("data-startup","ready")
        page.get_by_role("button",name="보유 나노바디 비교 · 입력하기",exact=True).click()
        page.get_by_text("GPU 없이 사용 흐름 연습 · 합성 데모",exact=True).click()
        page.locator("#fill-demo").click()
        page.get_by_role("button",name="입력 확인 후 다음",exact=True).click()
        expect(page.locator("#novice-consent")).not_to_be_checked()
        page.get_by_role("button",name="조건 확인 후 다음",exact=True).click()
        page.get_by_role("button",name="입력 검토·환경 점검",exact=True).click()
        expect(page.locator("#review-output")).to_be_visible(timeout=20000)
        config = json.loads(page.locator("#resolved-config").text_content())
        assert config["run"]["mock"] is True
        assert config["structure"]["msa"]["backend"] == "empty"
        assert config["screen"]["msa_mode"] == "single"
        assert config["screen"]["allow_public_msa_server"] is False
        # A native-only purpose cannot inherit a hidden checked mock or its review.
        for _ in range(3): page.locator("#novice-previous").click()
        page.get_by_role("button",name="단백질 복합체 예측 · 입력하기",exact=True).click()
        expect(page.locator("#mock")).not_to_be_checked()
        expect(page.locator("#mock")).to_be_disabled()
        assert page.evaluate("state.input === null")
        expect(page.locator("#start-job")).to_be_disabled()
        page.get_by_role("button",name="이 목적의 공개 예제 불러오기",exact=True).click()
        expect_public_tutorial_ready(page, "complex")
        page.get_by_role("button",name="입력 확인 후 다음",exact=True).click()
        expect(page.locator("#novice-consent")).not_to_be_checked()
        page.get_by_role("button",name="조건 확인 후 다음",exact=True).click()
        expect(page.locator("#novice-blocker")).to_contain_text("동의")
        page.locator("#novice-consent").check()
        page.get_by_role("button",name="조건 확인 후 다음",exact=True).click()
        expect(page.locator(".novice-heading")).to_contain_text("4/4")
        expect(page.locator("#start-job")).to_be_disabled()
        assert errors == []
        browser.close()
