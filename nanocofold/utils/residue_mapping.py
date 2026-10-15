"""Reversible spreadsheet-safe residue correspondence tables.

The CSV cells remain safe to open in a spreadsheet. A versioned, self-contained
metadata column records their exact source text so copying the CSV alone never
loses author chain IDs, signed residue labels or literal leading apostrophes.
Legacy tables without this column are read verbatim; no prefix is guessed.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .spreadsheet import csv_cell

EXACT_TEXT_COLUMN = "_nanocofold_exact_text_v1"


def write_residue_mapping(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Residue mapping must contain at least one row")
    fields = list(rows[0])
    if EXACT_TEXT_COLUMN in fields:
        raise ValueError("Reserved residue mapping encoding column")
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*fields, EXACT_TEXT_COLUMN])
        writer.writeheader()
        for row in rows:
            if set(row) != set(fields):
                raise ValueError("Residue mapping columns differ between rows")
            exact = {key: "" if value is None else str(value) for key, value in row.items()}
            writer.writerow({**{key: csv_cell(value) for key, value in exact.items()},
                             EXACT_TEXT_COLUMN: json.dumps(exact, ensure_ascii=False, sort_keys=True, allow_nan=False)})


def read_residue_mapping(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames
        if not fields or any(not field for field in fields) or len(set(fields)) != len(fields):
            raise ValueError("Invalid residue mapping CSV header")
        if any(field.startswith("_nanocofold_exact_text_") and field != EXACT_TEXT_COLUMN for field in fields):
            raise ValueError("Unsupported residue mapping exact-text encoding version")
        result = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Residue mapping CSV column count mismatch")
            if EXACT_TEXT_COLUMN in fields:
                metadata = row.pop(EXACT_TEXT_COLUMN)
                exact = json.loads(metadata)
                if (not isinstance(exact, dict) or set(exact) != set(row)
                        or any(not isinstance(value, str) or csv_cell(value) != row[key]
                               for key, value in exact.items())):
                    raise ValueError("Residue mapping exact-text receipt does not match CSV cells")
                row = {key: exact[key] for key in row}
            result.append(row)
        return result
