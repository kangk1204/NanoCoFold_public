"""Opt-in local Chromium QA of generic diagnostics; no server or scientific input."""
import json
import os
from pathlib import Path

import pytest

from nanocofold.handoff_diagnostics import saved_handoff_diagnostics
from test_saved_handoff_diagnostics import gate, write_gate, RECORDED


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in installed Chromium only")
def test_saved_handoff_keyboard_languages_and_missing_corrupt_states(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    root = Path(__file__).resolve().parents[1]
    source = (root / "nanocofold/templates/platform.js").read_text()
    renderer = source[source.index('// HANDOFF_PRESENTATION_START'):source.index('function resultMsaCaption(')]
    i18n = (root / "nanocofold/templates/platform-i18n.js").read_text()
    css = (root / "nanocofold/templates/platform.css").read_text()
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed; no installation attempted")
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 900})
            requests, errors = [], []
            page.on("request", lambda request: requests.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            for status in ('available', 'missing', 'invalid'):
                run = tmp_path / status
                run.mkdir()
                if status == 'available': write_gate(run, gate())
                elif status == 'invalid':
                    (run / '05_final').mkdir()
                    (run / '05_final/handoff_gate.json').write_text('{')
                summary = {"mode": "screen", "complete": True, "status": "complete",
                           "config": {"structure": {"msa": {"backend": "empty"}}},
                           "state": {"phases": {"final": {"handoff_gate": RECORDED}}},
                           "handoff_diagnostics": saved_handoff_diagnostics(run, RECORDED)}
                encoded = json.dumps(summary).replace('<', '\\u003c')
                document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><style>'+css+'</style></head><body><main><label for="language-select">Language / 언어</label><select id="language-select"><option value="en">English</option><option value="ko">한국어</option></select><p>Computation complete · synthetic saved fixture</p><div id="result-summary"></div></main><script>'+i18n+'</script><script>const $=id=>document.getElementById(id);function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)I18N.text(node,text);if(cls)node.className=cls;return node;}'+renderer+'\nrenderHandoffStatus('+encoded+');$("language-select").addEventListener("change",e=>I18N.setLanguage(e.target.value));</script></body></html>'
                path = tmp_path / (status+'.html')
                path.write_text(document)
                page.goto(path.as_uri())
                panel = page.locator('[data-handoff-status="blocked"]')
                expect(panel).to_contain_text('Handoff incomplete')
                expect(panel).to_contain_text('Recorded errors: 6 · Warnings: 1')
                expect(panel).to_contain_text('without an MSA')
                page.select_option('#language-select', 'ko')
                expect(panel).to_contain_text('인계 미완료')
                expect(panel).to_contain_text('기록 오류 6건 · 경고 1건')
                if status == 'available':
                    expect(panel.locator('li')).to_have_count(7)
                    expect(panel).to_contain_text('expression.vector')
                else:
                    expect(panel.locator('li')).to_have_count(0)
                    expect(panel).to_contain_text('상세 인계 JSON')
                toggle = panel.locator('details > summary')
                toggle.focus()
                page.keyboard.press('Enter')
                expect(panel.locator('details')).to_have_attribute('open', '')
                if status == 'available':
                    toggle.press('Enter')
                    page.screenshot(path=str(tmp_path / 'handoff-native-ko.png'), full_page=True)
                page.select_option('#language-select', 'en')
                expect(panel).to_contain_text('Handoff incomplete')
            assert requests == [] and errors == []
        finally:
            browser.close()
