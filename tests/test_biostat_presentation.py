"""Scientific limitations survive the delivered report's small bilingual runtime."""
import html
import json
import os
from pathlib import Path
import re

import pandas as pd
import pytest

from nanocofold import report, rank_select, engine_comparison
from nanocofold.config import load_config
from test_audit126_report_warnings import execute_subset, Warnings


PARTIAL_WARNING = (
    "부분 재평가 결과는 엔진별로 따로 순위를 계산했습니다. 엔진마다 후보 집합이 달라 "
    "엔진 성능·친화도 비교나 통합 순위로 해석할 수 없습니다. rank.final_n은 엔진별 상한이며 "
    "통합 발주 목록은 자동 승인하지 않습니다."
)
HISTORICAL_WARNING = (
    '구형 혼합·미확인 엔진 순위의 발주 전달을 보류했습니다. 원본 점수·선별 표는 보존했으며, '
    '새 실행 또는 재분석으로 엔진별 순위를 계산해야 합니다.'
)


def make_report(tmp_path):
    cfg = load_config()
    cfg['report']['viewer'] = 'none'
    frame = pd.DataFrame([{'design_id': '나노바디2(A3)', 'sequence': 'ACDEFG',
                           'iptm': .30000000000000004}])
    before = frame.copy(deep=True)
    path = report.generate_report(tmp_path, cfg, {'warnings': [PARTIAL_WARNING, HISTORICAL_WARNING],
                                  'raw_note': '원문 그대로 보존'}, scored=frame)
    pd.testing.assert_frame_equal(frame, before)
    return path


def test_partial_rescore_warning_is_in_actual_delivered_translation_subset(tmp_path):
    document = make_report(tmp_path).read_text()
    messages = Warnings(document).messages
    assert PARTIAL_WARNING in messages
    runtime = re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>', document)[1]
    translated = execute_subset(runtime, [PARTIAL_WARNING, HISTORICAL_WARNING])
    assert translated['ko'] == [PARTIAL_WARNING, HISTORICAL_WARNING]
    assert 'candidate cohorts differ' in translated['en'][0]
    assert 'per-engine limit' in translated['en'][0]
    assert 'not automatically approved' in translated['en'][0]
    assert not re.search('[가-힣]', translated['en'][0])
    assert 'Synthesis handoff is held' in translated['en'][1]
    assert 'Original score and selection tables were preserved' in translated['en'][1]
    assert 'data-recorded-value="0.30000000000000004"' in document
    records = []
    for block in re.findall(r'<pre[^>]*>([\s\S]*?)</pre>', document):
        try:
            value = json.loads(html.unescape(block))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and 'raw_note' in value:
            records.append(value['raw_note'])
    assert records == ['원문 그대로 보존']


def test_actual_per_engine_ranking_warnings_translate_in_delivered_subset(tmp_path):
    cfg = load_config()
    cfg['screen']['comparison']['enabled'] = True
    cfg['rank']['weights'] = {'iptm': 1., 'ipsae': 1.}
    cfg['report']['viewer'] = 'none'
    source = pd.DataFrame([
        {'design_id': 'c1', 'sequence': 'ACDE', 'predictor_engine': 'chai1', 'iptm': .9},
        {'design_id': 'c2', 'sequence': 'ACDF', 'predictor_engine': 'chai1', 'iptm': .8},
        {'design_id': 'c3', 'sequence': 'ACDG', 'predictor_engine': 'chai1', 'iptm': float('nan')},
        {'design_id': 'b1', 'sequence': 'YYYY', 'predictor_engine': 'boltz2', 'iptm': .9, 'ipsae': .8},
        {'design_id': 'b2', 'sequence': 'YYYA', 'predictor_engine': 'boltz2', 'iptm': .8, 'ipsae': .7},
    ])
    ranked, _ = engine_comparison.rank_and_select(source, cfg)
    messages = rank_select.ranking_warnings(ranked, cfg)
    assert any(value.startswith('chai1: ') for value in messages)
    document = report.generate_report(tmp_path, cfg, {'warnings': messages}, scored=ranked).read_text()
    runtime = re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>', document)[1]
    translated = execute_subset(runtime, messages)
    assert translated['ko'] == messages
    assert all(not re.search('[가-힣]', value) for value in translated['en'])
    assert [value.split(': ', 1)[0] for value in translated['en']] == [value.split(': ', 1)[0] for value in messages]


@pytest.mark.parametrize('width', [390, 1400])
def test_actual_report_language_flip_retains_new_limitation_and_raw_numbers(tmp_path, width):
    if os.getenv('NANOCOFOLD_BROWSER_TEST') != '1':
        pytest.skip('set NANOCOFOLD_BROWSER_TEST=1 for actual Chromium')
    from playwright.sync_api import sync_playwright, expect

    path = make_report(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 900})
        page.goto(path.as_uri(), wait_until='domcontentloaded')
        selector = page.locator('#language-select')
        cell = page.locator('[data-recorded-value="0.30000000000000004"]')
        for locale in ['en', 'ko', 'en']:
            selector.select_option(locale)
            warning = page.locator('[data-report-i18n]').filter(
                has_text='Partial reevaluation results' if locale == 'en' else '부분 재평가 결과')
            expect(warning).to_have_count(1)
            if locale == 'en':
                assert 'candidate cohorts differ' in warning.inner_text()
            else:
                assert warning.inner_text() == PARTIAL_WARNING
            historical = page.locator('[data-report-i18n]').filter(
                has_text='Synthesis handoff is held' if locale == 'en' else '구형 혼합·미확인 엔진 순위')
            expect(historical).to_have_count(1)
            expect(cell).to_have_count(2)
            assert cell.evaluate_all('(cells) => cells.map(cell => cell.getAttribute("data-recorded-value"))') == ['0.30000000000000004'] * 2
            assert '나노바디2(A3)' in page.locator('body').inner_text()
            output = os.getenv('NANOCOFOLD_BIOSTAT_SCREENSHOT_DIR')
            if output:
                destination = Path(output)
                destination.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(destination / f'report-{width}-{locale}.png'), full_page=False)
        browser.close()
