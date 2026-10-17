"""Navigation races against an idle local app with synthetic API jobs only."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_exception_ui_browser import exception_app, job

ROOT = Path(__file__).resolve().parents[1]
navigation_app = exception_app


def test_p2_navigation_node_contract():
    subprocess.run(['node', '--test', str(ROOT / 'tests/p2_navigation_contract.mjs')],
                   check=True, capture_output=True, text=True)


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium navigation gate')
@pytest.mark.parametrize('latest', ['forward', 'different_tab', 'same_tab'])
def test_delayed_history_restore_respects_latest_navigation(navigation_app, latest):
    import nanocofold
    from playwright.sync_api import expect, sync_playwright
    app, url = navigation_app
    alpha = job('a' * 32, 'Alpha', 'budget_exhausted') | {'can_resume': True}
    beta = job('b' * 32, 'Beta')
    jobs, posts, held, errors, status_reads = [alpha, beta], [], [], [], []
    hold = {'id': None}
    source = Path(os.environ.get('NANOCOFOLD_UI_SOURCE', ROOT / 'nanocofold/templates/platform.js'))
    trace = {'scenario': latest, 'start_workers': False, 'fixture_jobs': True,
             'imported_package': nanocofold.__file__, 'ui_source': str(source),
             'ui_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.add_init_script("""localStorage.setItem('nanocofold.language','en');
            window.__testIntervals=[]; const interval=setInterval;
            window.setInterval=(...args)=>{const id=interval(...args);window.__testIntervals.push(id);return id;};""")
        page.on('pageerror', lambda error: errors.append(str(error)))

        def api(route):
            if route.request.url.endswith('/api/environment/status'):
                # This endpoint uses POST for a read-only receipt lookup. Keep it
                # synthetic too, without triggering diagnostics or GPU probes.
                status_reads.append(route.request.url)
                route.fulfill(json={'ready': False, 'reason': 'no_record'})
                return
            if route.request.method == 'POST':
                posts.append(route.request.url)
                route.abort('failed')
                return
            if '/api/jobs' not in route.request.url:
                route.continue_()
                return
            suffix = route.request.url.split('/api/jobs', 1)[1]
            if not suffix:
                route.fulfill(json={'jobs': jobs})
            elif '/logs' in suffix:
                route.fulfill(json={'text': '', 'next_offset': 0})
            elif suffix.strip('/') == hold['id']:
                hold['id'] = None
                held.append(route)
            else:
                route.fulfill(json=next(row for row in jobs if row['id'] == suffix.strip('/')))

        page.route('**/api/**', api)
        if os.environ.get('NANOCOFOLD_UI_SOURCE'):
            page.route('**/assets/platform.js', lambda route: route.fulfill(
                path=source, content_type='application/javascript'))
        page.goto(url + '?view=jobs#candidate-details')
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        page.evaluate('window.__testIntervals.forEach(clearInterval)')
        page.wait_for_function('() => !state.pollingJobs')
        # Start this history scenario from the intended resumable job.
        page.locator('#job-list button', has_text='Alpha').click()
        expect(page.locator('#job-title')).to_have_text('Alpha')
        field = page.locator('#job-actions input')
        field.fill('17')
        # Native history: jobs Alpha -> results Alpha -> jobs Beta -> results Beta.
        page.locator('[data-view="results"]').click()
        page.locator('[data-view="jobs"]').click()
        page.locator('#job-list button', has_text='Beta').click()
        expect(page.locator('#job-title')).to_have_text('Beta')
        page.locator('[data-view="results"]').click()
        page.go_back()
        expect(page.locator('#view-jobs')).to_be_visible()
        page.wait_for_function('() => !state.pollingJobs')
        # The next Back targets results Alpha, but its job status response stalls.
        hold['id'] = alpha['id']
        page.go_back()
        page.wait_for_function('() => state.job === ' + json.dumps(alpha['id']))
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(10)
        assert held, 'Expected held Alpha status response'
        trace['pending'] = page.evaluate('({url:location.href,view:state.currentView,job:state.job})')
        if latest == 'forward':
            page.go_forward()
            expected_view, expected_job = 'jobs', beta['id']
        else:
            expected_view = 'input' if latest == 'different_tab' else 'jobs'
            page.locator('[data-view="' + expected_view + '"]').click()
            expected_job = alpha['id']
        trace['before_release'] = page.evaluate('({url:location.href,view:state.currentView,job:state.job})')
        held.pop().fulfill(json=alpha)
        page.wait_for_function('() => !state.pollingJobs')
        page.wait_for_timeout(100)
        trace['after_release'] = page.evaluate('({url:location.href,view:state.currentView,job:state.job,title:document.querySelector("#job-title").textContent})')
        trace.update(mutation_posts=posts, read_only_status_post_count=len(status_reads), page_errors=errors)
        evidence_dir = os.environ.get('NANOCOFOLD_UI_EVIDENCE_DIR')

        def save_evidence(*, screenshot=True):
            if evidence_dir:
                output = Path(evidence_dir)
                output.mkdir(parents=True, exist_ok=True)
                (output / ('ui-navigation-' + latest + '.json')).write_text(json.dumps(trace, indent=2) + '\n')
                if screenshot:
                    page.screenshot(path=str(output / ('ui-navigation-' + latest + '.png')), full_page=True)

        save_evidence()
        assert trace['after_release']['view'] == expected_view
        assert trace['after_release']['job'] == expected_job
        assert page.evaluate('new URL(location.href).searchParams.get("view")') == expected_view
        assert page.evaluate('location.hash') == '#candidate-details'
        expect(page.locator('#job-title')).to_have_text('Beta' if latest == 'forward' else 'Alpha')
        # The draft survives interrupted restoration, native history, and polling.
        page.locator('[data-view="jobs"]').click()
        page.locator('#job-list button', has_text='Alpha').click()
        expect(page.locator('#job-title')).to_have_text('Alpha')
        expect(field).to_have_value('17')
        field.focus()
        page.evaluate('() => refreshJobs()')
        expect(field).to_have_value('17')
        assert field.evaluate('(node) => document.activeElement === node')
        assert posts == []
        assert errors == []
        actual_jobstore_count = len(app.state.jobs.store.list())
        assert actual_jobstore_count == 0
        trace.update(resume_minutes=field.input_value(), resume_focused=True, preservation_passed=True,
                     actual_jobstore_count=actual_jobstore_count)
        save_evidence(screenshot=False)
        browser.close()
