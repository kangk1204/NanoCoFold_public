"""Exercise the shell boundary without apt, network, or changes to the host OS."""
import os
from pathlib import Path
import subprocess

import pytest

from test_public_install import bootstrap


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell_host(tmp_path):
    release = tmp_path / "os-release"
    shell = tmp_path / "install.sh"
    # Substitute only the trusted OS file in a disposable copy. Production has
    # no environment override that could bypass its distribution check.
    shell.write_text((ROOT / "install.sh").read_text().replace("/etc/os-release", str(release)))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "runtime-calls"
    for name, body in {
        "uname": 'case "$1" in -s) echo "${TEST_KERNEL}";; -m) echo "${TEST_ARCH}";; esac',
        "nvidia-smi": 'exit "${TEST_NVIDIA_EXIT}"',
        "python3": 'printf "%s\\n" "$@" > "$HOST_CALLS"',
        "sudo": 'echo "Unexpected system mutation" >&2; exit 99',
    }.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def check(distribution="ubuntu", version="25.10", kernel="Linux", architecture="x86_64", nvidia_exit=0):
        release.write_text(f'ID="{distribution}"\nVERSION_ID="{version}"\n')
        environment = {
            **os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "TEST_KERNEL": kernel, "TEST_ARCH": architecture,
            "TEST_NVIDIA_EXIT": str(nvidia_exit), "HOST_CALLS": str(calls),
        }
        result = subprocess.run(["bash", str(shell), "--check-host"],
                                capture_output=True, text=True, env=environment, timeout=10)
        return result, calls

    return check


@pytest.mark.parametrize("version", ["22.04", "24.04", "25.04", "25.10", "26.04", "27.10", ""])
def test_ubuntu_version_does_not_prevent_runtime_validation(shell_host, version):
    result, calls = shell_host(version=version)
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines()[-1] == "--check-host"


@pytest.mark.parametrize("distribution", ["debian", "linuxmint", "", "fedora"])
def test_ubuntu_distribution_boundary_is_preserved(shell_host, distribution):
    result, calls = shell_host(distribution=distribution)
    assert result.returncode == 1 and "Ubuntu" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize("kernel,architecture", [("Darwin", "x86_64"), ("Linux", "aarch64")])
def test_platform_boundary_is_preserved(shell_host, kernel, architecture):
    result, calls = shell_host(kernel=kernel, architecture=architecture)
    assert result.returncode == 1 and "x86_64" in result.stderr
    assert not calls.exists()


def test_nvidia_failure_still_stops_the_shell(shell_host):
    result, calls = shell_host(version="24.04", nvidia_exit=255)
    assert result.returncode == 255
    assert not calls.exists()


@pytest.mark.parametrize("libc,version", [("glibc", "2.33"), ("glibc", "N/A"), ("musl", "1.2")])
def test_actual_libc_requirement_is_preserved(monkeypatch, libc, version):
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.platform, "libc_ver", lambda: (libc, version))
    monkeypatch.setattr(bootstrap, "select_gpu", lambda *_: pytest.fail("GPU must not be selected"))
    with pytest.raises(ValueError, match="glibc 2.34"):
        bootstrap.check_host("all", None)


def test_actual_missing_tool_still_blocks_installation(monkeypatch):
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.platform, "libc_ver", lambda: ("glibc", "2.42"))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None if name == "gcc" else "/bin/" + name)
    monkeypatch.setattr(bootstrap, "select_gpu", lambda *_: pytest.fail("GPU must not be selected"))
    with pytest.raises(ValueError, match="gcc"):
        bootstrap.check_host("all", None)
