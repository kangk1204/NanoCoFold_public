"""Effective UI settings use the reviewed workspace defaults without GPU work."""
import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
from nanocofold.install import init_workspace
from nanocofold.platform.app import create_app, planning_defaults

ROOT = Path(__file__).resolve().parents[1]


def test_browser_count_controls_follow_presets_and_preserve_inputs():
    subprocess.run(['node', '--test', str(ROOT/'tests/analysis_scale_contract.mjs')],check=True,capture_output=True,text=True)


def test_session_exposes_only_planning_values_and_version(tmp_path):
    init_workspace(tmp_path)
    (tmp_path/'config/environment.yaml').write_text('design:\n  num_designs: 7\n  env_vars:\n    PRIVATE_TOKEN_SENTINEL: secret-value\nrank:\n  final_n: 4\n')
    values=planning_defaults(tmp_path)
    assert values['design']['num_designs']==7 and values['rank']['final_n']==4
    rendered=json.dumps(values)
    assert 'PRIVATE_TOKEN_SENTINEL' not in rendered and 'secret-value' not in rendered and str(tmp_path) not in rendered
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url="http://127.0.0.1",headers={"x-platform-token":app.state.token}) as client:
        data=client.get('/api/session').json()
        assert data['planning_defaults']==values
        from nanocofold import __version__
        assert data['version']==__version__


def test_invalid_workspace_config_keeps_environment_screen_accessible(tmp_path):
    init_workspace(tmp_path)
    (tmp_path/'config/environment.yaml').write_text('design: invalid\n')
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url="http://127.0.0.1",headers={"x-platform-token":app.state.token}) as client:
        response=client.get('/api/session')
        assert response.status_code==200 and response.json()['planning_defaults']=={}


def test_missing_installation_update_gives_next_action_without_internal_path(tmp_path):
    import runpy
    update=runpy.run_path(str(ROOT/'scripts/update_runtime.py'))['update']
    import pytest
    with pytest.raises(ValueError) as caught:
        update(tmp_path,run=lambda *args,**kwargs:pytest.fail('Started installation for an unknown workspace'))
    assert 'bash install.sh' in str(caught.value) and 'original installation folder' in str(caught.value)
    assert str(tmp_path) not in str(caught.value) and not list(tmp_path.iterdir())


def test_cli_update_uses_verified_installed_python_before_opening_job_database(tmp_path):
    """A host Python/SQLite mismatch must not prevent a managed update."""
    import shutil
    import sys
    repo = tmp_path/'source'
    scripts = repo/'scripts'
    scripts.mkdir(parents=True)
    for name in ('update_runtime.py', 'install_runtime.py'):
        shutil.copy2(ROOT/'scripts'/name, scripts/name)
    workspace = tmp_path/'workspace'
    python = workspace/'.venv-dev/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "managed interpreter selected\\n"\n')
    python.chmod(0o700)
    uv = tmp_path/'uv'
    uv.touch()
    location = repo/'.nanocofold/install-location.json'
    location.parent.mkdir()
    location.write_text(json.dumps({'workspace':str(workspace),'uv_path':str(uv)}))
    marker = workspace/'.platform/public-core-install.json'
    marker.parent.mkdir()
    valid = {'workspace':str(workspace),'python':str(python),'status':'complete'}
    marker.write_text(json.dumps(valid))
    # The stub deliberately never opens this DB. The recorded interpreter owns
    # the next validation pass; the host interpreter must not open it first.
    database = workspace/'.nanocofold/jobs.sqlite3'
    database.parent.mkdir()
    database.write_bytes(b'not a database')
    result = subprocess.run([sys.executable,str(scripts/'update_runtime.py')],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0 and 'managed interpreter selected' in result.stdout
    assert database.read_bytes() == b'not a database'
    marker.write_text(json.dumps({**valid,'status':'installing'}))
    result = subprocess.run([sys.executable,str(scripts/'update_runtime.py')],capture_output=True,text=True,timeout=10)
    assert result.returncode == 1 and 'managed interpreter selected' not in result.stdout
    assert database.read_bytes() == b'not a database'
