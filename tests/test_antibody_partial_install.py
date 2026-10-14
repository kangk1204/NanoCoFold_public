"""Real canonical binary probes and official-shell recovery without networking."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
import pytest
import yaml
from nanocofold import diagnostics,install
from nanocofold.utils import shell


def executable(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text("#!/bin/sh\n"+text);path.chmod(0o755)


@pytest.fixture
def managed_workspace(tmp_path,monkeypatch):
    ws=tmp_path/"workspace";env=ws/"envs/antibody";(env/"bin").mkdir(parents=True);(ws/"config").mkdir()
    executable(env/"bin/python",'printf \'{"ok": true, "torch": "fixture"}\\n\'\n')
    (ws/"config/default.yaml").write_text(yaml.safe_dump({"mature":{"anarci":{"env":str(env)}}}))
    (ws/"config/environment.yaml").write_text("{}\n")
    (ws/".platform").mkdir()
    marker=ws/".platform/managed-install-antibody.json"
    marker.write_text(json.dumps({"workspace":str(ws),"engine":"antibody","status":"complete"}))
    cache=tmp_path/"verified-cache/bin"
    for name in ["hmmscan","hmmsearch","hmmpress","hmmbuild"]:
        executable(cache/name,"printf '# "+name+" help\\n# HMMER 3.4\\n# fixture\\n'\n")
    executable(cache/"uv","exit 0\n")
    shutil.copyfile(Path(__file__).parents[1]/"envs/setup_antibody.sh",ws/"envs/setup_antibody.sh")
    (ws/"envs/install_locked.sh").write_text("install_locked() { :; }\n")
    monkeypatch.setenv("PATH",str(cache)+os.pathsep+os.environ["PATH"])
    monkeypatch.setitem(install.PROFILES,"screen",("antibody",))
    monkeypatch.setattr(install.platform,"system",lambda:"Linux")
    monkeypatch.setattr(install.platform,"machine",lambda:"x86_64")
    monkeypatch.setattr(install,"init_workspace",lambda *a,**k:None)
    monkeypatch.setattr(install,"_validate_environment",lambda *a,**k:{"packages":"verified"})
    monkeypatch.setattr(diagnostics,"_engine_weights",lambda *a,**k:{"ok":True,"stage":"fixture-model-data"})
    return ws,env,marker,cache


def test_transient_path_cannot_make_missing_registered_binary_ready(managed_workspace):
    ws,env,marker,cache=managed_workspace
    assert shutil.which("hmmscan")==str(cache/"hmmscan")
    assert install._antibody_binary_probe(env)["ok"] is False
    with pytest.raises(ValueError,match="offline"):
        install._offline_readiness(ws,("antibody",))
    report=json.loads((ws/".platform/offline-readiness.json").read_text())["anarci"]
    assert report["ok"] is False and report["binaries"][0]["stage"]=="binary_lookup"
    assert json.loads(marker.read_text())["status"]=="complete"


@pytest.mark.parametrize("previous_status",["installing","complete"])
def test_owned_python_only_install_runs_official_shell_and_recovers(managed_workspace,monkeypatch,previous_status):
    ws,env,marker,cache=managed_workspace;calls=[]
    marker.write_text(json.dumps({"workspace":str(ws),"engine":"antibody","status":previous_status}))
    python_before=(env/"bin/python").read_bytes();cache_before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.iterdir()}
    def real_shell(argv,**kwargs):
        calls.append(argv);result=subprocess.run(argv,cwd=kwargs["cwd"],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
    monkeypatch.setattr(shell,"run",real_shell)
    result=install.setup_profile(ws,"screen")
    assert len(calls)==1 and Path(calls[0][1]).name=="setup_antibody.sh"
    assert result["status"]=="complete" and result["offline_ready"] is True
    assert (env/"bin/hmmscan").is_symlink() and install._antibody_binary_probe(env)["ok"] is True
    assert (env/"bin/python").read_bytes()==python_before
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.iterdir()}==cache_before
    assert json.loads(marker.read_text())["status"]=="complete"


def test_broken_executable_is_not_reported_ready_or_marked_complete(managed_workspace,monkeypatch):
    ws,env,marker,cache=managed_workspace
    executable(env/"bin/hmmscan","exit 7\n")
    assert install._antibody_binary_probe(env)["ok"] is False
    calls=[]
    # A shell process returning successfully is insufficient without working HMMER.
    monkeypatch.setattr(shell,"run",lambda argv,**k:calls.append(argv))
    with pytest.raises(ValueError,match="offline"):
        install.setup_profile(ws,"screen")
    assert len(calls)==1 and json.loads(marker.read_text())["status"]=="installing"
    assert json.loads((ws/".platform/offline-readiness.json").read_text())["anarci"]["ok"] is False


def test_valid_registered_help_skips_reinstallation(managed_workspace,monkeypatch):
    ws,env,marker,cache=managed_workspace
    executable(env/"bin/hmmscan","printf '# HMMER 3.4\\n'\n")
    calls=[];monkeypatch.setattr(shell,"run",lambda argv,**k:calls.append(argv))
    assert install.setup_profile(ws,"screen")["offline_ready"] is True
    assert calls==[] and install._antibody_binary_probe(env)["ok"] is True


def test_unmanaged_environment_with_missing_binary_is_preserved(managed_workspace,monkeypatch):
    ws,env,marker,cache=managed_workspace;marker.unlink();before=(env/"bin/python").read_bytes()
    calls=[];monkeypatch.setattr(shell,"run",lambda argv,**k:calls.append(argv))
    with pytest.raises(ValueError,match="보존"):
        install.setup_profile(ws,"screen")
    assert calls==[] and (env/"bin/python").read_bytes()==before and not marker.exists()
