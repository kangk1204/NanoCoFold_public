"""Static reports localize only owned captions and retain saved scientific data."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from nanocofold import report


class ReportText(HTMLParser):
    """Read semantic table/tooltip text independently of presentation spans."""
    def __init__(self, document):
        super().__init__()
        self.text = []
        self.cells = []
        self.titles = {}
        self.cards = {}
        self.muted = []
        self.tables = []
        self._divs = []
        self._card_label = None
        self._table = None
        self._caption = None
        self._row = None
        self._body = False
        self._ignored = 0
        self._cell = None
        self._spans = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._ignored += 1
        if tag == "div":
            self._divs.append((attrs.get("class"), []))
        if tag == "table":
            self._table = {"caption": "", "rows": []}
        if tag == "caption":
            self._caption = []
        if tag == "tbody":
            self._body = True
        if tag == "tr":
            self._row = []
        if tag in {"td", "th"}:
            self._cell = []
        if tag == "span":
            self._spans.append((attrs.get("title"), attrs.get("class", ""), []))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._ignored -= 1
        if tag == "div" and self._divs:
            role, parts = self._divs.pop()
            if role == "k":
                self._card_label = "".join(parts).strip()
            if role == "v":
                self.cards[self._card_label] = "".join(parts).strip()
        if tag == "caption" and self._caption is not None:
            self._table["caption"] = "".join(self._caption).strip()
            self._caption = None
        if tag in {"td", "th"} and self._cell is not None:
            cell = "".join(self._cell).strip()
            self.cells.append(cell)
            if self._row is not None:
                self._row.append(cell)
            self._cell = None
        if tag == "tr" and self._body and self._table is not None:
            self._table["rows"].append(self._row)
        if tag == "tbody":
            self._body = False
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        if tag == "span" and self._spans:
            title, classes, parts = self._spans.pop()
            if title is not None:
                self.titles[title] = "".join(parts)
            if "muted" in classes.split():
                self.muted.append("".join(parts))

    def handle_data(self, text):
        if self._ignored:
            return
        self.text.append(text)
        if self._cell is not None:
            self._cell.append(text)
        for _, parts in self._divs:
            parts.append(text)
        if self._caption is not None:
            self._caption.append(text)
        for _, _, parts in self._spans:
            parts.append(text)


def make_report(tmp_path):
    run = tmp_path / "완료"
    run.mkdir()
    rows = pd.DataFrame([
        {"design_id": name, "source_sequence_id": name, "sequence": "ACDEFGHIK",
         "ipsae": .123456789012345, "iptm": .987654321098765, "final_eligible": index == 0,
         "final_evaluated": True, "ranking_status": "ranked" if index == 0 else "held",
         "ranking_unavailable_reason": "weighted_cdr_assessment_unavailable" if index else None,
         "exclusion_reason": ""}
        for index, name in enumerate(["완료", "미평가", "나노바디2(A3);O'Neil"])
    ])
    cfg = {"run": {"mode": "screen"}, "target": {"name": "적격"},
           "report": {"viewer": "none", "title": "미평가"}}
    context = {"raw_notes": ["완료", "미평가", '</script><img id="must-not-exist"> {0}']}
    original = rows.copy(deep=True)
    path = report.generate_report(run, cfg, context, scored=rows)
    pd.testing.assert_frame_equal(rows, original)
    return path


def test_static_report_binds_owned_captions_without_marking_raw_data(tmp_path):
    path = make_report(tmp_path)
    document = path.read_text()
    parsed = ReportText(document)
    assert 'I18N.init({explicit:true})' in document
    assert '<h1>미평가</h1>' in document
    assert {"완료", "미평가", "나노바디2(A3);O'Neil"} <= set(parsed.cells)
    assert '.123456789012345' in document and '.987654321098765' in document
    assert '<img id="must-not-exist">' not in document
    assert "__RESULT_DATA__" not in document
    assert path.stat().st_size < 100_000


def test_report_runtime_subsets_known_captions_and_escapes_script_end(tmp_path, monkeypatch):
    source = (report.TEMPLATE_DIR / "platform-i18n.js").read_text()
    source = source.replace('"미평가": "Not evaluated"', '"미평가": "</script><img id=bad>"')
    (tmp_path / "platform-i18n.js").write_text(source)
    monkeypatch.setattr(report, "TEMPLATE_DIR", tmp_path)
    runtime = report._report_i18n_runtime('<span data-report-i18n>미평가</span><pre>완료</pre>')
    assert "</script" not in runtime.lower() and r"<\/script>" in runtime
    assert '"미평가":' in runtime and '"미평가 지표: ":' not in runtime
    # The raw pre value is not a catalog-selection input.
    assert '"완료": "Complete"' not in runtime


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Actual bilingual static report acceptance")
@pytest.mark.parametrize("width", [1400, 390])
def test_static_report_locale_roundtrip_and_saved_copy_keep_raw_values(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright
    path = make_report(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": width, "height": 960})
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("http**/*", lambda route: (requests.append(route.request.url), route.abort()))
        page.goto(path.as_uri())
        expect(page.locator("html")).to_have_attribute("lang", "en")
        raw = """() => JSON.stringify({title:document.querySelector('h1').textContent,
          ids:[...document.querySelectorAll('td.original-id code')].map(n=>n.textContent),
          pre:[...document.querySelectorAll('pre')].map(n=>n.textContent),
          exactValues:[...document.querySelectorAll('td')].map(n=>n.textContent).filter(t=>/^0\\.\\d{10,}$/.test(t)),
          rows:document.querySelectorAll('tbody tr').length})"""
        before = page.evaluate(raw)
        for language in ["ko", "en", "ko"]:
            page.locator("#language-select").select_option(language)
            expect(page.locator("html")).to_have_attribute("lang", language)
            assert page.evaluate(raw) == before
            assert not page.locator("#must-not-exist").count()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
            if language == "en":
                assert page.locator("[data-report-i18n]").evaluate_all("nodes=>nodes.every(n=>!/[가-힣]/.test(n.textContent))")
        page.screenshot(path=str(tmp_path / f"static-report-{width}-ko.png"))
        # A rendered HTML copy carries its locale and canonical owned captions.
        saved = tmp_path / "saved-copy.html"
        page.locator("#language-select").select_option("en")
        saved.write_text(page.content())
        fresh = browser.new_context(viewport={"width": width, "height": 960})
        copied = fresh.new_page()
        copied.add_init_script("localStorage.setItem('nanocofold.language', 'ko')")
        copied.route("http**/*", lambda route: (requests.append(route.request.url), route.abort()))
        copied.on("pageerror", lambda error: errors.append(str(error)))
        copied.goto(saved.as_uri())
        expect(copied.locator("html")).to_have_attribute("lang", "en")
        assert copied.evaluate(raw) == before
        copied.locator("#language-select").select_option("ko")
        expect(copied.locator('a[href="#report-target"]')).to_have_text("01 입력·설정")
        copied.locator("#language-select").select_option("en")
        expect(copied.locator('a[href="#report-target"]')).to_have_text("01 Inputs & settings")
        assert copied.evaluate(raw) == before
        copied.goto(saved.as_uri() + "?lang=ko")
        expect(copied.locator("html")).to_have_attribute("lang", "ko")
        assert copied.evaluate(raw) == before
        assert errors == [] and requests == []
        browser.close()
