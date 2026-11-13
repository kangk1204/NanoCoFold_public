"""A language choice must not be offered before the released listener exists."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "nanocofold" / "templates"


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Chromium integration")
@pytest.mark.parametrize("template", ["platform.html", "platform_offline.html", "report.html.j2"])
def test_language_choice_waits_for_its_listener(template):
    from playwright.sync_api import sync_playwright, expect

    # Stop parsing just after the real language control, before the application
    # scripts would arrive. This reproduces the visible gap on slow machines.
    document = (ROOT / template).read_text()
    begin = document.index('<select id="language-select"')
    control = document[begin:document.index("</select>", begin) + len("</select>")]
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        page = browser.new_page()
        page.set_content("<html lang='en'><body>" + control + "</body></html>")
        select = page.locator("#language-select")
        expect(select).to_be_disabled()
        page.add_script_tag(path=str(ROOT / "platform-i18n.js"))
        page.evaluate("I18N.init()")
        expect(select).to_be_enabled()
        select.select_option("ko")
        expect(page.locator("html")).to_have_attribute("lang", "ko")
        select.select_option("en")
        expect(page.locator("html")).to_have_attribute("lang", "en")
