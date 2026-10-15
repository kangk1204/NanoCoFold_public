"""Explicit construct cropping and hotspot masks with original numbering retained."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from ..hotspots import parse_hotspot_spec
from .paths import sha256


def _range(value, length):
    if not isinstance(value, dict):
        raise ValueError("표적 구간은 시작·끝 위치 객체여야 합니다.")
    start, end = value.get("start", 1), value.get("end", length)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= length:
        raise ValueError(f"표적 구간은 1–{length} 범위의 시작 ≤ 끝 정수여야 합니다.")
    return start, end


def apply_regions(folder: Path, cfg: dict, review: dict, payload: dict):
    sequence = cfg["target"]["sequence"]
    length = len(sequence)
    if not cfg.get("input_review"):
        reference = folder / "reference.fasta"
        reference.write_text(f">A\n{sequence}\n")
        cfg["input_review"] = {"preparation": "fasta_prediction", "reference_path": str(reference),
                               "reference_sha256": sha256(reference), "provider": cfg["structure"]["provider"],
                               "target_name": cfg["target"]["name"], "context_names": [], "context_sequences": [],
                               "prediction_hotspots": cfg["target"]["hotspots"]}
    start, end = _range(payload.get("target_region", {}), length)
    exclusions = payload.get("hotspot_exclusions", [])
    if not isinstance(exclusions, list) or len(exclusions) > 100:
        raise ValueError("결합 부위 제외 구간은 100개 이하 목록이어야 합니다.")
    ranges, excluded = [], set()
    for entry in exclusions:
        a, b = _range(entry, length)
        label = entry.get("label", "사용자 제외 구간")
        if not isinstance(label, str) or len(label) > 200:
            raise ValueError("구간 이름은 200자 이하 문자열이어야 합니다.")
        ranges.append({"start": a, "end": b, "label": label})
        excluded.update(range(max(start, a), min(end, b) + 1))
    if excluded == set(range(start, end + 1)):
        raise ValueError("선택한 표적 전체를 hotspot에서 제외할 수 없습니다.")
    entries = parse_hotspot_spec(cfg["target"].get("hotspots"), "A")
    if any(e["chain"] != "A" or not start <= e["number"] <= end or e["number"] in excluded for e in entries):
        raise ValueError("선택한 결합 부위가 분석 구간 밖이거나 제외 구간에 포함됩니다. 부위를 다시 검토하세요.")
    cropped = start != 1 or end != length
    predicted = review.get("preparation") == "sequence_prediction"
    mapping_path = folder / "residue_mapping.csv"
    mapping = []
    if mapping_path.exists():
        with mapping_path.open(encoding="utf-8-sig", newline="") as stream:
            mapping = list(csv.DictReader(stream))
    # Sequence predictions refer to selected normalized chains, while the
    # coordinate export already renames the target to A.
    reference_chains = review.get("reference_chains", [])
    prediction_chains = review.get("chains", [])
    for row in mapping:
        output = row["output_chain"]
        chain_index = ord(output) - 65
        pos = int(row["output_position"])
        if predicted and chain_index < len(prediction_chains):
            inverse = {int(v): int(k) for k, v in prediction_chains[chain_index].get("observed_to_full", {}).items()}
            row["reference_chain"] = reference_chains[chain_index]["id"]
            row["reference_position"] = inverse.get(pos, "")
        else:
            row["reference_chain"] = output
            row["reference_position"] = pos
        row["source_full_position"] = pos
        row["hotspot_excluded"] = output == "A" and pos in excluded
    mapping = [r for r in mapping if r["output_chain"] != "A" or start <= int(r["output_position"]) <= end]
    for row in mapping:
        if row["output_chain"] == "A":
            row["output_position"] = int(row["output_position"]) - start + 1
    if cropped and cfg["target"].get("pdb"):
        from ..utils import pdb_io
        pdb = Path(cfg["target"]["pdb"])
        reference = folder / "reference_full_target.pdb"
        shutil.copy2(pdb, reference)
        structure = pdb_io.load_structure(pdb)
        chain = pdb_io.get_chain(structure, "A")
        residues = list(pdb_io.iter_residues(chain))
        if len(residues) != length:
            raise ValueError("좌표·서열 길이가 달라 구간을 임의 절단하지 않았습니다.")
        retained = [r.copy() for r in residues[start - 1:end]]
        for residue in list(chain):
            chain.detach_child(residue.id)
        for i, residue in enumerate(retained, 1):
            residue.id = (" ", i, " ")
            chain.add(residue)
        pdb_io.save_structure(structure, pdb)
        cfg["input_review"].update(reference_structure_path=str(reference), reference_structure_sha256=sha256(reference),
                                   target_sha256=sha256(pdb))
    cfg["target"]["sequence"] = sequence[start - 1:end]
    if entries:
        kind = "seq" if predicted or cfg["input_review"].get("preparation") == "fasta_prediction" else "pdb"
        cfg["target"]["hotspots"] = [f"A:{kind}{e['number'] - start + 1}" for e in entries]
    mask = sorted(p - start + 1 for p in excluded)
    cfg.setdefault("hotspots", {}).setdefault("auto", {})["exclude_positions"] = mask
    region = {"start": start, "end": end, "original_length": length,
              "basis": "full_sequence" if predicted or not cfg["target"].get("pdb") else "observed_standard_sequence",
              "hotspot_exclusions": ranges, "excluded_output_positions": mask,
              "statement": "도메인·태그·융합 구간 이름은 사용자 지정입니다. 자동으로 생물학적 영역을 확정하지 않습니다."}
    review["target_region"] = region
    review["target_sequence"] = cfg["target"]["sequence"]
    if cropped:
        review.setdefault("warnings", []).append(f"표적 구간 {start}–{end}/{length}만 분석합니다. 원본·번호 대응은 보존하며 절단으로 구조와 결합 부위가 달라질 수 있습니다.")
    if mask:
        review.setdefault("warnings", []).append(f"결합 부위 추천에서 사용자 지정 {len(mask)}잔기를 제외합니다. 파트너 및 실제 접촉 평가는 유지합니다.")
    if cfg.get("input_review"):
        saved = cfg["input_review"]
        saved.update(target_sequence=cfg["target"]["sequence"], target_region=region, hotspot_excluded_positions=mask)
        if predicted or saved.get("preparation") == "fasta_prediction":
            saved["prediction_hotspots"] = cfg["target"]["hotspots"]
            review["prediction_hotspots"] = cfg["target"]["hotspots"]
        else:
            saved["hotspot_positions"] = [e["number"] - start + 1 for e in entries]
    if mapping:
        with mapping_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(mapping[0]))
            writer.writeheader()
            writer.writerows(mapping)
    (folder / "input_review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2))
    records = [("A", cfg["target"]["sequence"])] + [(chr(66+i), c["sequence"]) for i, c in enumerate(cfg["target"].get("aux_chains", []))]
    (folder / "target.fasta").write_text("".join(f">{name}\n{seq}\n" for name, seq in records))
    return cfg, review
