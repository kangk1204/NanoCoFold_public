"""Exception UX against a real idle app; job API responses are browser fixtures."""
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_exception_ui_node_contract():
    subprocess.run(['node', '--test', str(ROOT / 'tests/exception_ui_contract.mjs')],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def exception_app(tmp_path):
    import uvicorn
    from nanocofold.install import init_workspace
    from nanocofold.platform.app import create_app
    init_workspace(tmp_path)
    app = create_app(tmp_path, start_workers=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    try:
        yield app, f'http://127.0.0.1:{port}/'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert app.state.jobs.store.list() == []


def browser_fixture(page, jobs, posts):
    def route(request):
        path = request.request.url.split('/api/jobs', 1)[1]
        if request.request.method == 'POST':
            posts.append(path)
            request.abort('failed')
        elif not path or path.startswith('?'):
            request.fulfill(json={'jobs': jobs})
        elif '/logs' in path:
            request.fulfill(json={'text': 'Saved log\n', 'next_offset': 10})
        else:
            job = next(row for row in jobs if row['id'] == path.strip('/'))
            request.fulfill(json=job)
    page.route('**/api/jobs**', route)


def job(identifier, name, status='running'):
    return {'id': identifier, 'name': name, 'kind': 'analysis', 'mode': 'screen',
            'state': status, 'phase': 'prediction', 'mock': False, 'progress': {},
            'can_resume': status == 'interrupted', 'elapsed_seconds': 10,
            'result_id': None}


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium exception gate')
def test_jobs_back_forward_reload_preserve_selected_job(exception_app):
    from playwright.sync_api import expect, sync_playwright
    _, url = exception_app
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        posts = []
        browser_fixture(page, [job('a' * 32, 'Alpha'), job('b' * 32, 'Beta')], posts)
        page.goto(url)
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        page.locator('[data-view="jobs"]').click()
        page.locator('#job-list button', has_text='Beta').click()
        expect(page.locator('#job-title')).to_have_text('Beta')
        page.locator('[data-view="results"]').click()
        page.go_back()
        expect(page.locator('#view-jobs')).to_be_visible()
        expect(page.locator('#job-title')).to_have_text('Beta')
        page.go_forward()
        expect(page.locator('#view-results')).to_be_visible()
        page.go_back()
        page.reload()
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        expect(page.locator('#view-jobs')).to_be_visible()
        expect(page.locator('#job-title')).to_have_text('Beta')
        assert posts == []
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium exception gate')
@pytest.mark.parametrize('operation,status', [('cancel', 'running'), ('resume', 'interrupted')])
def test_lost_job_operation_response_is_explained_and_not_replayed(exception_app, operation, status):
    from playwright.sync_api import expect, sync_playwright
    _, url = exception_app
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('nanocofold.language','ko')")
        posts = []
        browser_fixture(page, [job('a' * 32, 'Alpha', status)], posts)
        page.goto(url)
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        page.locator('[data-view="jobs"]').click()
        button = page.locator('#job-actions button', has_text='작업 취소' if operation == 'cancel' else '검증 후 재개')
        button.click()
        if operation == 'cancel':
            expect(page.locator('#cancel-confirmation')).to_be_visible()
            page.locator('#cancel-confirmation-submit').click()
        expect(page.locator('#notice')).to_contain_text('처리 상태를 확인하지 못했습니다')
        expect(page.locator('#notice')).to_contain_text('새로고침')
        page.locator('#refresh-jobs').click()
        assert posts == ['/' + 'a' * 32 + '/' + operation]
        page.locator('#language-select').select_option('en')
        expect(page.locator('#notice')).to_contain_text('The operation status could not be confirmed')
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium exception gate')
def test_batch_partial_submission_retry_preserves_review_selection_and_budget(exception_app):
    from playwright.sync_api import expect, sync_playwright
    _, url = exception_app
    batch_id = 'c' * 32
    batch = {'id': batch_id, 'name': 'Recovery batch', 'selected': ['one', 'two'],
             'budget_seconds': 120, 'review_sha256': 'unchanged-review',
             'rows': [{'id': 'one', 'job_id': 'a' * 32,
                       'job': job('a' * 32, 'Alpha', 'interrupted') | {'run_dir': '/saved/run', 'result_available': True}},
                      {'id': 'two', 'job_id': None, 'submission_error': 'Admission temporarily unavailable'}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('nanocofold.language','ko')")
        submissions = []
        def batch_route(route):
            if route.request.method == 'POST':
                submissions.append(route.request.post_data_json)
                batch['rows'][1].update(job_id='b' * 32, job=job('b' * 32, 'Beta', 'queued'))
                batch['rows'][1].pop('submission_error')
                route.fulfill(json=batch)
            elif route.request.url.endswith('/api/batches'):
                route.fulfill(json={'batches': [batch]})
            else:
                route.fulfill(json=batch)
        page.route('**/api/batches**', batch_route)
        page.goto(url + '?view=batch')
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        page.locator('#batch-history button').click()
        expect(page.locator('#batch-submit')).to_have_text('미제출 항목 다시 제출')
        expect(page.locator('#batch-submit')).to_be_enabled()
        expect(page.locator('#batch-rows')).to_contain_text('부분 결과 열기')
        expect(page.locator('#batch-rows')).to_contain_text('중단 전까지 저장된 부분 결과입니다. 완료·미완료 항목을 확인하세요.')
        page.locator('#batch-submit').click()
        expect(page.locator('#batch-submit')).to_be_disabled()
        assert submissions == [{'review_sha256': 'unchanged-review', 'selected': ['one', 'two'], 'budget_seconds': 120}]
        page.locator('#language-select').select_option('en')
        expect(page.locator('#batch-rows')).to_contain_text('Open partial results')
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium exception gate')
def test_cancelled_queued_batch_job_has_no_partial_result_link(exception_app):
    from playwright.sync_api import expect, sync_playwright
    _, url = exception_app
    batch = {'id': 'd' * 32, 'name': 'Cancelled before start', 'selected': ['one'],
             'rows': [{'id': 'one', 'job_id': 'a' * 32,
                       'job': job('a' * 32, 'Alpha', 'cancelled') | {
                           'run_dir': '/never-created/run', 'result_available': False}}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        def route(request):
            request.fulfill(json={'batches': [batch]} if request.request.url.endswith('/api/batches') else batch)
        page.route('**/api/batches**', route)
        page.goto(url + '?view=batch')
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        page.locator('#batch-history button').click()
        expect(page.locator('#batch-rows tbody tr')).to_have_count(1)
        expect(page.locator('#batch-rows a')).to_have_count(0)
        page.locator('#language-select').select_option('ko')
        expect(page.locator('#batch-rows a')).to_have_count(0)
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST') != '1', reason='Real Chromium exception gate')
def test_resume_minutes_survive_polling_and_job_navigation_and_submit_exact_budget(exception_app):
    from playwright.sync_api import expect, sync_playwright
    _, url = exception_app
    alpha = job('a' * 32, 'Alpha', 'budget_exhausted') | {'can_resume': True}
    beta = job('b' * 32, 'Beta')
    jobs = [alpha, beta]
    posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('nanocofold.language','ko')")
        def route(request):
            suffix = request.request.url.split('/api/jobs', 1)[1]
            if request.request.method == 'POST':
                posts.append((suffix, request.request.post_data_json))
                alpha.update(state='queued', can_resume=False)
                request.fulfill(json=alpha)
            elif not suffix:
                request.fulfill(json={'jobs': jobs})
            elif '/logs' in suffix:
                request.fulfill(json={'text': '', 'next_offset': 0})
            else:
                request.fulfill(json=next(row for row in jobs if row['id'] == suffix.strip('/')))
        page.route('**/api/jobs**', route)
        page.goto(url + '?view=jobs')
        expect(page.locator('body')).to_have_attribute('data-startup', 'ready')
        # Exercise the resumable job explicitly, regardless of list ordering.
        page.locator('#job-list button', has_text='Alpha').click()
        expect(page.locator('#job-title')).to_have_text('Alpha')
        minutes = page.locator('#job-actions input')
        minutes.fill('17')
        minutes.focus()
        page.wait_for_timeout(2500)
        expect(minutes).to_have_value('17')
        assert minutes.evaluate('(n)=>document.activeElement===n') is True
        page.locator('#job-list button', has_text='Beta').click()
        expect(page.locator('#job-title')).to_have_text('Beta')
        page.locator('#job-list button', has_text='Alpha').click()
        expect(page.locator('#job-title')).to_have_text('Alpha')
        expect(minutes).to_have_value('17')
        page.locator('#job-actions button', has_text='검증 후 재개').evaluate('(n)=>{n.click();n.click();}')
        expect(minutes).to_have_count(0)
        expect(page.locator('#job-actions button', has_text='작업 취소')).to_be_enabled()
        assert posts == [('/' + 'a' * 32 + '/resume', {'additional_budget_seconds': 1020})]
        browser.close()
