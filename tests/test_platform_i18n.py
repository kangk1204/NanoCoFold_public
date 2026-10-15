"""Both locales are real UI paths; sequences, IDs and saved data stay unchanged."""
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_supplied_html_boot import supplied_server  # real supplied package, no replacement page

ROOT = Path(__file__).resolve().parents[1]


def test_readme_install_commands_match_in_both_languages():
    texts = [(ROOT / name).read_text() for name in ('README.md', 'README.ko.md')]
    assert 'English' in texts[0].splitlines()[2] and '[한국어](README.ko.md)' in texts[0]
    assert '[English](README.md)' in texts[1]
    assert re.findall(r'```bash\n(.*?)```', texts[0], re.S) == re.findall(r'```bash\n(.*?)```', texts[1], re.S)


def test_actual_translator_preserves_user_data_and_logs():
    subprocess.run(['node', '--test', str(ROOT / 'tests/platform_i18n_contract.mjs'),
                    str(ROOT / 'tests/final_review_i18n_scope_contract.mjs')], check=True, capture_output=True)


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium release gate')
@pytest.mark.parametrize("mode,title", [("complex","Predict protein complexes"),("design","Design new nanobodies"),("screen","Compare existing nanobodies"),("mature","Evaluate nanobody variants")])
def test_english_default_korean_toggle_persistence_and_input_integrity(supplied_server, mode, title):
    from playwright.sync_api import expect, sync_playwright
    _, url = supplied_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page(); errors = []; submitted = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda r: submitted.append(r.url) if r.method == 'POST' and '/api/jobs' in r.url else None)
        page.goto(url)
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        expect(page.locator('#view-input > p.next-step')).to_contain_text('Choose a purpose.')
        page.get_by_role('button', name=title+' · Enter input', exact=True).click()
        page.get_by_role('button', name='Load public example for this purpose', exact=True).click()
        expect(page.locator('#tutorial-status')).to_have_text('Public input loaded. Computation has not started.')
        expect(page.locator('#view-input > p.next-step')).to_contain_text('Check full sequences, chain order, and copy counts.')
        field = '#complex-fasta' if mode == 'complex' else '#target-fasta'
        fasta = page.locator(field).input_value()
        page.locator('#job-name').fill("완료_나노바디2(A3);O'Neil")
        page.locator('#language-select').select_option('ko')
        expect(page.get_by_role('button', name='입력 확인 후 다음', exact=True)).to_be_visible()
        expect(page.locator('#view-input > p.next-step')).to_contain_text('전체 서열·체인 순서·사본 수')
        assert page.locator(field).input_value() == fasta
        if mode == 'complex':
            expect(page.locator('#novice-complex-copies input')).to_have_count(2)
            expect(page.locator('#complex-copies-0')).to_have_value('1')
            expect(page.locator('#complex-copies-1')).to_have_value('1')
        assert page.locator('#job-name').input_value() == "완료_나노바디2(A3);O'Neil"
        page.locator('#language-select').select_option('en')
        page.get_by_role('button', name='Review input & continue', exact=True).click()
        expect(page.locator('.novice-heading')).to_contain_text('Run settings')
        expect(page.locator('#view-input > p.next-step')).to_contain_text('maximum planned model count')
        page.get_by_role('button', name='Review settings & continue', exact=True).click()
        expect(page.locator('#novice-blocker')).to_contain_text('Consent')
        page.locator('#language-select').select_option('ko')
        expect(page.locator('#novice-blocker')).to_contain_text('동의')
        page.locator('#novice-consent').check()
        page.get_by_role('button', name='조건 확인 후 다음', exact=True).click()
        expect(page.locator('#view-input > p.next-step')).to_contain_text('입력을 검토하고 환경 점검')
        page.locator('#language-select').select_option('en')
        expect(page.locator('#view-input > p.next-step')).to_contain_text('Review the input and pass environment checks')
        expect(page.locator('#novice-consent')).to_be_checked()
        page.locator('#language-select').select_option('ko')
        page.reload(); expect(page.locator('html')).to_have_attribute('lang', 'ko')
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        assert errors == [] and submitted == []
        page.goto(url + '?lang=en'); expect(page.locator('html')).to_have_attribute('lang', 'en')
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium release gate')
def test_offline_toggle_preserves_structure_observation_user_id_and_numeric_arrays(tmp_path):
    import json
    import numpy as np
    from nanocofold import mock
    from nanocofold.platform.results import ResultReader
    from playwright.sync_api import expect, sync_playwright
    run = tmp_path / 'run'; (run / '03_score').mkdir(parents=True)
    rows = []
    for index, name in enumerate(['완료', "나노바디2(A3);O'Neil"]):
        coord = f'model_{index}.cif'
        mock.write_cif(mock.build_complex_structure('ACDEFGHIKL','ACCDEFGHIK',[],seed=42+index),run/coord)
        np.savez(run/f'pae_model_{index}.npz',pae=np.arange(400,dtype=float).reshape(20,20)/20)
        rows.append(f'"{name}",ACCDEFGHIK,{coord},A,B,colabfold,{0.4-index*.1},{(.2,.9)[index]},selected_target_binder_pair,false')
    (run/'run_config.yaml').write_text('run:\n  mode: screen\n  mock: true\n')
    (run/'run_state.json').write_text('{"phases":{"final":{}}}')
    (run/'03_score/scored_candidates.csv').write_text('design_id,sequence,cif_path,target_chain,binder_chain,predictor_engine,ipsae,iptm,iptm_scope,final_eligible\n'+'\n'.join(rows)+'\n')
    report=tmp_path/'report.html'; report.write_bytes(ResultReader(run).export('html')[0])
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page=browser.new_page(); errors=[]; external=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('http**/*',lambda route:(external.append(route.request.url),route.abort()))
        page.goto(report.as_uri())
        page.wait_for_function('()=>Boolean(state.viewer?.viewer.plugin.canvas3d)',timeout=30000)
        expect(page.locator('html')).to_have_attribute('lang','en')
        expect(page.locator('#candidate-title')).to_have_text('완료')
        assert page.locator('#distribution').evaluate('(node)=>node.data.map(trace=>trace.y)') == [[0.2]]
        assert page.locator('#distribution').evaluate('(node)=>node.layout.xaxis.ticktext') == ['Condition 1']
        page.locator('#select-result-target').click()
        page.wait_for_function('()=>state.viewer.selectedLabels?.length>0')
        snapshot='''()=>JSON.stringify({data:document.getElementById('result-data').textContent,
          structure:state.structure.content,request:state.structureRequest,candidate:state.candidate.candidate_id,
          observation:document.getElementById('observation-select').value,
          residues:state.viewer.selectedLabels,chains:[...document.querySelectorAll('[data-layer]')].map(e=>[e.dataset.layer,e.checked]),
          pae:document.getElementById('pae').data.map(d=>d.z),
          distribution:document.getElementById('distribution').data.map(d=>[d.name,d.y])})'''
        before=page.evaluate(snapshot)
        page.locator('#language-select').select_option('ko')
        expect(page.locator('#structure-note')).to_contain_text('체인')
        expect(page.locator('#candidate-title')).to_have_text('완료')
        assert page.evaluate(snapshot)==before
        assert page.locator('#distribution').evaluate('(node)=>node.layout.xaxis.ticktext') == ['조건 1']
        page.locator('#language-select').select_option('en')
        expect(page.locator('#candidate-title')).to_have_text('완료')
        assert page.evaluate(snapshot)==before
        page.locator('#candidate-table tbody tr').filter(has_text="나노바디2(A3);O'Neil").locator("button").click()
        expect(page.locator('#candidate-title')).to_have_text("나노바디2(A3);O'Neil")
        assert page.locator('#distribution').evaluate('(node)=>node.data.map(trace=>trace.y)') == [[0.9]]
        page.locator('#language-select').select_option('ko')
        page.locator('#language-select').select_option('en')
        assert page.locator('#distribution').evaluate('(node)=>node.data.map(trace=>trace.y)') == [[0.9]]
        assert errors==[] and external==[]
        browser.close()
