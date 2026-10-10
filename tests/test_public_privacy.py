"""Release boundary and public fixture tests; no inference or network calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nanocofold import cascade, colabfold_msa_local, diagnostics
from nanocofold.platform.cloud_agent import Agent, public_diagnostics

ROOT = Path(__file__).resolve().parents[1]


def test_public_preset_matches_documented_tutorial_exactly():
    provenance = json.loads((ROOT / "examples/tutorial/sources.json").read_text())
    source = {row["id"]: row for row in provenance["sequence_records"]}
    catalog = json.loads((ROOT / "nanocofold/_data/public-msa-presets.json").read_text())
    assert set(catalog["public_queries"]) == {"human_il6", "anti_il6_nb"}
    approved = cascade.approved_public_sequences(catalog["preset_id"])
    for identifier, row in catalog["public_queries"].items():
        assert row["sequence"] == source[identifier]["input_sequence"]
        assert row["source_url"] == source[identifier]["url"]
        assert hashlib.sha256(row["sequence"].encode()).hexdigest() == row["sequence_sha256"]
        assert approved[row["sequence"]] == identifier


def test_unknown_public_preset_fails_closed():
    with pytest.raises(ValueError):
        cascade.approved_public_sequences("unregistered")


def test_operator_profile_contains_no_destination():
    profile = colabfold_msa_local.PROFILE
    assert profile["enabled"] is False
    assert all(profile[key] is None for key in ("host", "hostname", "db_root", "work_root"))
    assert profile["executable_roots"] == []


@pytest.mark.parametrize("transport", [colabfold_msa_local.SSHTransport, colabfold_msa_local.LocalTransport])
def test_unconfigured_local_search_never_starts_process(monkeypatch, transport):
    def unexpected(*args, **kwargs):
        pytest.fail("Unconfigured transport attempted to start a process")
    monkeypatch.setattr(colabfold_msa_local.subprocess, "run", unexpected)
    with pytest.raises(colabfold_msa_local.LocalMSAError, match="not configured"):
        transport().call("probe")


def test_unconfigured_local_search_cannot_claim_readiness():
    with pytest.raises(colabfold_msa_local.LocalMSAError, match="not configured"):
        colabfold_msa_local._ready_manifest()


@pytest.mark.parametrize("location", ["wheel", "checkout", "workspace"])
def test_doctor_finds_shipped_fixtures_in_each_distribution(monkeypatch, tmp_path, location):
    package = tmp_path / "site-packages" / "nanocofold"
    workspace = tmp_path / "workspace"
    paths = {"wheel": package / "_data/examples/tutorial",
             "checkout": package.parent / "examples/tutorial",
             "workspace": workspace / "examples/tutorial"}
    fixtures = paths[location]
    fixtures.mkdir(parents=True)
    (fixtures / "target.fasta").write_text(">target\nACDE\n")
    (fixtures / "vhh_library.fasta").write_text(">candidate\nFGHI\n")
    monkeypatch.setattr(diagnostics, "__file__", str(package / "diagnostics.py"))
    monkeypatch.setattr(diagnostics, "PROJECT_ROOT", workspace)
    assert diagnostics._public_smoke_fixtures() == (fixtures / "target.fasta", fixtures / "vhh_library.fasta")


def test_missing_fixture_is_not_synthesized_or_downloaded(monkeypatch, tmp_path):
    package = tmp_path / "nanocofold"
    monkeypatch.setattr(diagnostics, "__file__", str(package / "diagnostics.py"))
    monkeypatch.setattr(diagnostics, "PROJECT_ROOT", tmp_path)
    assert all(not path.exists() for path in diagnostics._public_smoke_fixtures())


def test_dependency_metadata_has_no_local_install_origin():
    for path in (ROOT / "nanocofold/_data/locks").glob("*-source.json"):
        document = json.loads(path.read_text())
        for package in document.get("packages", []):
            direct = package.get("direct_url")
            assert not direct or not direct.get("url", "").startswith("file:"), path.name


@pytest.mark.parametrize("workflow", ["standard", "colabfold_boltz2"])
def test_generic_cloud_settings_need_no_fixed_remote_host(workflow):
    agent = Agent.__new__(Agent)
    params = {} if workflow == "standard" else {"workflow": workflow, "msa_source": "cache"}
    config = agent.settings("screen", params)
    assert config["run"]["mode"] == "screen"
    assert config["screen"]["restraint"]["enabled"] is False


def test_generic_cloud_public_preset_uses_current_catalog():
    agent = Agent.__new__(Agent)
    config = agent.settings("screen", {"workflow": "colabfold_boltz2",
        "msa_source": "public_mmseqs2", "cf_public_preset": "public_tutorial_v1"})
    assert config["screen"]["colabfold"]["cf_public_preset"] == "public_tutorial_v1"
    with pytest.raises(ValueError):
        agent.settings("screen", {"workflow": "colabfold_boltz2",
            "msa_source": "public_mmseqs2", "cf_public_preset": "unregistered"})


def test_ui_demo_uses_only_current_documented_public_vhh():
    document = json.loads((ROOT / "examples/tutorial/sources.json").read_text())
    sequence = next(row["input_sequence"] for row in document["sequence_records"] if row["id"] == "anti_il6_nb")
    for name in ("platform.js", "platform_offline.html"):
        assert sequence in (ROOT / "nanocofold/templates" / name).read_text()


def test_upstream_viewer_notices_match_content_manifest():
    manifest = json.loads((ROOT / "assets/vendor_manifest.json").read_text())
    for name in ("MOLSTAR_LICENSE.txt", "PLOTLY_LICENSE.txt", "3DMOL_LICENSE.txt"):
        content = (ROOT / "assets" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == manifest[name]["sha256"]


def test_diagnostic_export_hides_local_paths_and_credentials():
    value = public_diagnostics({"file": "/opt/nanocofold/local/run", "token": "fixture",
                                "checks": [{"ok": True, "stderr": "private output"}]})
    assert value == {"file": "[local path]", "checks": [{"ok": True}]}


def test_synthetic_design_fallback_is_a_documented_public_sequence():
    from nanocofold.design_rfantibody import framework_sequence
    sources = json.loads((ROOT / "examples/tutorial/sources.json").read_text())
    expected = next(row["input_sequence"] for row in sources["sequence_records"] if row["id"] == "anti_il6_nb")
    assert framework_sequence(None) == expected


def test_public_tutorial_input_can_be_reviewed_without_inference(tmp_path):
    from nanocofold.install import init_workspace
    from nanocofold.platform.inputs import InputManager
    init_workspace(tmp_path)
    manager = InputManager(tmp_path)
    try:
        response = manager.fasta({
            "content": (ROOT / "examples/tutorial/target.fasta").read_text(),
            "vhh_fasta": (ROOT / "examples/tutorial/vhh_library.fasta").read_text(),
            "mode": "screen", "name": "public-input-review", "mock": False,
            "hotspots": [], "provider": "boltz2",
        })
        assert response["config"]["run"]["mode"] == "screen"
        assert response["review"]["predictions_executed"] is False
        assert len(response["config"]["target"]["sequence"]) == 183
        assert list((tmp_path / "runs").iterdir()) == []
    finally:
        manager.close()
