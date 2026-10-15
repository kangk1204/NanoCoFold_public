"""Review follow-ups on isolated CPU fixtures; no engine/GPU executions."""
from __future__ import annotations

import asyncio
import ast
import copy
import errno
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from nanocofold.platform import jobs
from nanocofold.platform.app import create_app
from nanocofold.platform.errors import public_error_data

ROOT = Path(__file__).resolve().parents[1]


def _manager(tmp_path, *, gpu="0"):
    manager = jobs.JobManager(tmp_path)
    job = manager.create({"gpu": gpu, "config": {"run": {"mode": "design", "mock": True},
                                             "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}})
    return manager, job


def test_shipped_translator_covers_backend_literal_errors_without_progress_corruption():
    corpus = set()
    for path in [*(ROOT / "nanocofold/platform").glob("*.py"), *(ROOT/"nanocofold"/name for name in ["wizard.py", "structure_input.py", "hotspots.py"])]:
        for node in ast.walk(ast.parse(path.read_text())):
            values = []
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                values.append(node.exc.args[0])
            if isinstance(node, ast.Dict):
                values.extend(value for key, value in zip(node.keys, node.values)
                              if isinstance(key, ast.Constant) and key.value == "error")
            corpus.update(value.value for value in values if isinstance(value, ast.Constant)
                          and isinstance(value.value, str) and any("가" <= c <= "힣" for c in value.value))
    assert len(corpus) >= 315
    script = """const fs=require('fs'),vm=require('vm'); const c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const values=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(values.map(s=>c.I18N.value(c.I18N.term(s)))));"""
    raw = sorted(corpus)
    translated = json.loads(subprocess.run(["node", "-e", script, str(ROOT/"nanocofold/templates/platform-i18n.js")],
                                            input=json.dumps(raw), capture_output=True, text=True, check=True).stdout)
    failed = [source for source, english in zip(raw, translated) if any("가" <= c <= "힣" for c in english)]
    assert failed == [], "\n".join(failed)


@pytest.mark.parametrize("operation", ["open", "retention"])
def test_result_catalog_and_retention_summary_run_off_event_loop(tmp_path, monkeypatch, operation):
    app = create_app(tmp_path, start_workers=False)
    root = tmp_path/"runs"/"sample"; root.mkdir(parents=True)
    (root/"run_config.yaml").write_text("run:\n  mode: screen\n")
    (root/"run_state.json").write_text('{"execution":{"state":"cancelled"}}')
    loop_thread = threading.get_ident()
    calls = []
    def register(*args, **kwargs):
        calls.append(threading.get_ident())
        time.sleep(.08)
    monkeypatch.setattr(app.state.jobs.catalog, "register", register)
    if operation == "retention":
        from nanocofold.platform.retention import RetentionManager
        monkeypatch.setattr(RetentionManager, "apply", lambda *_args, **_kwargs: {"ok": True})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                    headers={"x-platform-token":app.state.token}) as client:
            url = "/api/results/open" if operation == "open" else "/api/results/sample/retention"
            response = await client.post(url, json={"path":str(root)})
            assert response.status_code == 200, response.text
    asyncio.run(exercise())
    assert calls and all(thread != loop_thread for thread in calls)
    app.state.inputs.close()


@pytest.mark.parametrize("failure", ["lock", "log", "popen"])
def test_launch_oserror_fails_only_one_job_and_keeps_queue_moving(tmp_path, monkeypatch, failure):
    manager, first = _manager(tmp_path, gpu="0")
    second = manager.create({"gpu":"1", "config":{"run":{"mode":"design", "mock":True},
                                                 "target":{"sequence":"LMNPQRSTVWYACDEFGHIK"}}})
    marker = Path(first["run_dir"]); marker.mkdir(parents=True); (marker/"keep.txt").write_bytes(b"preserved")
    actual_open = Path.open
    def bad_open(path, *args, **kwargs):
        if (failure == "lock" and path.name == "gpu-0.lock" or
                failure == "log" and str(path) == first["log_path"]):
            raise OSError(errno.EACCES, "fixture denied")
        return actual_open(path, *args, **kwargs)
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR", str(tmp_path/"locks"))
    monkeypatch.setattr(Path, "open", bad_open)
    actual_spawn = manager._spawn
    called = []
    def spawn(job):
        called.append(job["id"])
        if job["id"] == second["id"]:
            return False  # No actual worker/engine process is launched.
        return actual_spawn(job)
    monkeypatch.setattr(manager, "_spawn", spawn)
    if failure == "popen":
        monkeypatch.setattr(jobs.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(OSError(errno.ENOSPC,"fixture full")))
    manager._tick()
    assert called == [first["id"], second["id"]]
    current = manager.store.get(first["id"])
    assert current["state"] == "failed" and "권한" in current["error"]
    assert manager.store.get(second["id"])["state"] == "queued"
    assert (marker/"keep.txt").read_bytes() == b"preserved"
    manager.close()


def test_reference_reason_paths_are_sanitized_without_mutating_records():
    original={"reference":{"available":False,"reason":"참조 구조 오류 /lustre/private project/model.cif: 파일 확인"},
              "exclusion_reason":"recorded_scientific_reason", "candidate_id":"완료"}
    before=copy.deepcopy(original)
    result=public_error_data(original)
    assert "/lustre/" not in result["reference"]["reason"] and "private project" not in result["reference"]["reason"]
    assert result["candidate_id"] == "완료" and result["exclusion_reason"] == original["exclusion_reason"]
    assert original == before


def test_process_death_during_postterminal_indexing_is_recovered_after_restart(tmp_path):
    """Exercise actual worker.execute; replace inference and indexing by CPU fixtures."""
    import signal
    import sys
    manager, job = _manager(tmp_path)
    seen = tmp_path/"register-entered"
    script = r'''
import json,os,sys,time
from pathlib import Path
from nanocofold import cli
from nanocofold.platform import jobs,worker
from nanocofold.platform.result_catalog import ResultCatalog
workspace,identifier,seen=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3])
store=jobs.JobStore(workspace);job=store.get(identifier)
store.update(identifier,state="running",pid=os.getpid(),process_stamp=jobs.process_stamp(os.getpid()),started=time.time())
def run(_args):
 root=Path(job['run_dir']);(root/'03_score').mkdir(parents=True)
 (root/'run_config.yaml').write_text('run:\n  mode: design\n  mock: true\n'.replace('\\n','\n'))
 (root/'run_state.json').write_text(json.dumps({'execution':{'state':'failed'}}))
 (root/'03_score/scored_candidates.csv').write_text('design_id,sequence,prediction_error\npreserved,ACDEFG,fixture failure\n'.replace('\\n','\n'))
 (root/'keep.bin').write_bytes(b'preserved result bytes')
 return 1
cli.main=run
original=ResultCatalog.register
def register(self,*args,**kwargs):
 seen.write_text('inside register')
 time.sleep(30)
 return original(self,*args,**kwargs)
ResultCatalog.register=register
worker.execute(workspace,identifier)
'''
    child=subprocess.Popen([sys.executable,"-c",script,str(tmp_path),job["id"],str(seen)],
                           cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           env={**os.environ,"CUDA_VISIBLE_DEVICES":""})
    try:
        for _ in range(200):
            if seen.exists():break
            assert child.poll() is None
            time.sleep(.025)
        assert seen.exists()
        before=manager.store.get(job["id"])
        assert before["state"] == "failed" and before["catalog_pending"] is True
        child.kill();child.wait(timeout=5)
        recovered=jobs.JobManager(tmp_path)
        recovered._tick()
        assert recovered.catalog_thread is not None
        recovered.catalog_thread.join(timeout=10)
        current=recovered.store.get(job["id"])
        assert not current["catalog_pending"] and current["state"] == "failed"
        assert recovered.catalog.get(job["id"])["status"] == "failed"
        assert (Path(job["run_dir"])/"keep.bin").read_bytes() == b"preserved result bytes"
        recovered.close()
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)


def _pending_result(tmp_path):
    manager, job = _manager(tmp_path)
    root=Path(job["run_dir"]);root.mkdir(parents=True)
    (root/"run_config.yaml").write_text("run:\n  mode: design\n  mock: true\n")
    (root/"run_state.json").write_text('{"execution":{"state":"cancelled"}}')
    manager.store.update(job["id"],state="running",pid=None,process_stamp=None)
    terminal=manager.store.finish(job["id"],state="cancelled",can_resume=True)
    assert terminal["catalog_pending"]
    return manager,terminal


def test_index_recovery_blocks_only_same_job_resume_and_releases_lock(tmp_path,monkeypatch):
    manager,job=_pending_result(tmp_path)
    entered,release=threading.Event(),threading.Event()
    original=manager.catalog.register
    def slow(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(manager.catalog,"register",slow)
    manager._start_catalog_recovery()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError,match="복구 중"):
            manager.resume(job["id"])
        assert manager.store.get(job["id"])["state"] == "cancelled"
    finally:
        release.set();manager.catalog_thread.join(timeout=10)
    assert manager.resume(job["id"])["state"] == "queued"


def test_index_recovery_failure_retries_with_backoff_and_ignores_old_finished_jobs(tmp_path,monkeypatch):
    manager,job=_pending_result(tmp_path)
    calls=[]
    def fail(*_args,**_kwargs):
        calls.append(True);raise OSError(errno.ENOSPC,"fixture full")
    monkeypatch.setattr(manager.catalog,"register",fail)
    manager._start_catalog_recovery();manager.catalog_thread.join(timeout=5)
    assert manager.store.get(job["id"])["catalog_pending"] is True
    for _ in range(5):manager._start_catalog_recovery()
    assert len(calls)==1 and manager.catalog_retry_after[job["id"]]>time.monotonic()
    manager.store.update(job["id"],catalog_pending=False)
    manager.catalog_retry_after.clear();manager._start_catalog_recovery()
    assert len(calls)==1


def test_stale_index_recovery_cannot_clear_or_register_a_resumed_attempt(tmp_path,monkeypatch):
    manager,job=_pending_result(tmp_path)
    manager.resume(job["id"])
    called=[];monkeypatch.setattr(manager.catalog,"register",lambda *_a,**_k:called.append(True))
    manager._recover_catalog(job)
    assert called==[] and manager.store.get(job["id"])["state"]=="queued"


def test_wizard_default_port_is_distinct_and_i18n_asset_is_served(tmp_path):
    import inspect
    import urllib.request
    from nanocofold import wizard
    assert inspect.signature(wizard.create_server).parameters["port"].default == 8766
    assert inspect.signature(wizard.serve).parameters["port"].default == 8766
    server=wizard.create_server(tmp_path,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base+"/") as response:document=response.read().decode()
        assert '/assets/platform-i18n.js' in document and 'id="language-select"' in document
        with urllib.request.urlopen(base+"/assets/platform-i18n.js") as response:
            assert response.read() == (ROOT/"nanocofold/templates/platform-i18n.js").read_bytes()
    finally:
        server.shutdown();server.server_close();server.session.close();thread.join(timeout=5)


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST")!="1",reason="Real Chromium bilingual wizard gate")
def test_wizard_language_switch_preserves_inputs_and_raw_names_and_translates_errors(tmp_path):
    from playwright.sync_api import expect,sync_playwright
    from nanocofold import wizard
    server=wizard.create_server(tmp_path,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            page=browser.new_page();errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/?lang=en")
            expect(page.locator("h1")).to_have_text("NanoCoFold input wizard")
            expect(page.locator("#loadFile")).to_have_text("Load local structure")
            page.locator("#pdbId").fill("1BRS")
            page.locator("#language-select").select_option("ko")
            expect(page.locator("h1")).to_have_text("NanoCoFold 입력 마법사")
            expect(page.locator("#pdbId")).to_have_value("1BRS")
            page.locator("#language-select").select_option("en")
            page.locator("#loadFile").click()
            expect(page.locator("#loadStatus")).to_contain_text("empty or exceeds 20 MB")
            page.locator("#language-select").select_option("ko")
            expect(page.locator("#loadStatus")).to_contain_text("비어 있거나")
            page.locator("#language-select").select_option("en")
            page.route("**/api/load",lambda route:route.fulfill(json={"input_id":"fixture","source":{"kind":"upload","filename":"완료.pdb"},"warnings":[],"removed":[],"chains":[{"id":"A","original_id":"X","author_chain":"X","name":"완료","organism":"한국어 종","length":2,"residues":[{"original_number":1},{"original_number":2}]}]}))
            page.get_by_text("Advanced: paste structure text",exact=True).click()
            page.locator("#fileText").fill("ATOM fixture\n")
            page.locator("#loadFile").click()
            expect(page.locator("#loadStatus")).to_have_text("Local structure loaded.")
            expect(page.locator("#chainTable td").nth(2)).to_have_text("완료")
            page.locator('input[name="target"][value="A"]').check()
            for language in ["ko","en","ko","en"]:
                page.locator("#language-select").select_option(language)
                expect(page.locator('input[name="target"][value="A"]')).to_be_checked()
                expect(page.locator("#chainTable td").nth(2)).to_have_text("완료")
                expect(page.locator("#pdbId")).to_have_value("1BRS")
            assert errors == []
            browser.close()
    finally:
        server.shutdown();server.server_close();server.session.close();thread.join(timeout=5)


def test_wizard_mapping_csv_quotes_untrusted_description_but_keeps_manifest_data(tmp_path):
    import csv
    from nanocofold import mock,wizard
    from nanocofold.utils import pdb_io
    structure=mock.build_complex_structure("ACDEFGHIKLMNPQRSTVWY","ACCDEFGHIK",[],seed=42)
    source=tmp_path/"input.pdb";pdb_io.save_structure(structure,source)
    session=wizard.WizardSession(tmp_path/"exports")
    try:
        loaded=session.load({"filename":"input.pdb","content":source.read_text()})
        target=loaded["chains"][0]["id"]
        session.input["chains"][0]["name"]="=SUM(1,2)"
        result=session.analyze({"input_id":loaded["input_id"],"target":target,"context":[]})
        position=next(row["position"] for row in result["residues"] if row["relative_sasa"] is not None and row["relative_sasa"]>=.15)
        exported=session.export({"selection_id":result["selection_id"],"hotspots":[position],"acknowledge":True})
        root=Path(exported["export_dir"])
        with (root/"residue_mapping.csv").open(encoding="utf-8-sig") as handle:rows=list(csv.DictReader(handle))
        assert rows and rows[0]["protein"]=="'=SUM(1,2)"
        assert json.loads((root/"input_review.json").read_text())["target"]["name"]=="=SUM(1,2)"
        guide=(root/"README.md").read_text()
        assert "No GPU inference was run." in guide and "검토한 입력" in guide
    finally:session.close()


def test_cancelling_queued_resume_retains_partial_result_index_recovery(tmp_path):
    manager,job=_pending_result(tmp_path)
    assert manager.resume(job["id"])["state"]=="queued"
    cancelled=manager.cancel(job["id"])
    assert cancelled["state"]=="cancelled" and cancelled["catalog_pending"] is True
    manager._start_catalog_recovery();manager.catalog_thread.join(timeout=10)
    assert manager.catalog.get(job["id"])["status"]=="cancelled"
    assert manager.store.get(job["id"])["catalog_pending"] is False
    manager.close()
