"""Shared, lossless metadata contract for saved model rows and structures.

Builders do not score, infer native provenance, or turn missing fields into
successful evaluations. Mode-specific producers provide their recorded facts.
Coordinate text, PAE arrays, extension fields and scientific values are kept.
"""
from collections.abc import Mapping
from typing import Any, TypedDict


class ModelObservationRecord(TypedDict, total=False):
    observation_schema_version: int
    mode: str
    candidate_id: str
    engine: str | None
    evaluated: bool | None
    error: str | None
    iptm_scope: str | None
    ipsae_scope: str | None
    coordinate_source: dict | None


class StructurePayload(TypedDict, total=False):
    structure_schema_version: int
    mode: str
    candidate_id: str
    content: str
    format: str
    raw_content: str | None
    raw_format: str | None
    coordinate_source: dict
    iptm_scope: str | None
    ipsae_scope: str | None
    evaluated: bool | None
    error: str | None


_OBSERVATION_FIELDS = ("engine", "evaluated", "error", "iptm_scope", "ipsae_scope")


def model_observation_record(record: Mapping[str, Any], *, mode: str) -> dict:
    """Add common declarations without changing a producer's recorded values."""
    if not isinstance(record, Mapping):
        raise TypeError("Model observation record must be a mapping")
    result = dict(record)
    result.setdefault("observation_schema_version", 1)
    result.setdefault("mode", mode)
    for name in _OBSERVATION_FIELDS:
        result.setdefault(name, None)
    result.setdefault("coordinate_source", None)
    return result


def structure_payload(payload: Mapping[str, Any], *, observation=None, mode: str) -> dict:
    """Share row scope/status while preserving coordinates and native receipts.

    Large immutable coordinate/PAE values are not copied or recomputed. Only
    the returned top-level mapping is extended; input mappings are untouched.
    A structure file on its own is not proof of successful prediction/scoring.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("Structure payload must be a mapping")
    result = dict(payload)
    observation = model_observation_record(observation or {}, mode=mode)
    result.setdefault("structure_schema_version", 1)
    result.setdefault("mode", mode)
    for name in _OBSERVATION_FIELDS:
        if result.get(name) is None and observation[name] is not None:
            result[name] = observation[name]
        else:
            result.setdefault(name, None)
    result.setdefault("raw_content", None)
    result.setdefault("raw_format", None)
    result.setdefault("coordinate_source", {
        "schema_version": 2, "raw_preserved": False, "raw_path": None,
        "raw_sha256": None, "display_relation": "unverified_legacy",
        "verification": {"status": "unavailable", "reason": "native_origin_not_recorded"},
    })
    return result
