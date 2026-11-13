"""Exercise saved-analysis navigation and displayed options through the shipped UI."""

import io
import os
import zipfile
from pathlib import Path

import pytest
import yaml

from nanocofold.platform.result_catalog import ResultCatalog
from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run
from test_supplied_html_boot import supplied_server as _supplied_server

supplied_server = _supplied_server


browser_check = pytest.mark.skipif(
    os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Actual browser opt-in"
)


@pytest.fixture(scope="module")
def analyses(supplied_server):
    workspace, url = supplied_server
    catalog = ResultCatalog(workspace)
    for index in range(28):
        name = f"journey-{index:02d}"
        run, config = saved_run(workspace / "runs" / name, count=2 if index == 0 else 1)
        config["run"]["name"] = name
        config["target"]["name"] = "EGFR test fixture"
        (run / "run_config.yaml").write_text(yaml.safe_dump(config))
        catalog.register(name, run)
    assert "journey-00" not in {row["id"] for row in catalog.list()["items"]}
    return workspace, url, catalog


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        options = {"channel": os.environ["NCF_CHROMIUM_CHANNEL"]} if os.environ.get("NCF_CHROMIUM_CHANNEL") else {}
        browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"], **options)
        try:
            yield browser.new_page(viewport={"width": 1366, "height": 900})
        finally:
            browser.close()


@browser_check
def test_old_database_result_remains_selected_after_refresh(analyses, page):
    from playwright.sync_api import expect

    _, url, _ = analyses
    page.goto(url + "?result=journey-00&candidate=model-1&lang=ko")
    expect(page.locator("#candidate-title")).to_have_text("model-1")
    expect(page.locator("#result-select")).to_have_value("journey-00")
    page.locator('[data-view="catalog"]').click()
    page.locator('[data-view="results"]').click()
    expect(page.locator("#result-select")).to_have_value("journey-00")
    assert page.locator("#result-select option").count() <= 27
    expect(page.locator("#vhh-sequence-value")).to_have_value("ACCDEFGHIK")


@browser_check
def test_new_analysis_starts_with_its_own_candidates(analyses, page):
    from playwright.sync_api import expect

    _, url, _ = analyses
    page.goto(url + "?result=journey-26&lang=en")
    expect(page.locator("#candidate-table tbody tr")).to_have_count(1)
    page.locator("#candidate-search").fill("absent-from-the-next-analysis")
    page.locator("#eligibility").select_option("true")
    page.locator("#hotspot-filter").check()
    expect(page.locator("#candidate-table tbody tr")).to_have_count(0)
    page.locator("#result-select").select_option("journey-27")
    expect(page.locator("#candidate-search")).to_have_value("")
    expect(page.locator("#eligibility")).to_have_value("all")
    expect(page.locator("#hotspot-filter")).not_to_be_checked()
    expect(page.locator("#candidate-table tbody tr")).to_have_count(1)
    expect(page.locator("#vhh-sequence-value")).to_have_value("ACCDEFGHIK")
    page.locator("#candidate-search").fill("model-0")
    page.evaluate("loadResult('journey-27')")
    expect(page.locator("#candidate-search")).to_have_value("model-0")


@browser_check
@pytest.mark.parametrize("language", ["ko", "en"])
def test_review_displays_the_selected_comparison_msa(analyses, page, language):
    from playwright.sync_api import expect

    _, url, _ = analyses
    page.goto(url + "?lang=" + language)
    expect(page.locator("body")).to_have_attribute("data-startup", "ready")
    page.locator(".novice-purpose-choice").nth(2).click()
    page.locator("#load-tutorial").click()
    expect(page.locator("#job-name")).to_have_value("tutorial-il6-screen")
    page.locator("#novice-next").click()
    for source in ("cache", "local_mmseqs2", "public_api"):
        page.locator("#novice-msa").select_option(source)
        if source == "public_api":
            page.locator("#novice-consent").check()
        page.locator("#novice-next").click()
        with page.expect_response("**/api/inputs") as prepared:
            page.locator("#review-input").click()
        response = prepared.value.json()
        assert response["config"]["screen"]["comparison"]["enabled"]
        assert response["config"]["screen"]["colabfold"]["msa_source"] == source
        expect(page.locator("#novice-reviewed-summary")).to_contain_text("MSA: " + source)
        assert page.evaluate("api('/api/jobs')")["jobs"] == []
        page.locator("#novice-previous").click()


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("format", ["html", "report_zip"])
def test_export_records_the_chosen_report_language(tmp_path, language, format):
    run, _ = saved_run(tmp_path, count=1)
    reader = ResultReader(run)
    before = reader.all_rows()
    contents, _, _ = reader.export(format, {"language": language})
    if format == "report_zip":
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            assert archive.testzip() is None
            contents = archive.read("report.html")
    document = contents.decode()
    assert f'data-nanocofold-report-language="{language}"' in document
    assert reader.all_rows() == before


def test_invalid_export_language_has_an_actionable_error(tmp_path):
    run, _ = saved_run(tmp_path, count=1)
    for language in ("fr", "\"><script>", None, ["ko"]):
        with pytest.raises(ValueError, match="language"):
            ResultReader(run).export("html", {"language": language})


@browser_check
@pytest.mark.parametrize("language", ["ko", "en"])
def test_database_search_sort_excel_and_reopen_candidate(analyses, page, language):
    from openpyxl import load_workbook
    from playwright.sync_api import expect

    _, url, _ = analyses
    page.goto(url + "?view=catalog&lang=" + language)
    page.locator("#show-candidate-table").click()
    page.locator("#db-vhh-mock").check()
    page.locator("#db-vhh-target").fill("egfr")
    page.locator("#db-vhh-search").fill("CDE")
    page.locator("#db-vhh-engine").select_option("boltz2")
    page.locator("#db-vhh-apply").click()
    rows = page.locator("#db-vhh-table tbody tr")
    expect(rows).to_have_count(25)
    page.locator("#db-vhh-next").click()
    expect(rows).to_have_count(4)
    page.locator("#db-vhh-table th").filter(has_text="ipSAE").get_by_role("button").click()
    expect(rows).to_have_count(25)
    expect(rows.first).to_contain_text("model-1")
    with page.expect_download() as download:
        page.locator("#db-vhh-export").click()
    book = load_workbook(io.BytesIO(Path(download.value.path()).read_bytes()), read_only=True)
    values = list(book.active.values)
    book.close()
    assert len(values) == 30  # Export all 29 matches, not just the visible page.
    assert values[1][3] == "model-1" and values[1][8] == .21
    assert {r[12] for r in values[1:]} == {f"journey-{i:02d}" for i in range(28)}
    page.locator("#db-vhh-search").fill("model-1")
    expect(page.locator("#db-vhh-export")).to_be_disabled()
    page.locator("#db-vhh-apply").click()
    expect(rows).to_have_count(1)
    with page.expect_popup() as opened:
        rows.get_by_role("link", name="model-1", exact=True).click()
    result = opened.value
    expect(result.locator("#result-select")).to_have_value("journey-00")
    expect(result.locator("#candidate-title")).to_have_text("model-1")
    with result.expect_download() as fasta:
        result.locator("#save-vhh-sequence").click()
    assert Path(fasta.value.path()).read_text().splitlines() == [">model-1", "ACCDEFGHIK"]
    result.close()


@browser_check
@pytest.mark.parametrize("language", ["ko", "en"])
def test_downloaded_report_opens_in_selected_language_without_browser_preferences(analyses, page, tmp_path, language):
    from playwright.sync_api import expect

    _, url, _ = analyses
    page.goto(url + "?result=journey-00&lang=" + language)
    expect(page.locator("#candidate-table tbody tr")).to_have_count(2)
    page.locator("#export-format").select_option("report_zip")
    with page.expect_download() as downloaded:
        page.locator("#export-result").click()
    with zipfile.ZipFile(downloaded.value.path()) as archive:
        assert archive.testzip() is None
        archive.extractall(tmp_path / "delivery")
    context = page.context.browser.new_context()
    report = context.new_page()
    errors = []
    report.on("pageerror", lambda error: errors.append(str(error)))
    try:
        report.goto((tmp_path / "delivery/report.html").as_uri())
        expect(report.locator("#language-select")).to_have_value(language)
        expect(report.locator("#candidate-table tbody tr")).to_have_count(2)
        expect(report.locator("#vhh-sequence-value")).to_have_value("ACCDEFGHIK")
        report.locator("#language-select").select_option("en" if language == "ko" else "ko")
        expect(report.locator("#candidate-table tbody tr")).to_have_count(2)
        assert not errors
    finally:
        context.close()
