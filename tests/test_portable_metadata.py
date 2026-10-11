"""Presentation paths must never consume scientific payloads or human labels."""
import base64
import copy
import gzip
import hashlib
import json
import random
import struct
from pathlib import Path

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


@pytest.mark.parametrize("text,expected", [
    ("id1,/home/user/run/model.cif,0.5", "id1,[local path],0.5"),
    ("<td>/home/user/run/model.cif</td>", "<td>[local path]</td>"),
    ("path `/home/user/run/model.cif`", "path `[local path]`"),
    ("x;/home/user/run", "x;[local path]"),
    ("x|/home/user/run", "x|[local path]"),
    ("경로는/home/user/run 입니다", "경로는[local path] 입니다"),
    ("/nfs/lab/user/run/model.cif", "[local path]"),
    ("error at /gpfs/proj/user/x.py", "error at [local path]"),
    ("file:///home/user/run/report.html", "[local path]"),
    ("open file://localhost/home/user/run/report.html failed", "open [local path] failed"),
    ("x,file:///custom-mount/run/report.html,0.5", "x,[local path],0.5"),
    ("/home/user/run/model.cif: 파일을 찾을 수 없습니다", "[local path]: 파일을 찾을 수 없습니다"),
    ("/home/user/run/model.cif failed: missing input", "[local path] failed: missing input"),
])
def test_audit103_path_contexts_and_error_prose(text, expected):
    assert portable_metadata(text) == expected
    from nanocofold.platform.cloud_agent import public_diagnostics
    assert public_diagnostics({"message": text}) == {"message": expected}


def test_audit103_supplied_root_is_recognized_inside_free_text():
    # A supplied root is evidence even when its mount is outside the root list.
    root = Path("/custom-mount/run with spaces")
    value = f'row,{root}/model.cif,0.5; open "{root}/sub folder/model.cif" failed'
    assert portable_metadata(value, root=root) == 'row,model.cif,0.5; open "sub folder/model.cif" failed'
    assert portable_metadata(f"{root}/model.cif: 원인 설명", root=root) == "model.cif: 원인 설명"
    assert portable_metadata(f"row,{root}/../outside.cif,0.5", root=root) == "row,[local path],0.5"
    assert portable_metadata("row,/custom-mount/other/file.cif,0.5", root=root) == "row,/custom-mount/other/file.cif,0.5"


def test_audit103_explicit_path_fields_preserve_error_prose_and_identify_short_drive_paths():
    assert portable_metadata({"path": "/custom-mount/input.cif: 파일 없음"}) == {"path": "[local path]: 파일 없음"}
    assert portable_metadata({"path": "C:/run"}) == {"path": "[local path]"}


@pytest.mark.parametrize("text", [
    '{"a":1.50,"b":[1,2]}',
    ' { "metric": 1.5000e-1, "negative_zero": -0.0, "label": "구조/체인" }\n',
    '[1.50, 2e-3, "GET /api/results/abc", "chain A:/B interface"]',
    '{"content":"REMARK /home/user/source.pdb\\nATOM unchanged\\n","smiles":"/C=C/C"}',
])
def test_audit103_unchanged_json_strings_preserve_original_bytes(text):
    assert portable_metadata(text).encode("utf-8") == text.encode("utf-8")


def test_audit103_json_path_is_changed_and_scientific_values_are_preserved():
    text = '{"message":"id1,/home/user/run/model.cif,0.5","metric":1.50,"labels":["구조/체인", "GET /api/results/abc"]}'
    assert json.loads(portable_metadata(text)) == {
        "message": "id1,[local path],0.5", "metric": 1.50,
        "labels": ["구조/체인", "GET /api/results/abc"],
    }


@pytest.mark.parametrize("text", [
    "chain A:/B interface", "A:/B", "id1,A:/B,0.5", "GET /api/results/abc", "GET /v1/models",
    "경로/체인 매핑; 구조/예측", "<td>임계값/예측 품질</td>",
    "path `N[C@@H](C)/C=C/C(=O)O`", "x|F/C=C/F",
    "https://example.org/home/user/run", "s3://bucket/nfs/lab/file",
])
def test_audit103_new_boundaries_preserve_nonpath_scientific_text(text):
    assert portable_metadata(text) == text


def test_audit103_opaque_and_numeric_metadata_survives_path_changes():
    _, entry = encoded_pae()
    value = {"message": "경로는/home/user/run 입니다", "pae": entry,
             "content": "ATOM exact /home/user/source.pdb\n",
             "smiles": "/home/C=C/C", "integrity": "sha512-/home/data+/value==",
             "labels": ["구조/체인", "결합/KD/친화도"],
             "metrics": [None, .123456789012345, 0, -1, 2 ** 63]}
    original = copy.deepcopy(value)
    result = portable_metadata(value)
    assert result == {**original, "message": "경로는[local path] 입니다"}
    assert value == original
