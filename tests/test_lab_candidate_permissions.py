"""Authorization and workbook contents using only temporary analysis fixtures."""

import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from nanocofold.platform.app import create_app
from nanocofold.platform.lab import LabAccounts
from test_vhh_catalog import saved


def sign_in(app, name, password):
    client = TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)
    signed = client.post(
        "/lab/login", headers={"origin": "http://127.0.0.1"}, data={"name": name, "password": password}
    )
    assert signed.status_code == 303
    token = re.search(r'window.PLATFORM_TOKEN = "([0-9a-f]+)"', client.get("/").text)
    if token:
        client.headers["x-platform-token"] = token[1]
    return client


def test_only_admin_manages_sharing_and_every_result_path_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    lab = LabAccounts(work)
    code = lab.initialize_admin()
    for user in ("Kim", "Song"):
        lab.set_password(user, "local-password-" + user)
    app = create_app(work, start_workers=False)
    manager = app.state.jobs
    for name, owner in [("first", "Kim"), ("second", "Song"), ("legacy", None)]:
        root = saved(work, name)
        manager.catalog.register(name, root)
        if owner:
            with manager.store.connect() as db:
                db.execute("INSERT INTO lab_usage VALUES(?,?,?,?,?,?)", (name, name, name, owner, 1.0, name))
    a = sign_in(app, "admin", "admin")
    activation_page = a.get("/")
    assert "Activation code" in activation_page.text
    assert activation_page.headers["referrer-policy"] == "same-origin"
    assert a.get("/api/candidate-catalog").status_code == 403
    origin = {"origin": "http://127.0.0.1"}
    assert (
        "Check the code"
        in a.post(
            "/lab/activate",
            headers=origin,
            data={"code": "incorrect", "password": "secure-local-test", "confirm": "secure-local-test"},
        ).text
    )
    assert (
        a.post(
            "/lab/activate",
            headers=origin,
            data={"code": code, "password": "secure-local-test", "confirm": "secure-local-test"},
        ).status_code
        == 303
    )
    assert not lab.login("admin", "admin")
    admin = sign_in(app, "admin", "secure-local-test")
    kim = sign_in(app, "Kim", "local-password-Kim")
    assert admin.get("/api/session").json()["lab_admin"] is True
    assert not kim.get("/api/session").json()["lab_shared"]
    assert kim.post("/api/lab/sharing", json={"shared": True, "admin": True}).status_code == 403
    assert [x["id"] for x in kim.get("/api/result-catalog?researcher=Song").json()["items"]] == ["first"]
    assert [x["id"] for x in kim.get("/api/results").json()["results"]] == ["first"]
    for url in (
        "",
        "/structure?candidate_id=vhh-1",
        "/candidates",
        "/observations",
        "/artifacts",
        "/retention",
        "/download?path=run_config.yaml",
    ):
        assert kim.get("/api/results/second" + url).status_code == 404, url
    assert kim.post("/api/results/second/export", json={"format": "xlsx"}).status_code == 404
    assert kim.post("/api/results/open", json={"path": str(work / "runs/second")}).status_code == 403
    assert (
        kim.post("/api/environment/status", json={"config_path": "runs/second/run_config.yaml"}).status_code
        == 403
    )
    assert (
        kim.post("/api/jobs", json={"config": {"target": {"fasta": "runs/second/run_config.yaml"}}}).status_code
        == 403
    )
    assert kim.get("/api/candidate-catalog?researcher=Song").json()["total"] == 3
    assert admin.get("/api/candidate-catalog").json()["total"] == 9
    sheet = load_workbook(
        io.BytesIO(kim.get("/api/candidate-catalog/export?researcher=Song&lang=en").content), read_only=True
    ).active
    assert {r[12] for r in list(sheet.values)[1:]} == {"first"}
    assert admin.post("/api/lab/sharing", json={"shared": True}).json() == {"shared": True}
    assert kim.get("/api/results/second").status_code == 200
    assert kim.get("/api/candidate-catalog").json()["total"] == 9
    assert admin.post("/api/lab/sharing", json={"shared": False}).json() == {"shared": False}
    assert kim.get("/api/results/second").status_code == 404
    assert kim.get("/api/results/legacy").status_code == 404
    assert admin.get("/api/results/legacy").status_code == 200
    with manager.catalog.connect() as db:
        db.execute("UPDATE results SET vhh_index=0 WHERE id='second'")
    assert admin.get("/api/candidate-catalog").json()["sequence_index_pending"] == 1
    for endpoint in ("/api/candidate-catalog", "/api/result-catalog"):
        assert not kim.get(endpoint).json().get("sequence_index_pending")
    for client in (a, admin, kim):
        client.close()
    app.state.inputs.close()


def test_candidate_grid_sort_excel_and_legacy_index(tmp_path):
    from nanocofold.platform import candidate_catalog as grid
    from nanocofold.platform.result_catalog import ResultCatalog

    cat = ResultCatalog(tmp_path)
    for run in ("alpha", "beta"):
        root = saved(tmp_path, run)
        cfg = root / "run_config.yaml"
        cfg.write_text(cfg.read_text() + "target:\n  name: EGFR\n")
        score = root / "03_score/scored_candidates.csv"
        score.write_text(
            "candidate_id,sequence,predictor_engine,final_eligible,ipsae,interface_pae\n"
            + "\n".join(f"=VH-{i},ACDEFGHIK,boltz2,true,{i / 100},{30 - i}" for i in range(30))
            + "\nheld,PPQQLL,chai1,false,,\n"
        )
        cat.register(run, root)
    args = dict(
        target="egfr", search="CDE", engine="boltz2", decision="eligible", sort="ipsae", descending=True
    )
    page = grid.page(cat, **args)
    assert page["total"] == 60 and len(page["items"]) == 25
    assert page["items"][0]["candidate_id"] == "=VH-29"
    for lang in ("en", "ko"):
        file = grid.workbook(cat, lang, **args)
        try:
            book = load_workbook(file, read_only=True)
            rows = list(book.active.rows)
            assert len(rows) == 61 and rows[1][3].value == "=VH-29" and rows[1][3].data_type == "s"
            assert rows[-1][8].value == 0 and rows[1][8].data_type == "n"
            assert [r[12].value for r in rows[1:26]] == [item["result_id"] for item in page["items"]]
            book.close()
        finally:
            file.unlink()
    with cat.connect() as db:
        db.execute("UPDATE results SET vhh_index=0")
    assert grid.page(cat)["total"] == 0 and cat.backfill_vhh() == 2
    assert grid.page(cat, **args)["total"] == 60
    with cat.connect() as db:
        db.execute("""UPDATE results SET payload=json_set(payload,'$.mock',1) WHERE id='beta'""")
    assert grid.page(cat, **args)["total"] == 30
    assert grid.page(cat, include_mock=True, **args)["total"] == 60
    for key in grid.FIELDS:
        assert grid.page(cat, sort=key, limit=1)["total"] == 31
    with pytest.raises(ValueError):
        grid.page(cat, sort="untrusted")


def test_preexisting_admin_name_never_promoted_or_reset_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    lab = LabAccounts(tmp_path / "work")
    lab.set_password("admin", "legacy-password")
    token = lab.login("admin", "legacy-password")
    assert not lab.access(token)[1]
    with pytest.raises(ValueError, match="existing account"):
        lab.initialize_admin()
    lab.promote_existing_admin()
    assert lab.access(token) is None
    assert lab.access(lab.login("admin", "legacy-password"))[1] is True


def test_disabled_researcher_name_cannot_inherit_old_results(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    lab = LabAccounts(tmp_path / "work")
    lab.set_password("Song", "first-password")
    token = lab.login("Song", "first-password")
    lab.disable("Song")
    assert lab.access(token) is None
    with pytest.raises(ValueError, match="previous researcher"):
        lab.set_password("Song", "second-password")


def test_legacy_batch_keeps_its_reviewed_input_only_for_owner(tmp_path):
    from nanocofold.install import init_workspace
    from nanocofold.platform.batches import BatchManager
    from nanocofold.platform.inputs import InputManager
    from nanocofold.platform.jobs import JobManager
    from test_exception_batch_recovery import manifest

    init_workspace(tmp_path)
    previous = InputManager(tmp_path)
    private = InputManager(tmp_path, root=tmp_path / "inputs/lab/owner")
    jobs = JobManager(tmp_path)
    try:
        record = BatchManager(previous, jobs).review(manifest(), researcher="Song")
        request = {"review_sha256": record["review_sha256"]}
        manager = BatchManager(private, jobs)
        with pytest.raises(ValueError, match="담당자"):
            manager.submit(record["id"], request, researcher="Kim", legacy_inputs=previous)
        result = manager.submit(record["id"], request, researcher="Song", legacy_inputs=previous)
        assert result["rows"][0]["job_id"]
    finally:
        private.close()
        previous.close()
        jobs.close()
