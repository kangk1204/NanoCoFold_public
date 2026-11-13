"""Search and export saved VHH observations across accessible analyses."""

import contextlib
import math
import tempfile
from pathlib import Path

FIELDS = {
    "name": ("r.name", "분석", "Analysis"),
    "researchers": ("researchers", "담당자", "Researcher"),
    "target": ("r.target_name", "표적", "Target"),
    "candidate_id": ("e.candidate_id", "후보 ID", "Candidate ID"),
    "sequence": ("s.sequence", "VHH 서열", "VHH sequence"),
    "engine": ("e.engine", "예측 엔진", "Prediction engine"),
    "decision": ("e.decision", "기록 판정", "Recorded decision"),
    "selection_rank": ("e.selection_rank", "최종 선별 순위", "Final selection rank"),
    "ipsae": ("e.ipsae", "ipSAE", "ipSAE"),
    "iptm": ("e.iptm", "ipTM", "ipTM"),
    "interface_pae": ("e.interface_pae", "계면 PAE (Å)", "Interface PAE (Å)"),
    "created": ("r.created", "분석 일시", "Run date"),
    "result_id": ("e.result_id", "결과 ID", "Result ID"),
    "mock": ("json_extract(r.payload,'$.mock')", "연습 실행", "Practice run"),
    "status": ("r.status", "실행 상태", "Run status"),
}
DECISIONS = {
    "eligible": ("적격", "Eligible"),
    "ineligible": ("부적격", "Ineligible"),
    "held": ("검토 보류", "Held for review"),
    "unassessed": ("미평가", "Not evaluated"),
    "control": ("WT·대조군", "WT / control"),
    "failed": ("평가 오류", "Evaluation error"),
}


def initialize(db):
    # Migrating an install.5 catalog preserves its sequence links until each saved run is indexed.
    if "target_name" not in {row[1] for row in db.execute("PRAGMA table_info(results)")}:
        db.execute("ALTER TABLE results ADD COLUMN target_name TEXT")
        for key in (
            "engine TEXT",
            "decision TEXT",
            "selection_rank INTEGER",
            "ipsae REAL",
            "iptm REAL",
            "interface_pae REAL",
        ):
            db.execute("ALTER TABLE vhh_entries ADD COLUMN " + key)
        db.execute("UPDATE results SET vhh_index=0")
    db.execute("CREATE INDEX IF NOT EXISTS vhh_engine_decision ON vhh_entries(engine,decision,selection_rank)")
    db.execute("CREATE INDEX IF NOT EXISTS vhh_decision ON vhh_entries(decision,result_id)")


def metrics(row):
    from ..developability import review_only_hold

    if row.get("is_parent_control") or row.get("source_sequence_id") == "WT" or row["candidate_id"] == "WT":
        decision = "control"
    elif row.get("error") or row.get("prediction_error") or row.get("prediction_status") == "failed":
        decision = "failed"
    elif not row.get("evaluated"):
        decision = "unassessed"
    elif row.get("final_eligible") is True:
        decision = "eligible"
    elif review_only_hold(row):
        decision = "held"
    elif row.get("final_eligible") is False:
        decision = "ineligible"
    else:
        decision = "unassessed"
    rank = row.get("selection_rank") if row.get("selected") else None
    numbers = [
        v if type(v) in (int, float) and math.isfinite(v) else None
        for v in (row.get(k) for k in ("ipsae", "iptm", "interface_pae"))
    ]
    return (
        row.get("predictor_engine") or row.get("engine"),
        decision,
        rank if type(rank) is int and rank > 0 else None,
        *numbers,
    )


@contextlib.contextmanager
def query(
    catalog,
    *,
    search="",
    target="",
    researcher="",
    engine="",
    decision="",
    sort="created",
    descending=True,
    include_mock=False,
):
    if (
        not all(isinstance(v, str) for v in (search, target, researcher, engine, decision, sort))
        or len(search) > 2000
        or len(target) > 500
        or len(researcher) > 48
        or len(engine) > 60
        or (decision and decision not in DECISIONS)
        or sort not in FIELDS
        or type(descending) is not bool
        or type(include_mock) is not bool
    ):
        raise ValueError("Invalid candidate search filters.")
    with catalog.connect() as db:
        usage = False
        if (catalog.root / "jobs.sqlite3").is_file():
            db.execute("ATTACH DATABASE ? AS activity", (str(catalog.root / "jobs.sqlite3"),))
            usage = bool(db.execute("SELECT 1 FROM activity.sqlite_master WHERE name='lab_usage'").fetchone())
        db.execute("BEGIN")
        conditions, args = ["r.vhh_index=1"], []
        if researcher:
            conditions.append(
                "EXISTS (SELECT 1 FROM activity.lab_usage u WHERE u.result_id=r.id AND u.researcher=?)"
                if usage
                else "0"
            )
            if usage:
                args.append(researcher)
        if not include_mock:
            conditions.append("COALESCE(json_extract(r.payload,'$.mock'),0)=0")
        if search:
            frag = "".join(search.split()).upper()
            conditions.append(
                "(instr(lower(e.candidate_id),lower(?)) OR instr(lower(r.name),lower(?)) OR instr(lower(r.id),lower(?)) OR instr(s.sequence,?))"
            )
            args.extend((search, search, search, frag if len(frag) >= 3 else None))
        if target:
            conditions.append("instr(lower(r.target_name),lower(?))>0")
            args.append(target)
        for key, value in (("engine", engine), ("decision", decision)):
            if value:
                conditions.append("e." + key + "=?")
                args.append(value)
        owner_expr = (
            (
                "(SELECT group_concat(researcher, ', ') FROM (SELECT DISTINCT researcher FROM activity.lab_usage u "
                "WHERE u.result_id=r.id ORDER BY researcher))"
            )
            if usage
            else "NULL"
        )
        selected = ",".join(
            (owner_expr if key == "researchers" else value[0]) + " AS " + key for key, value in FIELDS.items()
        )
        source = (
            " FROM vhh_entries e JOIN results r ON r.id=e.result_id LEFT JOIN vhh_sequences s ON s.id=e.sequence_id WHERE "
            + " AND ".join(conditions)
        )
        # Stable ties preserve engine and run identity. Missing values always sort last.
        order = (
            " ORDER BY ("
            + sort
            + " IS NULL),"
            + sort
            + (" DESC" if descending else " ASC")
            + ",result_id,candidate_id"
        )
        yield db, "SELECT " + selected + source + order, source, args, usage


def page(catalog, offset=0, limit=25, **filters):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid candidate page.")
    with query(catalog, **filters) as (db, sql, source, args, usage):
        total = db.execute("SELECT count(*)" + source, args).fetchone()[0]
        rows = db.execute(sql + " LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        status = catalog.sequence_index_status(db, filters.get("researcher"), usage)
        return {"items": [dict(row) for row in rows], "total": total, **status}


def workbook(catalog, lang, **filters):
    from datetime import datetime, timezone
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell

    if lang not in {"ko", "en"}:
        raise ValueError("Choose English or Korean.")
    language = 1 if lang == "en" else 0
    book = Workbook(write_only=True)

    def sheet(number):
        item = book.create_sheet(("Candidates" if language else "후보") + f" {number}")
        item.freeze_panes = "A2"
        item.append([v[language + 1] for v in FIELDS.values()])
        return item

    with tempfile.NamedTemporaryFile(prefix="nanocofold-candidates-", suffix=".xlsx", delete=False) as temp:
        path = Path(temp.name)
    try:
        out = sheet(1)
        with query(catalog, **filters) as (db, sql, _source, args, _usage):
            for i, row in enumerate(db.execute(sql, args)):
                if i and i % 1048575 == 0:
                    out.auto_filter.ref = "A1:O1048576"
                    out = sheet(i // 1048575 + 1)
                values = []
                for key in FIELDS:
                    v = row[key]
                    if key == "created":
                        v = datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    if key == "decision":
                        v = DECISIONS.get(v, ("", ""))[language]
                    if key == "mock":
                        v = bool(v)
                    cell = WriteOnlyCell(out, value=v)
                    if isinstance(v, str):
                        cell.data_type = "s"
                    values.append(cell)
                out.append(values)
        out.auto_filter.ref = f"A1:O{(i % 1048575 + 2) if 'i' in locals() else 1}"
        book.save(path)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
