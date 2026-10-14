"""A drift guard requiring only this checkout, also in an isolated public ZIP."""
import hashlib
import json
from pathlib import Path


def test_shared_core_matches_the_jointly_sealed_manifest_without_a_sibling_repo():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "tests/core_sync_manifest.json").read_text())
    assert manifest["schema_version"] == 1
    actual = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (root / "nanocofold").rglob("*")
              if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
              and p.suffix in {".py", ".js", ".j2", ".html", ".css"}}
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
