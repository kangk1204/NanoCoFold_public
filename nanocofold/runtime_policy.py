"""Distribution policy data, separated from the shared execution modules."""
from pathlib import Path
import json


def runtime_policy():
    path = Path(__file__).parent / "_data/runtime-policy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported runtime policy schema")
    if data.get("smoke_fixture_directory") not in ("examples/tutorial", "data/benchmark"):
        raise ValueError("Unregistered diagnostic fixture source")
    sequence = data.get("mock_framework_sequence")
    if not isinstance(sequence, str) or not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
        raise ValueError("Invalid synthetic framework policy")
    profile = data.get("local_msa_profile")
    if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
        raise ValueError("Invalid local MSA policy")
    if profile["enabled"] and any(not isinstance(profile.get(key), str) or not profile[key]
                                  for key in ("name", "host", "hostname", "db_root", "work_root")):
        raise ValueError("Enabled local MSA requires a registered destination")
    return data
