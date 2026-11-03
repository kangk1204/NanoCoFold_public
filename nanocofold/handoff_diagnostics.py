"""Present saved handoff receipts without validating, approving or changing a run."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .handoff_i18n import localize_handoff

SOURCE = "05_final/handoff_gate.json"
MAX_BYTES = 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate diagnostic key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("Nonfinite diagnostic value")


def _summary(value):
    if (not isinstance(value, dict) or type(value.get("ok")) is not bool
            or any(type(value.get(k)) is not int or value[k] < 0 for k in ("n_errors", "n_warnings"))):
        return None
    if value["ok"] != (value["n_errors"] == 0):
        return None
    return {k: value[k] for k in ("ok", "n_errors", "n_warnings")}


def saved_handoff_diagnostics(root: Path, recorded=None):
    """Read only a bounded, run-contained receipt. Missing details never imply pass."""
    recorded_summary = _summary(recorded)
    result = {"schema_version": 1, "source": SOURCE, "source_sha256": None,
              "source_bytes": None, "detail_status": "missing", "reason": "receipt_missing",
              "status": "blocked" if recorded_summary and recorded_summary["ok"] is False else "unknown",
              "ok": None, "n_errors": None, "n_warnings": None,
              "recorded_summary": recorded_summary, "errors": None, "warnings": None,
              "readonly": True, "revalidated": False}
    try:
        root = Path(root).resolve()
        path = (root / SOURCE).resolve()
        if not path.is_relative_to(root):
            result.update(detail_status="unavailable", reason="receipt_outside_run")
            return result
        if not path.exists():
            return result
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            result.update(detail_status="invalid", reason="receipt_size_or_type_invalid")
            return result
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            result.update(detail_status="invalid", reason="receipt_size_or_type_invalid")
            return result
        result.update(source_sha256=hashlib.sha256(raw).hexdigest(), source_bytes=len(raw))
        gate = json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)
        if (not isinstance(gate, dict) or type(gate.get("ok")) is not bool
                or any(not isinstance(gate.get(k), list) for k in ("errors", "warnings"))
                or any(not isinstance(item, dict) or not isinstance(item.get("code"), str)
                       or not isinstance(item.get("message"), str)
                       for key in ("errors", "warnings") for item in gate[key])
                or gate["ok"] != (len(gate["errors"]) == 0)):
            result.update(detail_status="invalid", reason="receipt_schema_invalid")
            return result
        counts = {"ok": gate["ok"], "n_errors": len(gate["errors"]), "n_warnings": len(gate["warnings"])}
        localized = {lang: localize_handoff(gate, lang) for lang in ("ko", "en")}
        for key in ("errors", "warnings"):
            result[key] = [{**copy.deepcopy(item), "message_i18n": {
                lang: localized[lang][key][i]["message"] for lang in ("ko", "en")}}
                for i, item in enumerate(gate[key])]
        result.update(counts)
        if recorded is not None and (recorded_summary is None or recorded_summary != counts):
            result.update(detail_status="inconsistent", reason="recorded_summary_mismatch", status="unknown")
            return result
        result.update(detail_status="available", reason=None, status="passed" if gate["ok"] else "blocked")
        return result
    except (OSError, ValueError, TypeError, RecursionError):
        result.update(detail_status="invalid", reason="receipt_unreadable_or_invalid")
        return result
