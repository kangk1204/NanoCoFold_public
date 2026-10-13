"""Saved-coordinate interpretation only. No inference, rescoring or run writes."""
from __future__ import annotations

import io
import csv
import json
import re
from itertools import combinations
from pathlib import Path

import numpy as np
from Bio.PDB import PDBIO, Superimposer

from ..utils import pdb_io
from ..utils.seq import AA1
from .paths import contained, sha256


def closest_atoms(pair):
    a, b = list(pdb_io.iter_heavy_atoms(pair["res_a"])), list(pdb_io.iter_heavy_atoms(pair["res_b"]))
    distances = np.linalg.norm(np.array([x.coord for x in a])[:, None] - np.array([x.coord for x in b])[None, :], axis=-1)
    i, j = np.unravel_index(np.argmin(distances), distances.shape)
    return {"atom_a": a[i].name, "atom_b": b[j].name}


def cysteine_geometry(binder):
    cysteines = [r for r in pdb_io.iter_residues(binder) if r.resname == "CYS"]
    pairs = []
    for a, b in combinations(cysteines, 2):
        distance = float(np.linalg.norm(a["SG"].coord - b["SG"].coord)) if "SG" in a and "SG" in b else None
        pairs.append({"a": pdb_io.residue_label(a, binder.id), "b": pdb_io.residue_label(b, binder.id),
                      "distance": distance, "atom_a": "SG", "atom_b": "SG"})
    return {"residues": [pdb_io.residue_label(r, binder.id) for r in cysteines],
            "missing_sg": [pdb_io.residue_label(r, binder.id) for r in cysteines if "SG" not in r],
            "pairs": sorted(pairs, key=lambda p: p["distance"] if p["distance"] is not None else float("inf")),
            "note": "SG–SG 좌표 거리입니다. 공유결합·자유 Cys를 확정하지 않으며 서열 기반 제외 판정을 변경하지 않습니다."}


def _numbered_cdr_ranges(entry, sequence):
    if not isinstance(entry, dict) or entry.get("error") or entry.get("scheme_supported") is False:
        return {}
    positions = entry.get("imgt_positions")
    if (not isinstance(positions, list) or not positions
            or any(not isinstance(p, dict) or type(p.get("index")) is not int
                   or not 1 <= p["index"] <= len(sequence)
                   or not isinstance(p.get("aa"), str) or len(p["aa"]) != 1
                   or p["aa"] not in AA1 or p["aa"] != sequence[p["index"] - 1] for p in positions)):
        return {}
    # ANARCI numbers the antibody domain, excluding expression tags. Its
    # recorded bounds and indexes are already full-sequence 1-based positions.
    # Older receipts without either bound can prove only full-length coverage.
    if "domain_start" not in entry and "domain_end" not in entry:
        start, end = 1, len(sequence)
    else:
        start, end = entry.get("domain_start"), entry.get("domain_end")
        if (type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(sequence)):
            return {}
    if [p["index"] for p in positions] != list(range(start, end + 1)):
        return {}
    domain = "".join(p["aa"] for p in positions)
    if sequence.find(domain) != start - 1 or sequence.find(domain, start) >= 0:
        return {}
    ranges = entry.get("cdr_ranges")
    names = ("CDR1", "CDR2", "CDR3")
    if not isinstance(ranges, dict) or not all(name in ranges for name in names):
        return {}
    previous = start - 1
    for name in names:
        bounds = ranges[name]
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(value) is not int for value in bounds)
                or not start <= bounds[0] <= bounds[1] <= end or bounds[0] <= previous):
            return {}
        previous = bounds[1]
    if any("imgt" in position for position in positions):
        if any(type(position.get("imgt")) is not int or position["imgt"] < 1 for position in positions):
            return {}
        for name, (lo, hi) in {"CDR1": (27, 38), "CDR2": (56, 65), "CDR3": (105, 117)}.items():
            mapped = [position["index"] for position in positions if lo <= position["imgt"] <= hi]
            if not mapped or list(ranges[name]) != [min(mapped), max(mapped)]:
                return {}
    return {name: ranges[name] for name in names}


def _saved_cdr_ranges(reader, row, sequence):
    identifiers = [row.get("candidate_id"), row.get("source_sequence_id")]
    for name in ("04_developability/anarci_batch.log", "02_mature/anarci.log"):
        log = reader._path(name)
        if not log.is_file():
            continue
        for line in reversed(log.read_text(errors="replace").splitlines()):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            entries = payload.get("results", {})
            if isinstance(entries, dict):
                for identifier in identifiers:
                    ranges = _numbered_cdr_ranges(entries.get(identifier), sequence)
                    if ranges:
                        return ranges, "saved_anarci_numbering", name
            if name != "02_mature/anarci.log":
                continue
            ranges = _numbered_cdr_ranges(payload, sequence)
            if ranges:
                return ranges, "saved_anarci_numbering", name
            # Mature libraries substitute residues in the parent's recorded CDRs.
            # This maps those intervals; it never claims fresh mutant numbering.
            parent = reader.state.get("phases", {}).get("mature", {})
            if not isinstance(parent, dict) or reader.config.get("run", {}).get("mode") != "mature":
                continue
            wt = parent.get("wt_sequence")
            if not isinstance(wt, str) or len(wt) != len(sequence):
                continue
            ranges = _numbered_cdr_ranges(payload, wt)
            if not ranges or ranges != parent.get("cdr_ranges"):
                continue
            positions = set()
            for bounds in ranges.values():
                if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                        or any(type(v) is not int for v in bounds)
                        or not 1 <= bounds[0] <= bounds[1] <= len(wt)):
                    break
                positions.update(range(bounds[0], bounds[1] + 1))
            else:
                if any(a != b and i not in positions for i, (a, b) in enumerate(zip(wt, sequence), 1)):
                    continue
                selected = reader._path("02_mature/prefiltered_candidates.csv")
                if selected.is_file():
                    with selected.open(encoding="utf-8-sig", newline="") as handle:
                        if any(item.get("variant_id") in identifiers and item.get("sequence") == sequence
                               for item in csv.DictReader(handle)):
                            return ranges, "saved_parent_cdr_substitution_ranges", name
    return {}, "unavailable", None


def cdr_annotations(reader, row, binder):
    sequence = pdb_io.chain_sequence(binder)
    if sequence != row.get("sequence"):
        return {"ranges": {}, "source": "unavailable", "note": "후보 서열과 구조의 VHH 서열이 달라 CDR 위치를 표시하지 않습니다."}
    ranges, source, source_path = _saved_cdr_ranges(reader, row, sequence)
    details = row.get("cdr_details") or {}
    if not ranges and details.get("configured_source") == "config" and details.get("recorded_ranges"):
        ranges, source = details["recorded_ranges"], "saved_user_defined_ranges"
    residues = list(pdb_io.iter_residues(binder))
    result = {}
    for name, bounds in ranges.items():
        if (name not in {"CDR1", "CDR2", "CDR3"} or not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(type(v) is not int for v in bounds) or not 1 <= bounds[0] <= bounds[1] <= len(residues)):
            continue
        result[name] = {"start": bounds[0], "end": bounds[1],
                        "labels": [pdb_io.residue_label(r, binder.id) for r in residues[bounds[0]-1:bounds[1]]]}
    return {"ranges": result, "source": source, "source_path": source_path,
            "note": "저장된 ANARCI 번호 매김" if source == "saved_anarci_numbering" else
                    "저장된 WT ANARCI 구간 · 길이 변화 없는 CDR 치환 후보에 대응" if source == "saved_parent_cdr_substitution_ranges" else
                    "저장된 사용자 지정 서열 구간" if source == "saved_user_defined_ranges" else "저장된 검증 가능 CDR 번호 매김이 없습니다."}


def verified_reference(reader):
    """Accept portable copies or an exact hash-verified platform job snapshot."""
    portable = reader._path("input_reference_manifest.json")
    if portable.is_file():
        info = json.loads(portable.read_text())
        path = reader._path(info["path"])
        if sha256(path) != info["sha256"]:
            raise ValueError("원본 참조 좌표 해시 불일치")
        return path, info
    # Backward compatibility without modifying older result directories. Only
    # the same workspace's exact job snapshot is eligible; imported paths in a
    # YAML file alone never authorize reading arbitrary host files.
    if reader.root.parent.name != "runs" or not re.fullmatch(r"[0-9a-f]{32}", reader.root.name):
        raise FileNotFoundError("이 결과에 보존된 원본 참조 좌표가 없습니다.")
    manifest = reader._json("platform_manifest.json")
    workspace = reader.root.parent.parent
    folder = contained(workspace, workspace / ".nanocofold/jobs" / reader.root.name, exists=True)
    for field in ("input_review.reference_structure_path", "target.pdb"):
        entry = next((e for e in manifest.get("inputs", []) if e.get("field") == field), None)
        if not entry:
            continue
        path = contained(folder, entry["snapshot"], exists=True)
        section, key = field.split(".")
        configured = Path(reader.config.get(section, {}).get(key) or "").resolve()
        if path != configured or sha256(path) != entry["sha256"]:
            raise ValueError("원본 참조 스냅샷 경로/해시 불일치")
        return path, {"sha256": entry["sha256"], "field": field, "source": "verified_job_snapshot"}
    raise FileNotFoundError("보존된 좌표가 없습니다. FASTA 입력에는 실험 좌표가 제공되지 않습니다.")


def reference_overlay(reader, target, mapping):
    try:
        path, provenance = verified_reference(reader)
        reference = pdb_io.load_structure(path)
        review = reader._json("input_review.json")
        chains = review.get("chains", [])
        prediction = review.get("preparation") == "sequence_prediction"
        ref_chain = review.get("reference_chains", [{}])[0].get("id", "A") if prediction else "A"
        inverse = {int(v): int(k) for k, v in (chains[0].get("observed_to_full", {}) if chains else {}).items()}
        ref_residues = {chain.id: {i: r for i, r in enumerate(pdb_io.iter_residues(chain), 1)} for chain in next(reference.get_models())}
        target_residues = list(pdb_io.iter_residues(target))
        rows = [r for r in mapping if r.get("output_chain") == "A"]
        correspondences = []
        if rows:
            for row in rows:
                pos = int(row["output_position"])
                ref_pos = row.get("reference_position")
                if "reference_position" not in row:
                    ref_pos = inverse.get(pos) if prediction else pos
                if ref_pos not in (None, ""):
                    correspondences.append((pos, row.get("reference_chain") or ref_chain, int(ref_pos)))
        elif not prediction:
            # Legacy direct-coordinate inputs are aligned only on an identical
            # complete sequence, never on an inferred ambiguous alignment.
            chain = pdb_io.get_chain(reference, "A")
            if pdb_io.chain_sequence(chain) == pdb_io.chain_sequence(target):
                correspondences = [(i, "A", i) for i in range(1, len(target_residues)+1)]
        fixed, moving = [], []
        for pos, chain, ref_pos in correspondences:
            original = ref_residues.get(chain, {}).get(ref_pos)
            predicted = target_residues[pos-1] if 1 <= pos <= len(target_residues) else None
            if (original is not None and predicted is not None and original.resname == predicted.resname
                    and "CA" in original and "CA" in predicted
                    and np.isfinite(original["CA"].coord).all() and np.isfinite(predicted["CA"].coord).all()):
                fixed.append(predicted["CA"])
                moving.append(original["CA"])
        if len(fixed) < 3 or any(np.linalg.matrix_rank(np.array([a.coord for a in atoms]) - np.mean([a.coord for a in atoms], axis=0)) < 2 for atoms in (fixed, moving)):
            raise ValueError("원본과 예측의 명확한 비공선 Cα 대응이 3개 미만입니다.")
        fit = Superimposer()
        fit.set_atoms(fixed, moving)
        fit.apply(reference.get_atoms())
        stream = io.StringIO()
        writer = PDBIO()
        writer.set_structure(reference)
        writer.save(stream)
        return {"available": True, "content": stream.getvalue(), "format": "pdb", "matched_ca": len(fixed),
                "rmsd": float(fit.rms), "reference_sha256": provenance["sha256"],
                "method": "target_CA_rigid_fit", "note": "보존된 입력 표적·파트너 좌표를 표적 Cα로 강체 정렬합니다. 실험 B-factor를 pLDDT로 해석하지 않습니다."}
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return {"available": False, "reason": str(exc)}
