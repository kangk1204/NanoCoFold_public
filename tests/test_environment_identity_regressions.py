"""Receipt invariants across processes and changed local installations; no engines run."""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.app import create_app
from nanocofold.platform.environment import environment_scope
from nanocofold.platform.inputs import normalized_config

ROOT = Path(__file__).resolve().parents[1]


def test_same_installation_has_same_scope_in_server_and_detached_worker(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy2(ROOT / "config/default.yaml", tmp_path / "config/default.yaml")
    config = json.loads((ROOT / "nanocofold/_data/starter-presets.json").read_text())["modes"]["complex"]["native_override"]
    app = create_app(tmp_path, start_workers=False)
    try:
        job = app.state.jobs.create({"config": config, "environment_only": True}, kind="diagnostics")
        code = """import json,sys
from pathlib import Path
from nanocofold.config import load_config
from nanocofold.platform.environment import environment_scope
print(json.dumps(environment_scope(Path(sys.argv[2]),load_config(sys.argv[1]),gpu='0')))
"""
        completed = subprocess.run([sys.executable, "-c", code, job["config_path"], str(tmp_path)],
            cwd=tmp_path, env={**os.environ, "NANOCOFOLD_ROOT": str(tmp_path),
                              "PYTHONPATH": str(ROOT), "CUDA_VISIBLE_DEVICES": ""},
            text=True, capture_output=True, check=True)
        assert json.loads(completed.stdout) == job["environment_scope"]
    finally:
        app.state.jobs.close(); app.state.inputs.close()


@pytest.mark.parametrize("mutation", ["remove_model", "change_model", "change_engine", "change_metadata"])
def test_consumed_nested_asset_changes_invalidate_receipt(tmp_path, mutation, monkeypatch):
    cfg = normalized_config(tmp_path, {"run": {"mode": "complex"}})
    cache = tmp_path / "models/boltz2"
    cache.mkdir(parents=True)
    model = cache / "boltz2_conf.ckpt"
    model.write_bytes(b"synthetic model fixture; never loaded")
    for section in (cfg["screen"], cfg["structure"]["boltz2"]):
        section.setdefault("env_vars", {})["BOLTZ_CACHE"] = str(cache)
    site = Path(cfg["screen"]["boltz2_env"]) / "lib/python3.12/site-packages"
    engine = site / "boltz/__init__.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("version = 'fixture'\n")
    metadata = site / "boltz-0.0.0.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: boltz\nVersion: fixture\n")
    original_read = Path.read_bytes
    def no_model_read(path):
        assert path != model, "Readiness must stat, not hash/load, model weights"
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", no_model_read)
    before = environment_scope(tmp_path, cfg)
    if mutation == "remove_model": model.unlink()
    elif mutation == "change_model": model.write_bytes(b"changed synthetic model fixture")
    elif mutation == "change_engine": engine.write_text("raise RuntimeError('changed fixture')\n")
    else: metadata.write_text("Name: boltz\nVersion: changed\n")
    assert environment_scope(tmp_path, cfg) != before


@pytest.mark.parametrize("explicit_engines", [False, True])
@pytest.mark.parametrize("mode_location", ["payload", "config", "both"])
def test_complex_environment_mode_and_default_engines_normalize_before_validation(tmp_path, explicit_engines, mode_location):
    from nanocofold.config import load_config
    app = create_app(tmp_path, start_workers=False)
    comparison = {"enabled": True}
    if explicit_engines: comparison["engines"] = ["boltz2", "chai1"]
    payload = {"mode": "complex", "config": {"screen": {"comparison": comparison}}}
    if mode_location != "payload": payload["config"]["run"] = {"mode": "complex"}
    if mode_location == "config": payload.pop("mode")
    original = copy.deepcopy(payload)
    expected = comparison.get("engines", normalized_config(tmp_path)["screen"]["comparison"]["engines"])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"x-platform-token": app.state.token}
        status = client.post("/api/environment/status", json=payload, headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["scope"]["engines"] == sorted(expected)
        check = client.post("/api/environment/check", json=payload, headers=headers)
        assert check.status_code == 200, check.text
        assert check.json()["environment_scope"] == status.json()["scope"]
        snapshot = load_config(check.json()["config_path"])
        assert snapshot["complex"]["comparison_engines"] == expected
        assert snapshot["screen"]["comparison"]["enabled"] is False
    assert payload == original


def test_bytecode_cache_does_not_invalidate_checked_installation(tmp_path):
    cfg = normalized_config(tmp_path, {"run": {"mode": "complex"}})
    site = Path(cfg["screen"]["boltz2_env"]) / "lib/python3.12/site-packages"
    package = site / "boltz"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("version = 'fixture'\n")
    before = environment_scope(tmp_path, cfg)
    for folder in (package / "__pycache__", site / "__pycache__"):
        folder.mkdir()
        (folder / "generated.cpython-312.pyc").write_bytes(b"synthetic import cache")
    assert environment_scope(tmp_path, cfg) == before


def test_unrelated_analysis_and_compilation_caches_do_not_expire_receipt(tmp_path):
    from nanocofold.platform.environment import normalized_installation_config
    preset = json.loads((ROOT / "nanocofold/_data/starter-presets.json").read_text())["modes"]["complex"]["native_override"]
    cfg = normalized_installation_config(tmp_path, preset)
    data = Path(cfg["screen"]["colabfold"]["data_dir"])
    data.mkdir(parents=True)
    (tmp_path / "models/boltz2/mols").mkdir(parents=True)
    before = environment_scope(tmp_path, cfg)
    for name in ("runs/example/worker.log", "models/colabfold/jax_cache/cache_entry",
                 "models/boltz2/mols/local_working_note.log", "models/colabfold/download.log"):
        output = tmp_path / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("unrelated cache/log fixture")
    assert environment_scope(tmp_path, cfg) == before


@pytest.mark.parametrize("mode", ["complex", "design", "screen", "mature"])
def test_actual_input_apis_reuse_the_starter_installation_receipt(tmp_path, mode):
    """Only doctor completion is a fixture; configuration/review/status are real."""
    from nanocofold.platform.tutorials import load_public_example
    preset = json.loads((ROOT / "nanocofold/_data/starter-presets.json").read_text())["modes"][mode]["native_override"]
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"x-platform-token": app.state.token}
        payload = {"mode": mode, "config": preset}
        status = client.post("/api/environment/status", json=payload, headers=headers)
        assert status.status_code == 200, status.text
        check = client.post("/api/environment/check", json=payload, headers=headers)
        assert check.status_code == 200, check.text
        app.state.jobs.store.update(check.json()["id"], state="complete",
            diagnostics={"ok": True, "environment_scope": status.json()["scope"]})
        entry = load_public_example(mode)["payload"]["entries"][0]
        if mode == "complex":
            settings = {"comparison_engines": preset["screen"]["comparison"]["engines"], "seeds": [42],
                        "msa": "precomputed", "msa_source": "public_api", "allow_public_msa_server": True,
                        "models_per_seed": 1, "recycles": 3, "diffusion_steps": 200}
            reviewed = client.post("/api/inputs/complex", headers=headers,
                json={"proteins": entry["proteins"], "settings": settings, "mock": False})
        else:
            preset = copy.deepcopy(preset)
            preset["screen"]["allow_public_msa_server"] = True
            reviewed = client.post("/api/inputs", headers=headers,
                json={**entry, "kind": "fasta", "mode": mode, "config": preset, "mock": False, "provider": "boltz2"})
        assert reviewed.status_code == 200, reviewed.text
        readiness = client.post("/api/environment/status", headers=headers,
            json={"input_id": reviewed.json()["id"], "mode": mode})
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["ready"] is True
        assert readiness.json()["scope"] == status.json()["scope"]
        assert len(app.state.jobs.store.list()) == 1  # Review/status submitted no work.
