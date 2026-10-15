"""An installation receipt enables UI preparation, never replaces native preflight."""
import copy
import time

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.app import create_app
from nanocofold.platform.inputs import normalized_config
from nanocofold.platform.environment import environment_scope, readiness


def test_scope_ignores_input_but_changes_with_engine_install_and_gpu(tmp_path):
    cfg = normalized_config(tmp_path, {"run": {"mode": "complex"}})
    original = environment_scope(tmp_path, cfg)
    changed = copy.deepcopy(cfg)
    changed["run"].update(name="another experiment", seed=17)
    changed["target"]["sequence"] = "ACDEFGHIKLMNPQRSTVWY"
    assert environment_scope(tmp_path, changed) == original
    changed.setdefault("complex", {})["comparison_engines"] = ["chai1"]
    assert environment_scope(tmp_path, changed) != original
    assert environment_scope(tmp_path, cfg, gpu="1") != original
    folder = tmp_path / ".platform"
    folder.mkdir(exist_ok=True)
    (folder / "setup-complex.json").write_text('{"status":"changed"}')
    assert environment_scope(tmp_path, cfg) != original


@pytest.mark.parametrize("state,ok,environment_only,expected", [
    ("complete", True, True, True), ("failed", True, True, False),
    ("cancelled", True, True, False), ("interrupted", True, True, False),
    ("complete", False, True, False), ("complete", "true", True, False),
    ("complete", True, False, False),
])
def test_only_explicit_completed_pass_is_reused(tmp_path, state, ok, environment_only, expected):
    app = create_app(tmp_path, start_workers=False)
    cfg = normalized_config(tmp_path, {"run": {"mode": "complex"}})
    job = app.state.jobs.create({"mode": "complex", "environment_only": environment_only}, kind="diagnostics")
    app.state.jobs.store.update(job["id"], state=state, diagnostics={"ok": ok, "environment_scope": environment_scope(tmp_path,cfg)})
    assert readiness(tmp_path, cfg, app.state.jobs.store)["ready"] is expected
    app.state.jobs.close();app.state.inputs.close()


def test_api_reuses_check_across_inputs_and_latest_failure_blocks(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"x-platform-token": app.state.token}
        payload = {"mode": "complex", "config": {"run": {"mode": "complex"}}}
        assert client.post("/api/environment/status", json=payload).status_code == 403
        status = client.post("/api/environment/status", json=payload, headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["ready"] is False
        assert app.state.jobs.store.list() == []
        job = client.post("/api/environment/check",json=payload,headers=headers).json()
        assert job["environment_only"] is True and job["kind"] == "diagnostics"
        assert job["result_id"] is None
        scope = status.json()["scope"]
        app.state.jobs.store.update(job["id"],state="complete",finished=time.time(),diagnostics={"ok":True,"environment_scope":scope})
        payload["config"]["run"].update(name="new input", seed=73)
        assert client.post("/api/environment/status",json=payload,headers=headers).json()["ready"] is True
        # A new failed check cannot be concealed by an older success.
        later = client.post("/api/environment/check",json=payload,headers=headers).json()
        app.state.jobs.store.update(later["id"],state="complete",diagnostics={"ok":False,"environment_scope":scope})
        assert client.post("/api/environment/status",json=payload,headers=headers).json()["ready"] is False
        assert len(app.state.jobs.store.list()) == 2
        assert client.post("/api/environment/status",json={"mode":"unknown"},headers=headers).status_code == 400


def test_install_check_skips_only_input_specific_msa_and_wt_checks(monkeypatch):
    import nanocofold.diagnostics as d
    from nanocofold.config import load_config
    cfg = load_config()
    monkeypatch.setattr(d,"_probe_python_env",lambda *a,**k:{"ok":True,"stage":"test"})
    monkeypatch.setattr(d,"_probe_jax",lambda *a,**k:{"ok":True})
    monkeypatch.setattr(d,"_probe_headless_plot",lambda *a,**k:{"ok":True})
    monkeypatch.setattr(d,"_probe_binary",lambda *a,**k:{"ok":True,"stage":"test"})
    monkeypatch.setattr(d,"_engine_weights",lambda *a,**k:{"ok":True})
    monkeypatch.setattr(d,"_colabfold_msa_check",lambda *a:pytest.fail("installation check inspected protein MSA"))
    report=d._probe_engine(cfg,"colabfold",{},environment_only=True)
    assert report["ok"] is True
    assert not any(c["name"]=="colabfold.msa" for c in report["checks"])
    report=d._probe_engine(cfg,"antifold",{},environment_only=True)
    assert not any(c["name"]=="antifold.wt_structure" for c in report["checks"])
    # Kernel/import failures remain blocking even for a reusable check.
    monkeypatch.setattr(d,"_probe_python_env",lambda *a,**k:{"ok":False,"stage":"test"})
    assert d._probe_engine(cfg,"colabfold",{},environment_only=True)["ok"] is False
    with pytest.raises(ValueError,match="smoke"):
        d.doctor(cfg,smoke=True,environment_only=True)


@pytest.mark.parametrize("mode",["complex","design","screen","mature"])
def test_every_starter_can_be_checked_without_input_or_transmission_consent(tmp_path,mode):
    import json
    from pathlib import Path
    presets=json.loads((Path(__file__).resolve().parents[1]/"nanocofold/_data/starter-presets.json").read_text())
    config=presets["modes"][mode]["native_override"]
    before=copy.deepcopy(config)
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        headers={"x-platform-token":app.state.token}
        payload={"mode":mode,"config":config}
        status=client.post("/api/environment/status",json=payload,headers=headers)
        assert status.status_code==200,status.text
        response=client.post("/api/environment/check",json=payload,headers=headers)
        assert response.status_code==200,response.text
        job=response.json()
        assert job["environment_only"] is True and job["result_id"] is None
        from nanocofold.config import load_config
        snapshot=load_config(job["config_path"])
        assert snapshot["screen"]["allow_public_msa_server"] is False
        assert snapshot["screen"]["colabfold"]["msa_source"]=="cache"
        assert job["environment_scope"]==status.json()["scope"]
    assert config==before


@pytest.mark.parametrize("config",[[],{"screen":None},{"run":"wrong"},{"screen":{"colabfold":[]}}])
def test_bad_installation_configuration_is_rejected_without_jobs(tmp_path,config):
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        headers={"x-platform-token":app.state.token}
        for endpoint in ("status","check"):
            response=client.post("/api/environment/"+endpoint,json={"config":config},headers=headers)
            assert response.status_code==400,response.text
        assert not app.state.jobs.store.list()
