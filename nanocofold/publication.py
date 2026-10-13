"""Shared checks for published tables and derived report completion."""
from pathlib import Path
import hashlib


TABLES = ("03_score/scored_candidates.csv", "05_final/selected_candidates.csv")


def table_hashes(root):
    return {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in TABLES}


def tables_available(root, final):
    if not final or not all((Path(root) / name).is_file() for name in TABLES):
        return False
    # Historical records predate the explicit checkpoint; never override a
    # current failed attempt with historical files still present on disk.
    if "tables_committed" in final and final["tables_committed"] is not True:
        return False
    expected = final.get("table_sha256")
    return expected is None or expected == table_hashes(root)


def publication_issue(final):
    if "tables_committed" in final and final["tables_committed"] is not True:
        return "최신 계산 표가 확정되지 않았습니다. 이전 표를 완료 결과로 승격하지 않습니다."
    for key in ("exports_status", "report_status", "delivery_status"):
        if (key == "exports_status" and final.get(key) == "not_applicable"
                and final.get("cascade") is True and final.get("tables_committed") is True
                and type(final.get("n_selected")) is int and final["n_selected"] == 0
                and final.get("synthesis") is None and final.get("handoff_gate") is None):
            # An empty cascade publishes evaluated/failed observations and an
            # empty selection table, but has no synthesis/handoff exports.
            # This permits evidence delivery, never inference success: the
            # cascade summary and failed rows still determine analysis status.
            continue
        if key in final and final[key] != "complete":
            return f"산출물 발행 미완료: {key}={final[key]}"
    return None


def gate_summary(gate):
    if not isinstance(gate, dict) or type(gate.get("ok")) is not bool:
        return None
    def count(name):
        values = gate.get(name)
        if isinstance(values, list):
            return len(values)
        value = gate.get("n_" + name)
        return value if type(value) is int and value >= 0 else None
    errors, warnings = count("errors"), count("warnings")
    inconsistent = gate["ok"] is False and errors == 0
    return {"ok": gate["ok"], "n_errors": errors, "n_warnings": warnings,
            "diagnostic": ("미통과 사유가 기록되지 않았습니다. 원본 게이트 기록을 확인하세요." if inconsistent else
                           "오류·경고 개수 기록이 불완전합니다. 원본 게이트 기록을 확인하세요." if errors is None or warnings is None else None)}
