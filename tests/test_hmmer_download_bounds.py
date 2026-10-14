"""Exercise real shell failure/retry paths without an external connection."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def shell_workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "envs").mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / "envs/setup_antibody.sh", root / "envs/setup_antibody.sh")
    (root / "envs/install_locked.sh").write_text("install_locked() { mkdir -p envs/antibody/bin; }\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("uv", "gcc", "make"):
        p = binaries / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    curl = binaries / "curl"
    curl.write_text(f"#!{sys.executable}\n" + """import json,os,sys
from pathlib import Path
a=sys.argv[1:];Path(os.environ['CURL_ARGS']).write_text(json.dumps(a))
Path(a[a.index('-o')+1]).write_bytes(b'preserved-download-prefix')
raise SystemExit(int(os.environ['CURL_EXIT']))
""")
    curl.chmod(0o755)
    sha = binaries / "sha256sum"
    sha.write_text(f"#!{sys.executable}\n" + """import hashlib,sys
from pathlib import Path
expected,name=sys.stdin.read().strip().split(maxsplit=1)
raise SystemExit(0 if hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected else 1)
""")
    sha.chmod(0o755)
    tar = binaries / "tar"
    tar.write_text("#!/bin/sh\ntouch \"$TAR_CALLED\"\nexit 9\n")
    tar.chmod(0o755)
    env = dict(os.environ, PATH=str(binaries)+":/usr/bin:/bin", CURL_ARGS=str(tmp_path / "curl-args.json"),
               CURL_EXIT="28", TAR_CALLED=str(tmp_path / "tar-called"))
    return root, env


def invoke(root, env):
    return subprocess.run(["bash", "envs/setup_antibody.sh"], cwd=root, env=env,
                          capture_output=True, text=True, timeout=10)


def attempts(root):
    return sorted((root / ".platform/hmmer-source-attempts").glob("attempt.*"))


def test_failed_download_is_bounded_and_partial_preserved(shell_workspace):
    root, env = shell_workspace
    result = invoke(root, env)
    assert result.returncode == 28
    assert "같은 설치 명령을 다시 실행" in result.stderr
    args = json.loads(Path(env["CURL_ARGS"]).read_text())
    for flag, value in (("--connect-timeout", "20"), ("--max-time", "120"),
                        ("--retry", "2"), ("--retry-max-time", "240")):
        assert args[args.index(flag)+1] == value
    assert "https://eddylab.org/software/hmmer/hmmer-3.4.tar.gz" in args
    attempt, = attempts(root)
    assert (attempt / "hmmer.tar.gz.part").read_bytes() == b"preserved-download-prefix"
    assert "status=download_failed" in (attempt / "status.txt").read_text()
    assert not (attempt / "hmmer.tar.gz").exists()
    assert not Path(env["TAR_CALLED"]).exists()


def test_retry_creates_new_attempt_without_overwriting_failure(shell_workspace):
    root, env = shell_workspace
    assert invoke(root, env).returncode == 28
    first, = attempts(root)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir()}
    assert invoke(root, env).returncode == 28
    assert len(attempts(root)) == 2
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir()} == hashes
    assert not Path(env["TAR_CALLED"]).exists()


def test_downloaded_unverified_archive_is_not_extracted(shell_workspace):
    root, env = shell_workspace
    env["CURL_EXIT"] = "0"
    result = invoke(root, env)
    assert result.returncode == 1
    assert "SHA-256이 일치하지 않습니다" in result.stderr
    attempt, = attempts(root)
    assert "status=checksum_failed" in (attempt / "status.txt").read_text()
    assert (attempt / "hmmer.tar.gz.part").read_bytes() == b"preserved-download-prefix"
    assert not (attempt / "hmmer.tar.gz").exists()
    assert not Path(env["TAR_CALLED"]).exists()
