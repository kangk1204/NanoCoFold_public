"""Exercise the delivered explorer and the separate summary as a reader would."""
import os
import pytest

from nanocofold.report import generate_report, report_explorer_path
from test_audit21_presentation import saved_run


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Browser opt-in")
def test_metric_labels_explain_direction_without_changing_results(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    run, cfg = saved_run(tmp_path, count=2)
    report = generate_report(run, cfg, {})
    explorer = report_explorer_path(report)
    with sync_playwright() as pw:
        options = {"channel": os.environ["NCF_CHROMIUM_CHANNEL"]} if os.environ.get("NCF_CHROMIUM_CHANNEL") else {}
        browser = pw.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={"width": 840, "height": 720}, reduced_motion="reduce")
            page.goto(explorer.as_uri() + "?lang=en")
            panel = page.locator("#candidate-details-panel")
            panel.locator("summary").first.click()
            tips = panel.locator("dt button[data-ncf-metric]")
            expect(tips).to_have_count(24)
            ip = tips.locator('xpath=self::*[@data-ncf-metric="ipsae"]')
            pae = panel.locator('button[data-ncf-metric="interface_pae"]')
            tip = page.locator('#ncf-metric-tooltip')
            ip.hover(); expect(tip).to_contain_text("↑ Interface confidence")
            page.mouse.move(0, 0); expect(tip).to_be_hidden()
            pae.focus(); expect(tip).to_contain_text("↓ Uncertainty")
            expect(pae).to_have_attribute("aria-describedby", "ncf-metric-tooltip")
            pae.press("Escape"); expect(tip).to_be_hidden()
            page.select_option("#language-select", "ko")
            panel.locator('[data-ncf-metric="esm_score"]').focus(); expect(tip).to_contain_text("0점과 다릅니다")
            panel.locator('[data-ncf-metric="n_hbonds"]').focus(); expect(tip).to_contain_text("수소결합 개수")
            count = page.locator('#candidate-table tbody tr').count()
            header = page.locator('#candidate-table th[data-column-key="ipsae"]')
            before = header.get_attribute('aria-sort')
            header.locator('.ncf-help-only').click(); expect(tip).to_be_visible()
            assert header.get_attribute('aria-sort') == before and page.locator('#candidate-table tbody tr').count() == count
            header.locator('.sort-header').click()
            assert header.get_attribute('aria-sort') != before
            page.goto(report.as_uri() + "?lang=ko")
            th = page.locator('th[data-column-key="ipsae"] button').first
            th.focus(); expect(tip).to_contain_text("계면 배치 신뢰도")
            th.press("Escape"); expect(tip).to_be_hidden()
            touch = browser.new_context(viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True, reduced_motion="reduce")
            mobile = touch.new_page(); mobile.goto(explorer.as_uri() + "?lang=en")
            mobile.locator('#candidate-details-panel > summary').tap()
            control = mobile.locator('#candidate-detail [data-ncf-metric="interface_pae"]')
            control.tap(); mobiletip = mobile.locator('#ncf-metric-tooltip')
            expect(mobiletip).to_contain_text("↓ Uncertainty")
            bounds = mobiletip.bounding_box()
            assert 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= 320
            control.tap(); expect(mobiletip).to_be_hidden()
        finally:
            browser.close()
