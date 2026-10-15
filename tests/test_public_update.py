import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from update_runtime import update


def test_update_preserves_gpu_location_and_results_and_invokes_no_inference(tmp_path):
    repo=tmp_path/"repo"; workspace=tmp_path/"workspace"
    python=workspace/".venv-dev/bin/python";python.parent.mkdir(parents=True);python.touch()
    uv=tmp_path/"uv";uv.touch()
    location=repo/".nanocofold/install-location.json";location.parent.mkdir(parents=True)
    location.write_text(json.dumps({"workspace":str(workspace),"uv_path":str(uv),"gpu":"GPU-existing","profile":"complex"}))
    marker=workspace/".platform/public-core-install.json";marker.parent.mkdir()
    marker.write_text(json.dumps({"workspace":str(workspace),"python":str(python),"status":"complete"}))
    result=workspace/"runs/original/report.html";result.parent.mkdir(parents=True);result.write_text("preserve me")
    before=(location.read_bytes(),marker.read_bytes(),result.read_bytes());calls=[]
    update(repo,run=lambda args,**kwargs:calls.append((args,kwargs)))
    assert (location.read_bytes(),marker.read_bytes(),result.read_bytes())==before
    assert len(calls)==4
    for args,kwargs in calls:
        assert "setup" not in args and "doctor" not in args and "run" not in args
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"]==""
    assert list((workspace/".platform/core-updates").glob("*.json"))
    import sqlite3
    db=workspace/".nanocofold/jobs.sqlite3";db.parent.mkdir()
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT)")
        connection.execute("INSERT INTO jobs VALUES ('running')")
    calls.clear()
    with pytest.raises(ValueError,match="작업"):
        update(repo,run=lambda args,**kwargs:calls.append((args,kwargs)))
    assert not calls and (location.read_bytes(),marker.read_bytes(),result.read_bytes())==before


def test_update_refuses_unmanaged_environment_without_installing(tmp_path):
    location=tmp_path/".nanocofold/install-location.json";location.parent.mkdir()
    location.write_text(json.dumps({"workspace":str(tmp_path),"uv_path":"/missing"}))
    with pytest.raises(OSError):
        update(tmp_path,run=lambda *a,**k:pytest.fail("unmanaged install changed"))
