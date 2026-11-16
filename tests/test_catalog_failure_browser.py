"""CPU-only UI reproduction against isolated local records."""
import json
import os
import pytest
import test_exception_ui_browser as exception_tests

exception_app = exception_tests.exception_app


@pytest.fixture
def browser_workspace(exception_app, tmp_path):
    app, url = exception_app
    run = tmp_path / "runs" / "catalog-query-fixture"
    (run / "03_score").mkdir(parents=True)
    (run / "run_config.yaml").write_text("run:\n  name: catalog-query-fixture\n  mode: screen\n")
    (run / "run_state.json").write_text('{"execution":{"state":"complete"},"phases":{"final":{}}}')
    (run / "03_score" / "scored_candidates.csv").write_text(
        "candidate_id,sequence,predictor_engine,final_eligible\n" +
        "".join(f"fixture_{i},ACDEFGHIK,boltz2,false\n" for i in range(30)))
    app.state.jobs.catalog.register(run.name, run)
    return tmp_path, url


@pytest.fixture
def chromium_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width":1440,"height":1100})
        page.add_init_script("localStorage.setItem('nanocofold.language','ko')")
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url)
                if request.url.startswith("http") and "127.0.0.1" not in request.url else None)
        yield page, browser, errors, external
        browser.close()
        assert errors == [] and external == []

pytestmark = pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in Chromium acceptance")

@pytest.mark.parametrize('language', ['ko', 'en'])
@pytest.mark.parametrize('kind', ['runs', 'candidates'])
def test_failed_catalog_query_does_not_leave_stale_results(browser_workspace, chromium_page, language, kind):
    from playwright.sync_api import expect
    _, url = browser_workspace
    page, _, errors, external = chromium_page
    page.goto(url)
    page.locator('#language-select').select_option(language)
    page.locator('[data-view="catalog"]').click()
    if kind == 'candidates':
        page.locator('#show-candidate-table').click()
        table, status, next_button, search, apply, endpoint = '#db-vhh-table', '#db-vhh-status', '#db-vhh-next', '#db-vhh-search', '#db-vhh-apply', '**/api/candidate-catalog?*'
    else:
        table, status, next_button, search, apply, endpoint = '#catalog-table', '#catalog-status', '#catalog-next', '#catalog-search', '#catalog-refresh', '**/api/result-catalog?*'
    expect(page.locator(table+' tbody tr')).not_to_have_count(0)
    page.route(endpoint, lambda route: route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Temporary lookup failure'})))
    page.locator(search).fill('no-existing-result')
    page.locator(apply).click()
    expect(page.locator('#notice')).to_contain_text('Temporary lookup failure')
    expect(page.locator(table+' tbody tr')).to_have_count(0)
    expect(page.locator(next_button)).to_be_disabled()
    expect(page.locator(status)).to_contain_text('조회에 실패' if language=='ko' else 'Could not load the results database')
    page.unroute(endpoint)
    page.locator(search).fill('')
    page.locator(apply).click()
    expect(page.locator(table+' tbody tr')).not_to_have_count(0)
    assert errors == [] and external == []


@pytest.mark.parametrize('language', ['ko', 'en'])
@pytest.mark.parametrize('kind', ['runs', 'candidates'])
def test_obsolete_catalog_failure_keeps_new_success(browser_workspace, chromium_page, language, kind):
    from playwright.sync_api import expect
    _, url = browser_workspace
    page, _, _, _ = chromium_page
    page.goto(url)
    page.locator('#language-select').select_option(language)
    page.locator('[data-view="catalog"]').click()
    if kind == 'candidates':
        page.locator('#show-candidate-table').click()
        table, search, apply, endpoint = '#db-vhh-table', '#db-vhh-search', '#db-vhh-apply', '**/api/candidate-catalog?*'
    else:
        table, search, apply, endpoint = '#catalog-table', '#catalog-search', '#catalog-refresh', '**/api/result-catalog?*'
    expect(page.locator(table+' tbody tr')).not_to_have_count(0)
    held = []
    def delay_obsolete(route):
        if 'obsolete-request' in route.request.url:
            held.append(route)
        else:
            route.continue_()
    page.route(endpoint, delay_obsolete)
    page.locator(search).fill('obsolete-request')
    with page.expect_request(lambda request: 'obsolete-request' in request.url):
        page.locator(apply).click()
    page.locator(search).fill('')
    # Re-entering the DB starts a new query while the old search is still pending.
    page.locator('[data-view="jobs"]').click()
    page.locator('[data-view="catalog"]').click()
    expect(page.locator(table+' tbody tr')).not_to_have_count(0)
    assert len(held) == 1
    with page.expect_response(lambda response: 'obsolete-request' in response.url) as old_response:
        held[0].fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'Late obsolete failure'}))
    old_response.value.finished()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('#notice')).not_to_contain_text('Late obsolete failure')
    expect(page.locator(table+' tbody tr')).not_to_have_count(0)
