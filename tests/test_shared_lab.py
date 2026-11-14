"""One trusted research group, one result catalog, distinct identities and GPU admission."""
import asyncio
import json
import re

import httpx
import pytest

from nanocofold.platform import gpu_admission
from nanocofold.platform.app import create_app
from nanocofold.platform.jobs import JobManager
from nanocofold.platform.lab import LabAccounts


def analysis(name):
    return {"name": name, "config": {"run": {"mode": "design", "mock": True},
                                     "target": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}}


def test_account_session_drafts_and_attribution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "work"
    app = create_app(root, start_workers=False)
    users = LabAccounts(root)
    origin = "http://127.0.0.1"
    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=origin) as a, httpx.AsyncClient(transport=transport, base_url=origin) as b:
            assert "__TOKEN__" not in (await a.get("/")).text
            users.set_password("송윤진", "test-alpha-private")
            users.set_password("Beta", "test-beta-private")
            assert (await a.get("/api/jobs",headers={"X-Platform-Token":app.state.token})).status_code == 401
            assert (await a.get("/assets/platform.js")).status_code == 401
            assert (await a.post("/lab/login",data={"name":"Beta","password":"test-beta-private"})).status_code == 403
            auth = {}
            for client,user,password in ((a,"송윤진","test-alpha-private"), (b,"Beta","test-beta-private")):
                assert (await client.post("/lab/login",headers={"origin":origin},data={"name":user,"password":"wrong"})).status_code == 401
                signed = await client.post("/lab/login",headers={"origin":origin},data={"name":user,"password":password})
                assert signed.status_code == 303
                token = re.search(r'window.PLATFORM_TOKEN = "([0-9a-f]+)"', (await client.get("/")).text)[1]
                client.headers.update({"x-platform-token":token})
                auth[user]=token
                assert (await client.get("/api/session")).json()["researcher"] == user
                assert (await client.post("/api/draft",json={"version":1,"fields":{"name":user}})).status_code == 200
            assert (await a.get("/api/draft")).json()["fields"]["name"] == "송윤진"
            assert (await b.get("/api/draft")).json()["fields"]["name"] == "Beta"
            assert (await a.get("/api/jobs",headers={"x-platform-token":auth["Beta"]})).status_code == 403
            submitted = await a.post("/api/jobs",json={**analysis("first"), "researcher":"Beta","submission_id":"first-lab-request-01"})
            assert submitted.status_code == 200, submitted.text
            job = submitted.json()
            assert job["researcher"] == "송윤진"
            assert (await b.post("/api/jobs/"+job["id"]+"/cancel",json={})).status_code==403
            assert (await a.post("/api/jobs/"+job["id"]+"/cancel",json={})).json()["state"]=="cancelled"
            users.set_password("Beta","replacement-password",create=False)
            assert (await b.get("/api/jobs")).status_code==401
            assert (await a.post("/api/lab/logout",json={})).status_code==200
            assert (await a.get("/api/session")).status_code==401
            users.db.rename(users.db.with_suffix(".saved"))
            assert (await a.get("/")).status_code==503
    try:
        asyncio.run(exercise())
    finally:
        app.state.inputs.close()


def test_same_researcher_reuses_result_and_page_filtered_metadata(tmp_path):
    m = JobManager(tmp_path)
    try:
        first=m.create({**analysis("canonical"),"researcher":"Song","reuse_results":False})
        original=first["id"]
        # Exercise the durable registration transaction for an identical active job.
        data={"digest":"testing","reusable":True}
        m.store.update(original,execution_identity=data)
        duplicate=dict(first,id="active-link",name="Song repeat",researcher="Song",reuse_results=True,execution_identity=data)
        assert m.store.insert(duplicate)["id"]==original
        m.store.update(original,state="complete",result_id=original)
        alias=dict(duplicate,id="reused-link",state="complete",result_id=original,reused_result_id=original)
        assert m.store.insert(alias)["state"]=="complete"
        with m.catalog.connect() as db:
            for identifier in (original,"old"):
                record={"id":identifier,"name":"same name"}
                db.execute("""INSERT INTO results(id,created,updated,mode,retention,status,can_reuse,name,payload)
                              VALUES(?,1,1,'design','full','complete',0,'same name',?)""",(identifier,json.dumps(record)))
        assert m.catalog.list(researcher="Song")["items"]==[{"id":original,"name":"same name","researchers":["Song"]}]
        assert len(m.catalog.list(search="same")["items"])==2
        assert not m.catalog.list(researcher="unknown")["items"]
        page=m.store.usage("Song",limit=1)
        following=m.store.usage("Song",**page["next_cursor"],limit=1)
        assert following["next_cursor"] is not None and page["items"][0]["request_id"]!=following["items"][0]["request_id"]
        assert all(row["name"] == "Song repeat" and row["reused"] for row in m.store.usage("Song")["items"] if row["request_id"] != original)
        assert not m.store.usage("Kim")["items"]
        assert not (tmp_path/"runs"/"reused-link").exists()
        with m.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM lab_usage").fetchone()[0]==3
    finally:
        m.close()


UUID = "GPU-12345678-abcd-abcd-abcd-123456789abc"


@pytest.mark.parametrize(("metrics","reason"),[
    ("12000, 11300, 1",None), ("12000, 5000, 0","capacity"),
    ("12000, 12000, 90","utilization"), ("12000, [N/A], 0","telemetry"),
    ("12000, 12001, 0","telemetry"),
])
def test_only_selected_device_is_probed_with_external_work(tmp_path,monkeypatch,metrics,reason):
    from xml.etree.ElementTree import fromstring
    def fake(gpu):
        assert gpu == "7"
        total, free, util = metrics.split(", ")
        return fromstring(f"<gpu><uuid>{UUID}</uuid><fb_memory_usage><total>{total} MiB</total><free>{free} MiB</free></fb_memory_usage>"
                          f"<utilization><gpu_util>{util} %</gpu_util></utilization>"
                          "<processes><process_info><type>C</type><pid>1234</pid></process_info></processes></gpu>")
    monkeypatch.setattr(gpu_admission,"_snapshot",fake)
    with gpu_admission.shared_reservation("7",tmp_path) as (reading,handles):
        assert reading["reason"] == reason
        assert bool(handles) == (reason is None)


def test_reservations_across_servers_and_device_aliases(tmp_path,monkeypatch):
    import fcntl
    import os
    monkeypatch.setattr(gpu_admission,"shared_snapshot",lambda gpu:(UUID,"0",16000,15300,10,{}))
    with gpu_admission.shared_reservation("0",tmp_path) as (first,leases):
        assert first["reason"] is None
        with gpu_admission.shared_reservation(UUID,tmp_path) as (second,more):
            assert second["reason"] is None and second["reservations"]==1
            with gpu_admission.shared_reservation("0",tmp_path) as (third,empty):
                assert third["reason"]=="capacity" and not empty
            # Old installations and maintenance keep exclusive access until all shared workers exit.
            for alias in ["0",UUID]:
                with (tmp_path/f"gpu-{alias}.lock").open("a+") as old:
                    with pytest.raises(BlockingIOError):fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)
            import subprocess, sys
            child = subprocess.Popen([sys.executable,"-c","import sys; sys.stdin.read()"],stdin=subprocess.PIPE,
                                     pass_fds=tuple(h.fileno() for h in more))
        try:
            with gpu_admission.shared_reservation("0",tmp_path) as (reading,_):
                assert reading["reason"]=="capacity"  # exited parent, living descendant
        finally:
            child.stdin.close()
            child.wait(timeout=5)
        with gpu_admission.shared_reservation("0",tmp_path) as (replacement,_):
            assert replacement["reason"] is None
    with (tmp_path/f"gpu-{UUID}.lock").open("a+") as old:
        fcntl.flock(old,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with gpu_admission.shared_reservation("0",tmp_path) as (reading,_):
            assert reading["reason"]=="exclusive"
    # A measured allocation is already subtracted by the driver. Only reserve the remainder.
    used = gpu_admission.reservation_budget(16000,10500,{"a":6144},{"a":5400},6144)
    assert used["available_mib"] == 8156 and used["reason"] is None
    monkeypatch.setenv("NANOCOFOLD_GPU_JOB_MIB","invalid")
    with gpu_admission.shared_reservation("0",tmp_path) as (reading,_):
        assert reading["reason"]=="policy"


def test_busy_gpu_wait_cancel_and_admit_without_starting_an_engine(tmp_path, monkeypatch):
    manager=JobManager(tmp_path)
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR",str(tmp_path/"locks"))
    probes=[]
    def reading(_gpu):
        probes.append(1)
        return (UUID,"0",16000,2000 if len(probes)==1 else 15000,1,{})
    monkeypatch.setattr(gpu_admission,"shared_snapshot",reading)
    jobs=[manager.create({**analysis(name),"reuse_results":False}) for name in ("a","b")]
    for job in jobs:
        manager.store.update(job["id"],mock=False)
    for job in jobs:
        assert not manager._spawn(manager.store.get(job["id"]))
        wait=manager.store.get(job["id"])
        assert wait["state"]=="queued" and wait["phase"]=="waiting_for_gpu" and wait["started"] is None
        assert wait["gpu_reading"]["reason"]=="capacity"
    assert len(probes)==1
    assert manager.cancel(jobs[0]["id"])["state"]=="cancelled"
    manager.gpu_retry.clear()
    # Stub the process boundary only. Test the real lease and queued -> running transaction.
    from types import SimpleNamespace
    from nanocofold.platform import jobs as module
    monkeypatch.setattr(module.subprocess,"Popen",lambda *a,**kw:SimpleNamespace(pid=99999991))
    monkeypatch.setattr(module,"process_stamp",lambda _pid:"test-stamp")
    assert manager._spawn(manager.store.get(jobs[1]["id"]))
    assert manager.store.get(jobs[1]["id"])["state"]=="running"
    assert manager.store.get(jobs[0]["id"])["state"]=="cancelled"
    assert len(probes)==2


def test_queue_runs_two_analyses_and_releases_one_reservation(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    from nanocofold.platform import jobs as module

    manager = JobManager(tmp_path)
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(gpu_admission, "shared_snapshot", lambda _gpu: (UUID, "0", 16000, 15500, 5, {}))
    submitted = [manager.create({**analysis(name), "reuse_results": False}) for name in ("first","second","third")]
    for job in submitted:
        manager.store.update(job["id"], mock=False)
    owned = []
    def spawn(*args, pass_fds, **kwargs):
        owned.append([os.dup(fd) for fd in pass_fds])
        return SimpleNamespace(pid=9999910+len(owned), poll=lambda: None)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "process_stamp", lambda _pid: "fixture-birth")
    monkeypatch.setattr(manager, "_start_catalog_recovery", lambda: None)
    try:
        manager._tick()
        first, second, third = [manager.store.get(row["id"]) for row in submitted]
        assert [x["state"] for x in (first,second,third)] == ["running","running","queued"]
        assert third["gpu_reading"]["reason"] == "capacity" and third["started"] is None
        for descriptor in owned[0]:
            os.close(descriptor)
        owned[0] = []
        manager.gpu_retry.clear()
        assert manager._spawn(manager.store.get(third["id"]))
        assert manager.store.get(second["id"])["state"] == "running"
    finally:
        for handles in owned:
            for descriptor in handles:
                os.close(descriptor)


def test_shared_login_burst_queues_without_parallel_password_hashes(tmp_path, monkeypatch):
    import threading
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "lab"
    app = create_app(root, start_workers=False)
    accounts = LabAccounts(root)
    for name in ("Ada", "Bea", "Chen"):
        accounts.set_password(name, "correct-password-" + name)
    original = LabAccounts.login
    release = threading.Event()
    running = 0
    peak = 0

    def delayed(self, name, password):
        nonlocal running, peak
        running += 1
        peak = max(running, peak)
        try:
            if not release.wait(10):
                raise RuntimeError("queued login never released")
            return original(self, name, password)
        finally:
            running -= 1

    monkeypatch.setattr(LabAccounts, "login", delayed)
    async def exercise():
        origin = "http://127.0.0.1"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
            names = ["Ada", "Bea", "Chen"] * 3
            requests = [asyncio.create_task(client.post("/lab/login", headers={"origin":origin},
                        data={"name":name, "password":"correct-password-"+name})) for name in names]
            done, _ = await asyncio.wait(requests, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            release.set()
            responses = await asyncio.gather(*requests)
            assert len(done) == 3
            assert sorted(r.status_code for r in responses) == [303]*6 + [429]*3
            busy = next(r for r in responses if r.status_code == 429)
            assert busy.headers["retry-after"] and '<form' in busy.text
            for name, response in zip(names, responses):
                if response.status_code == 303:
                    assert accounts.user(response.cookies[accounts.cookie]) == name
            assert peak == 1
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        app.state.inputs.close()
