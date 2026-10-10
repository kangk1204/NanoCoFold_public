"""Presentation paths must never consume scientific payloads or human labels."""
import base64
import copy
import gzip
import hashlib
import json
import random
import struct

import pytest

from nanocofold.utils.presentation import portable_metadata


@pytest.mark.parametrize("text", [
    "임계값/예측 품질을 점검하세요.", "서열/폴딩 적합도 prior입니다.",
    "구조/체인 매핑", "결합/KD/친화도", "πρωτεΐνη/δομή", "résidu/chaîne",
    "ipSAE/ipTM 기준", "A / B", "N/A", "3/4 hotspots", "models/s",
    "F/C=C/F", "N[C@@H](C)/C=C/C(=O)O", "[NH+]/C=C\\C",
    "sha512-0cUUwMj7FiH+/1Hp7F2Px6ywQV+/4A==", "</script><p>값/지표</p>",
])
def test_scientific_and_human_slash_text_is_unchanged(text):
    assert portable_metadata(text) == text


def encoded_pae():
    generator = random.Random(7)
    raw = struct.pack("<14400d", *[round(generator.uniform(.2, 30), 3) for _ in range(14400)])
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    assert "+/" in encoded
    return raw, {"encoding": "gzip-base64-float64le", "dimension": 120,
                 "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "data": encoded}


def test_encoded_pae_survives_exact_roundtrip_and_serialized_json():
    raw, entry = encoded_pae()
    original = copy.deepcopy(entry)
    for payload in (entry, {"outer": entry}, json.dumps({"outer": entry})):
        result = portable_metadata(payload)
        if isinstance(result, str):
            result = json.loads(result)
        actual = result.get("outer", result)
        restored = gzip.decompress(base64.b64decode(actual["data"], validate=True))
        assert actual == original
        assert restored == raw
        assert len(restored) == actual["bytes"]
        assert hashlib.sha256(restored).hexdigest() == actual["sha256"]
        assert len(struct.unpack("<14400d", restored)) == actual["dimension"] ** 2
    assert entry == original


@pytest.mark.parametrize("value", [
    "/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+/wD+",
    base64.b64encode(bytes(range(256)) * 4).decode("ascii"),
])
def test_untyped_base64_including_leading_slash_is_unchanged(value):
    assert portable_metadata(value) == value
    assert portable_metadata({"data": value})["data"] == value


@pytest.mark.parametrize("key,value", [
    ("content", "REMARK /home/user/source.pdb\nATOM unchanged\n"),
    ("smiles", "/C=C/C"), ("canonical_smiles", "/C=C/C"),
    ("isomeric_smiles", "C:/C=C"), ("integrity", "sha512-/home/data+/value=="),
    ("sha256", "f" * 64), ("sha512", "b" * 128),
])
def test_typed_native_chemical_and_integrity_fields_are_opaque(key, value):
    assert portable_metadata({key: value}) == {key: value}


@pytest.mark.parametrize("path", [
    "/home/user/project/model.pdb", "/Users/user/folder/model.pdb",
    "/tmp/run/model.pdb", "/var/tmp/run/model.pdb", "/private/tmp/a",
    "/mnt/disk/data/file.cif", "/srv/workspace/input.fasta",
    "C:\\Users\\user\\file.pdb", "C:/Users/user/file.pdb",
    "\\\\server\\share\\file.pdb", "\\\\?\\C:\\Users\\user\\file.pdb",
])
def test_real_host_paths_still_redacted(path):
    assert portable_metadata(path) == "[local path]"
    assert portable_metadata({"path": path}) == {"path": "[local path]"}
    assert portable_metadata("missing " + path) == "missing [local path]"


def test_explicit_path_fields_accept_custom_roots_and_in_run_paths_stay_relative(tmp_path):
    root = tmp_path / "run with spaces"
    value = {"model_path": str(root / "models/example.cif"),
             "source_root": "/unusual_mount/experiment", "env": "/custom-prefix/tool/python",
             "outside": str(root / "../outside.pdb"),
             "json": json.dumps({"input_file": "/custom-prefix/input.fasta"})}
    original = copy.deepcopy(value)
    result = portable_metadata(value, root=root)
    assert result["model_path"] == "models/example.cif"
    assert result["source_root"] == result["env"] == result["outside"] == "[local path]"
    assert json.loads(result["json"])["input_file"] == "[local path]"
    assert value == original


@pytest.mark.parametrize("url", ["https://example.org/-/data", "s3://bucket/path/file", "gs://bucket/path", "ftp://example.org/path"])
def test_urls_are_preserved_beside_real_host_paths(url):
    value = f'see {url} then "/home/user/with spaces/model.pdb"; 서열/폴딩'
    assert portable_metadata(value) == f'see {url} then "[local path]"; 서열/폴딩'


def test_cloud_diagnostics_keeps_labels_and_removes_secrets():
    from nanocofold.platform.cloud_agent import public_diagnostics
    value = {"label": "임계값/예측 품질", "path": "/home/user/file.pdb",
             "nested": {"password": "do not publish", "stdout": "private stdout", "label": "서열/폴딩"}}
    assert public_diagnostics(value) == {"label": "임계값/예측 품질", "path": "[local path]",
                                         "nested": {"label": "서열/폴딩"}}


def test_cloud_diagnostics_keeps_typed_array_and_custom_path_context():
    from nanocofold.platform.cloud_agent import public_diagnostics
    _, entry = encoded_pae()
    # Even a valid encoding whose bytes happen to resemble a pathname is opaque.
    entry["data"] = "/home/" + entry["data"]
    value = {"pae": entry, "path": "/custom-mount/private/probe.json", "token": "secret"}
    original = copy.deepcopy(value)
    result = public_diagnostics(value)
    assert result == {"pae": entry, "path": "[local path]"}
    assert value == original
