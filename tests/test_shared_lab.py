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


def test_two_researchers_reuse_one_result_and_page_filtered_metadata(tmp_path):
    m = JobManager(tmp_path)
    try:
        first=m.create({**analysis("canonical"),"researcher":"Kim","reuse_results":False})
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
        assert m.catalog.list(researcher="Song")["items"]==[{"id":original,"name":"same name","researchers":["Kim","Song"]}]
        assert len(m.catalog.list(search="same")["items"])==2
        assert not m.catalog.list(researcher="unknown")["items"]
        page=m.store.usage("Song",limit=1)
        following=m.store.usage("Song",**page["next_cursor"],limit=1)
        assert following["next_cursor"] is None and page["items"][0]["request_id"]!=following["items"][0]["request_id"]
        assert all(row["name"] == "Song repeat" and row["reused"] for row in m.store.usage("Song")["items"])
        assert not m.store.usage("Kim")["items"][0]["reused"]
        assert len(m.store.usage("Kim")["items"])==1
        assert not (tmp_path/"runs"/"reused-link").exists()
        with m.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM lab_usage").fetchone()[0]==3
    finally:
        m.close()


@pytest.mark.parametrize(("metrics","pids","reason"),[
    ("12000, 11300, 1","",""), ("12000, 11300, 1","1234","process"),
    ("12000, 6100, 0","","memory"), ("12000, 12000, 82","","utilization"),
    ("12000, [N/A], 0","","telemetry"), ("12000, 12001, 0","","telemetry"),
    ("12000, 10000, 2","[Not Supported]","telemetry"),
])
def test_only_the_selected_gpu_is_probed(monkeypatch,metrics,pids,reason):
    from xml.etree.ElementTree import fromstring
    def fake(gpu):
        assert gpu == "7"
        total, free, util = metrics.split(", ")
        kind = "C" if pids == "1234" else pids or "C+G"
        return fromstring(f"<gpu><fb_memory_usage><total>{total} MiB</total><free>{free} MiB</free></fb_memory_usage>"
                          f"<utilization><gpu_util>{util} %</gpu_util></utilization>"
                          f"<processes><process_info><type>{kind}</type></process_info></processes></gpu>")
    monkeypatch.setattr(gpu_admission,"_snapshot",fake)
    assert (gpu_admission.availability("7")["reason"] or "")==reason


def test_busy_gpu_wait_cancel_and_admit_without_starting_an_engine(tmp_path, monkeypatch):
    manager=JobManager(tmp_path)
    monkeypatch.setenv("NANOCOFOLD_GPU_LOCK_DIR",str(tmp_path/"locks"))
    probes=[]
    def reading(_gpu):
        probes.append(1)
        return {"reason":"process" if len(probes)==1 else None,"free_mib":10000,"total_mib":12000,"utilization":1}
    monkeypatch.setattr(gpu_admission,"availability",reading)
    jobs=[manager.create({**analysis(name),"reuse_results":False}) for name in ("a","b")]
    for job in jobs:
        manager.store.update(job["id"],mock=False)
    for job in jobs:
        assert not manager._spawn(manager.store.get(job["id"]))
        wait=manager.store.get(job["id"])
        assert wait["state"]=="queued" and wait["phase"]=="waiting_for_gpu" and wait["started"] is None
        assert wait["gpu_reading"]["reason"]=="process"
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
