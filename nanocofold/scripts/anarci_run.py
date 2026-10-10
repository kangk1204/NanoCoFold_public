#!/usr/bin/env python
"""ANARCI(BSD-3)로 VHH 서열을 IMGT 넘버링하고 CDR 범위를 JSON으로 출력한다.

사용(antibody env): python anarci_run.py <sequence> <scheme>
출력: {"cdr1": [start, end], "cdr2": [...], "cdr3": [...], "imgt_positions": [...]}
start/end 는 1-based **서열 인덱스**(inclusive, 갭 잔기 제외).

중요: ANARCI `number()` 결과는 갭 위치를 `((번호, 삽입코드), '-')` 로 반환한다.
갭은 서열에 존재하는 잔기가 아니므로 서열 인덱스를 증가시키면 안 된다(F01).
"""
from __future__ import annotations

import json
import sys

try:
    from anarci import number
except ImportError:  # pragma: no cover - antibody env 전용
    number = None  # type: ignore[assignment]

IMGT_CDR = {"CDR1": (27, 38), "CDR2": (56, 65), "CDR3": (105, 117)}
GAP_CHARS = {"-", ".", "*"}


def compute_cdr_ranges(numbering, scheme: str = "imgt", source_sequence: str | None = None) -> dict:
    """ANARCI 넘버링 리스트에서 서열 인덱스 기준 CDR 범위를 계산한다.

    - 갭/결손(`'-'` 등)은 건너뛰고 서열 인덱스를 증가시키지 않는다.
    - CDR 경계는 IMGT 스킴일 때만 적용한다(다른 스킴은 빈 범위 + 사유 반환).
    """
    imgt_positions: list[dict] = []
    ranges: dict[str, list[int] | None] = {"CDR1": None, "CDR2": None, "CDR3": None}
    sequence_index = 0
    gap_count = 0
    if numbering is None:
        return {"cdr_ranges": {}, "imgt_positions": [], "gap_count": 0, "scheme_supported": False}
    numbering = list(numbering)
    domain = "".join(str(entry[1]) for entry in numbering if entry is not None and str(entry[1]).strip() not in GAP_CHARS)
    offset = 0
    if source_sequence is not None:
        offset = source_sequence.find(domain) if domain else -1
        if offset < 0 or source_sequence.find(domain, offset + 1) >= 0:
            raise ValueError("ANARCI numbered domain does not map uniquely to the source sequence")
    sequence_index = offset
    for entry in numbering:
        if entry is None:
            continue
        (imgt_number, insertion), aa = entry
        if str(aa).strip() in GAP_CHARS:
            gap_count += 1
            continue
        sequence_index += 1
        imgt_positions.append({
            "index": sequence_index,
            "imgt": imgt_number,
            "insertion": insertion,
            "aa": aa,
        })
        if str(scheme).lower() != "imgt":
            continue
        for cdr_name, (start, end) in IMGT_CDR.items():
            if start <= imgt_number <= end:
                if ranges[cdr_name] is None:
                    ranges[cdr_name] = [sequence_index, sequence_index]
                else:
                    ranges[cdr_name][1] = sequence_index
    return {
        "cdr_ranges": {name: value for name, value in ranges.items() if value},
        "imgt_positions": imgt_positions,
        "gap_count": gap_count,
        "scheme_supported": str(scheme).lower() == "imgt",
        "domain_start": offset + 1,
        "domain_end": sequence_index,
    }


def _read_fasta(path: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    name: str | None = None
    chunks: list[str] = []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(chunks)))
            name = line[1:].split()[0]
            chunks = []
        else:
            chunks.append(line)
    if name is not None:
        records.append((name, "".join(chunks)))
    return records


def run_batch(fasta_path: str, scheme: str) -> int:
    if number is None:
        print(json.dumps({"error": "anarci_not_installed"}))
        return 1
    results: dict = {}
    for name, sequence in _read_fasta(fasta_path):
        numbering, chain_type = number(sequence, scheme=scheme)
        if not numbering:
            results[name] = {"error": "numbering_failed", "chain_type": str(chain_type)}
            continue
        try:
            entry = compute_cdr_ranges(numbering, scheme=scheme, source_sequence=sequence)
        except ValueError:
            results[name] = {"error": "numbering_source_mapping_failed", "chain_type": str(chain_type)}
            continue
        entry["chain_type"] = str(chain_type)
        results[name] = entry
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: anarci_run.py <sequence> [scheme] | --fasta <file> [scheme]", file=sys.stderr)
        return 2
    if sys.argv[1] == "--fasta":
        if len(sys.argv) < 3:
            print("usage: anarci_run.py --fasta <file> [scheme]", file=sys.stderr)
            return 2
        scheme = sys.argv[3] if len(sys.argv) > 3 else "imgt"
        return run_batch(sys.argv[2], scheme)
    sequence = sys.argv[1].strip()
    scheme = sys.argv[2] if len(sys.argv) > 2 else "imgt"

    if number is None:
        print(json.dumps({"error": "anarci_not_installed"}))
        return 1

    numbering, chain_type = number(sequence, scheme=scheme)
    if not numbering:
        print(json.dumps({"error": "numbering_failed", "chain_type": str(chain_type)}))
        return 1

    try:
        result = compute_cdr_ranges(numbering, scheme=scheme, source_sequence=sequence)
    except ValueError:
        print(json.dumps({"error": "numbering_source_mapping_failed", "chain_type": str(chain_type)}))
        return 1
    result["chain_type"] = str(chain_type)
    if not result["scheme_supported"]:
        result["warning"] = f"IMGT CDR 경계는 scheme={scheme} 에 적용되지 않습니다."
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
