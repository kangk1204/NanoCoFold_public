"""Lossless, content-addressed assets for self-contained result reports.

Only the presentation copy changes. Coordinates and canonical float64 PAE bytes
retain their hashes; the browser expands just the selected model.
"""
import base64
import gzip
import hashlib
import json


def compact_payload(payload, *, min_bytes=8 * 1024 * 1024):
    if payload.get("offline_asset_format") == "nanocofold-lossless-v1":
        return payload
    structures = payload.get("structures", [])
    # Small and historical report consumers keep the established inline form.
    if sum(len(s.get("content") or "") + len(s.get("raw_content") or "") for s in structures) < min_bytes:
        return payload
    pool, bindings = {}, []
    for structure in structures:
        asset_keys = {"content", "raw_content", "raw_format", "format", "chains", "hotspots", "contacts", "clashes",
                      "pae", "residue_mapping", "cdr", "cysteines", "reference", "target_region"}
        binding = {key: value for key, value in structure.items() if key not in asset_keys}
        body = {key: value for key, value in structure.items() if key in asset_keys}
        if body.get("raw_content") is not None and body.get("raw_content") == body.get("content"):
            body.pop("raw_content")
            body["raw_content_ref"] = "content"
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in pool:
            pool[digest] = {"encoding": "gzip-base64-json", "bytes": len(raw), "sha256": digest,
                            "data": base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii")}
        bindings.append({**binding, "structure_ref": digest})
    arrays = {}
    for key, record in payload.get("pae_arrays", {}).items():
        if record.get("encoding") != "gzip-base64-float64le":
            arrays[key] = record
            continue
        raw = gzip.decompress(base64.b64decode(record["data"], validate=True))
        expected = record["dimension"] ** 2 * 8
        if len(raw) != expected or record["bytes"] != expected or hashlib.sha256(raw).hexdigest() != key or record["sha256"] != key:
            raise ValueError("PAE array hash or size mismatch")
        # Shuffle bytes, never numbers: this preserves every IEEE-754 bit.
        shuffled = b"".join(raw[offset::8] for offset in range(8))
        arrays[key] = {**record, "encoding": "gzip-base64-shuffled-float64le",
                       "data": base64.b64encode(gzip.compress(shuffled, compresslevel=9, mtime=0)).decode("ascii")}
    return {**payload, "structures": bindings, "structure_assets": pool, "pae_arrays": arrays,
            "offline_asset_format": "nanocofold-lossless-v1"}
