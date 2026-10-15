"""A drift guard requiring only this checkout, also in an isolated public ZIP."""
import runpy
import json
from pathlib import Path


def test_shared_core_matches_the_jointly_sealed_manifest_without_a_sibling_repo():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "tests/core_sync_manifest.json").read_text())
    assert manifest["schema_version"] == 2
    collect = runpy.run_path(str(root / "scripts/seal_core_contract.py"))["core_files"]
    actual = collect(root)
    assert actual == manifest["files"], "Shared core changed; compare both repos before resealing"


def test_operator_policy_is_data_and_public_distribution_has_no_destination():
    from nanocofold.runtime_policy import runtime_policy
    policy = runtime_policy()
    if policy["smoke_fixture_directory"] == "examples/tutorial":
        profile = policy["local_msa_profile"]
        assert profile["enabled"] is False
        assert all(profile[key] is None for key in ("host", "hostname", "db_root", "work_root"))
    else:
        assert policy["smoke_fixture_directory"] == "data/benchmark"
        assert policy["local_msa_profile"]["enabled"] is True


def test_scientific_settings_assets_and_dependencies_cannot_be_resealed_with_peer_drift(tmp_path):
    import shutil
    import subprocess
    import sys
    import yaml
    root = Path(__file__).resolve().parents[1]
    # A real second source tree, with no dependency on a sibling checkout.
    for name in ("nanocofold", "config", "scripts", "tests"):
        shutil.copytree(root / name, tmp_path / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root / "pyproject.toml", tmp_path)
    script = tmp_path / "scripts/seal_core_contract.py"
    manifest = tmp_path / "tests/core_sync_manifest.json"
    before = manifest.read_bytes()
    for relative in ("config/default.yaml", "nanocofold/_data/starter-presets.json",
                     "nanocofold/_data/model-assets.json", "nanocofold/_data/locks/boltz2.txt"):
        path = tmp_path / relative
        original = path.read_bytes()
        if relative.endswith("default.yaml"):
            value = yaml.safe_load(original)
            value["score"]["contract_drift"] = 1
            path.write_text(yaml.safe_dump(value))
        elif relative.endswith(".json"):
            value = json.loads(original)
            value["contract_drift"] = 1
            path.write_text(json.dumps(value))
        else:
            path.write_text(original.decode() + "\nnew_dependency==1.0\n")
        completed = subprocess.run([sys.executable, str(script), "--peer", str(root)],
                                   capture_output=True, text=True)
        assert completed.returncode != 0 and relative in completed.stderr
        assert manifest.read_bytes() == before
        path.write_bytes(original)
