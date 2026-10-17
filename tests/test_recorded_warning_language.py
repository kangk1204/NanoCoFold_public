"""Recorded-warning display is localized without rewriting scientific records."""
import csv
import io
import json
import os
import zipfile

import pytest


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium recorded-warning acceptance')
def test_delivered_warning_language_switch_preserves_records_and_deduplicates(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from nanocofold import mock
    from nanocofold.platform.results import ResultReader

    run = tmp_path / 'run'
    (run / '03_score').mkdir(parents=True)
    mock.write_cif(mock.build_complex_structure('ACDEFGHIKL', 'ACCDEFGHIK', [], seed=42), run / 'model.cif')
    (run / 'run_config.yaml').write_text('run:\n  mode: screen\n  mock: true\n')
    recommendation = '엔진별 추천 목록을 따로 저장했습니다. 통합 발주 목록은 연구자가 서열을 선택한 뒤 작성해야 합니다.'
    held = 'boltz2: 종합점수·기록 순위 보류 1/1개 모델. 이 중 1개는 CDR 개발가능성이 미평가입니다. ANARCI 환경 또는 서열에 맞는 CDR 범위를 확인하세요.'
    raw_note = '연구자 원문: 후보한글(2) / 0.95'
    recorded = [recommendation, held, raw_note, recommendation]
    (run / 'run_state.json').write_text(json.dumps({'execution': {'state': 'complete'}, 'warnings': recorded}, ensure_ascii=False))
    with (run / '03_score/scored_candidates.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['design_id','sequence','cif_path','engine','target_chain','binder_chain','iptm','final_eligible','mock'])
        writer.writeheader()
        writer.writerow({'design_id':'후보한글(2)','sequence':'ACCDEFGHIK','cif_path':'model.cif','engine':'boltz2','target_chain':'A','binder_chain':'B','iptm':.95,'final_eligible':False,'mock':True})
    data, _, _ = ResultReader(run).export('report_zip')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = tmp_path / 'report.html'
        document.write_bytes(archive.read('report.html'))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        errors = []
        external = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: external.append(request.url) if request.url.startswith(('https://','http://')) else None)
        page.goto(document.as_uri())
        page.locator('#language-select').select_option('en')
        warnings = page.locator('#result-issues > .warning')
        expect(warnings).to_have_count(3)
        expect(warnings.nth(0)).to_have_text('Warnings recorded at execution: Recommendations were saved separately for each engine. Compile a combined order list only after a researcher selects the sequences.')
        expect(warnings.nth(1)).to_contain_text('Warnings recorded at execution: boltz2: Composite score and recorded rank are held for 1/1 models.')
        expect(warnings.nth(1)).not_to_contain_text('종합점수')
        expect(warnings.nth(2)).to_have_text('Warnings recorded at execution: ' + raw_note)
        before = page.evaluate('({warnings:window.NANOCOFOLD_OFFLINE.summary.warnings,rows:window.NANOCOFOLD_OFFLINE.candidates.rows})')
        for language in ['ko','en','ko','en']:
            page.locator('#language-select').select_option(language)
            prefix = '실행 당시 기록 경고: ' if language == 'ko' else 'Warnings recorded at execution: '
            expect(warnings).to_have_count(3)
            expect(warnings.nth(2)).to_have_text(prefix + raw_note)
            expect(page.locator('#candidate-table')).to_contain_text('후보한글(2)')
            if language == 'ko':
                expect(warnings.nth(0)).to_have_text(prefix + recommendation)
                expect(warnings.nth(1)).to_have_text(prefix + held)
            assert page.evaluate('({warnings:window.NANOCOFOLD_OFFLINE.summary.warnings,rows:window.NANOCOFOLD_OFFLINE.candidates.rows})') == before
        assert before['warnings'] == recorded
        assert errors == [] and external == []
        browser.close()
