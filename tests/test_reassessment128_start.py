"""Fresh-download launch errors, without installation, networking or GPUs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "fresh source 한글"
    (source / "scripts").mkdir(parents=True)
    for name in ("start.sh", "scripts/start_server.py", "scripts/install_runtime.py"):
        shutil.copy2(ROOT / name, source / name)
    (source / "existing-results.txt").write_text("preserve existing work\n")
    return source


def snapshot(source):
    return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*") if p.is_file()}


def launch(source, *arguments, env=None):
    return subprocess.run(["/bin/bash", str(source / "start.sh"), *arguments],
                          cwd=source.parent, capture_output=True, text=True, timeout=10,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})


@pytest.mark.parametrize("option", ["-h", "--help"])
def test_start_help_needs_no_python_installation_or_gpu(source, option):
    before = snapshot(source)
    result = launch(source, option, env={"PATH": ""})
    assert result.returncode == 0 and not result.stderr
    assert "사용법" in result.stdout and "Usage" in result.stdout
    assert "bash install.sh" in result.stdout and "--port 18765" in result.stdout
    assert snapshot(source) == before


def test_start_before_install_explains_next_step_without_internal_error(source):
    before = snapshot(source)
    result = launch(source, "--port", "18765")
    assert result.returncode == 1 and not result.stdout
    assert "먼저 bash install.sh" in result.stderr
    assert "First run bash install.sh" in result.stderr
    for internal in (str(source), "install-location.json", "Errno", "Traceback"):
        assert internal not in result.stderr
    assert snapshot(source) == before


@pytest.mark.parametrize("receipt", ["{", "[]", "null", "{}", '{"workspace": ""}',
    '{"workspace": "relative"}', '{"workspace": []}',
    '{"workspace": "/missing", "uv_path": 123}', '{"workspace": "/missing", "gpu": 123}'])
def test_broken_installation_record_has_actionable_bilingual_error(source, receipt):
    location = source / ".nanocofold/install-location.json"
    location.parent.mkdir()
    location.write_text(receipt)
    before = snapshot(source)
    result = launch(source, "--port", "18765")
    assert result.returncode == 1
    assert "bash install.sh" in result.stderr and "Rerun" in result.stderr
    assert "설치" in result.stderr
    assert str(source) not in result.stderr and "Traceback" not in result.stderr
    assert snapshot(source) == before


def test_removed_installation_is_not_recreated_by_start(source):
    location = source / ".nanocofold/install-location.json"
    location.parent.mkdir()
    missing = source / "removed workspace"
    location.write_text(json.dumps({"workspace": str(missing)}))
    before = snapshot(source)
    result = launch(source)
    assert result.returncode == 1 and "Cannot find the installed program" in result.stderr
    assert "bash install.sh" in result.stderr
    assert not missing.exists() and snapshot(source) == before
