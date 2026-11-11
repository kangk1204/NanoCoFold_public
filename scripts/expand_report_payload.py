"""Expand saved NanoCoFold structure bindings for offline reference comparison.

This read-only helper leaves the source report, candidate scores and PAE arrays
as supplied. Pass its return value to the evidence reader when the saved report
uses compressed assets; use the standard renderer for a new presentation.
"""
import base64
import gzip
import hashlib
import io
import json


def expand_report_payload(payload):
    """Return a presentation with inline structures. Check every referenced asset."""
    if "offline_asset_format" not in payload:
        return payload
    if payload["offline_asset_format"] != "nanocofold-lossless-v1":
        raise ValueError("Unsupported saved-report asset format")
    pool = payload["structure_assets"]
    structures = []
    for binding in payload["structures"]:
        digest = binding["structure_ref"]
        asset = pool[digest]
        if asset["encoding"] != "gzip-base64-json":
            raise ValueError("Unsupported structure asset encoding")
        size = asset["bytes"]
        if type(size) is not int or size < 0 or asset["sha256"] != digest:
            raise ValueError("Structure asset hash or size mismatch")
        compressed = base64.b64decode(asset["data"], validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as src:
            raw = src.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Structure asset hash or size mismatch")
        body = json.loads(raw)
        if "raw_content_ref" in body:
            if body.pop("raw_content_ref") != "content" or "raw_content" in body or "content" not in body:
                raise ValueError("Invalid raw structure content reference")
            body["raw_content"] = body["content"]
        metadata = {key: value for key, value in binding.items() if key != "structure_ref"}
        if metadata.keys() & body.keys():
            raise ValueError("Ambiguous structure metadata")
        structures.append({**metadata, **body})
    result = {key: value for key, value in payload.items()
              if key not in {"offline_asset_format", "structure_assets"}}
    result["structures"] = structures
    return result
