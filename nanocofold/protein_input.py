"""Protein sequence provenance and bounded, unambiguous residue correspondence."""
from __future__ import annotations

import re
from pathlib import Path

from .utils.seq import AA1TO3, AA3TO1

AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
STANDARD_COMPONENTS = frozenset(AA1TO3.values())
MAX_PREDICTION_RESIDUES = 2000


def sequence_metadata(cif: dict | None, path: Path) -> dict:
    if cif is not None:
        values = lambda key: cif.get(key, []) if isinstance(cif.get(key, []), list) else [cif[key]] if key in cif else []
        sequences, origins = {}, {}
        for entity, sequence in zip(values("_entity_poly.entity_id"), values("_entity_poly.pdbx_seq_one_letter_code_can")):
            text = re.sub(r"\s+", "", str(sequence)).upper()
            if text and re.fullmatch("[A-Z]+", text):
                sequences[str(entity)] = text
                origins[str(entity)] = "mmcif_entity_poly"
        monomers = {}
        for entity, number, component in zip(values("_entity_poly_seq.entity_id"), values("_entity_poly_seq.num"), values("_entity_poly_seq.mon_id")):
            try:
                position = int(number)
            except ValueError:
                continue
            monomers.setdefault(str(entity), {}).setdefault(position, set()).add(str(component))
        for entity, positions in monomers.items():
            if entity not in sequences and set(positions) == set(range(1, len(positions) + 1)):
                components_by_position = [next(iter(positions[i])) if len(positions[i]) == 1 else "UNK"
                                          for i in range(1, len(positions) + 1)]
                sequences[entity] = "".join(AA3TO1[m] if m in STANDARD_COMPONENTS else "M" if m == "MSE" else "X"
                                            for m in components_by_position)
                origins[entity] = "mmcif_entity_poly_seq"
        components = {}
        labels = values("_atom_site.label_asym_id")
        insertions = values("_atom_site.pdbx_PDB_ins_code") or ["?"] * len(labels)
        for chain, position, author, insertion, component in zip(
            labels, values("_atom_site.label_seq_id"), values("_atom_site.auth_seq_id"),
            insertions, values("_atom_site.label_comp_id"),
        ):
            try:
                index = int(position)
                label = str(int(author)) + (str(insertion).strip() if insertion not in ("?", ".") else "")
            except ValueError:
                continue
            if index < 1:
                continue
            record = {"label_position": index, "original_label": label, "component": component}
            components.setdefault(str(chain), {})[(index, label, component)] = record
        return {"entity_sequences": sequences, "entity_sequence_sources": origins,
                "polymer_components": {chain: list(rows.values()) for chain, rows in components.items()}}
    sequences, counts, rows = {}, {}, {}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("SEQRES"):
            chain = line[11:12].strip() or " "
            try:
                total = int(line[13:17])
            except ValueError:
                continue
            counts.setdefault(chain, set()).add(total)
            rows.setdefault(chain, []).extend(line[19:70].split())
    for chain, monomers in rows.items():
        if counts[chain] == {len(monomers)}:
            # Parent sequence, never a coordinate/atom conversion. MSE is the
            # conventional selenomethionine component; other unknowns stay X.
            sequences[chain] = "".join(AA3TO1[m] if m in STANDARD_COMPONENTS else "M" if m == "MSE" else "X" for m in monomers)
    return {"pdb_sequences": sequences}


def unique_subsequence_positions(observed: str, full: str) -> dict[str, int]:
    """Only positions shared by the earliest and latest possible embeddings."""
    earliest, latest = [], []
    cursor = 0
    for aa in observed:
        found = full.find(aa, cursor)
        if found < 0:
            return {}
        earliest.append(found + 1)
        cursor = found + 1
    cursor = len(full)
    for aa in reversed(observed):
        found = full.rfind(aa, 0, cursor)
        if found < 0:
            return {}
        latest.append(found + 1)
        cursor = found
    latest.reverse()
    return {str(i): a for i, (a, b) in enumerate(zip(earliest, latest), 1) if a == b}


def annotate_chain(chain: dict, metadata: dict) -> None:
    full = metadata.get("entity_sequences", {}).get(chain.get("entity_id"))
    source = metadata.get("entity_sequence_sources", {}).get(chain.get("entity_id"), "not_recorded")
    if not full:
        full = metadata.get("pdb_sequences", {}).get(chain["original_id"])
        if full:
            source = "pdb_seqres"
    components = metadata.get("polymer_components", {}).get(chain["original_id"], [])
    modifications = [r for r in components if r["component"] not in STANDARD_COMPONENTS]
    observed_positions = {r["label_position"] for r in components}
    mapping, conflicts = {}, []
    if full:
        if source.startswith("mmcif_"):
            labels = metadata.get("residue_sequence_positions", {}).get(chain["original_id"], {})
            for row in chain["residues"]:
                index = labels.get(row["original_label"])
                if index is None:
                    continue
                if 1 <= index <= len(full) and (full[index - 1] == row["aa"] or full[index - 1] not in AA):
                    mapping[str(row["position"])] = index
                else:
                    conflicts.append(f"원래 잔기 {row['original_label']}와 전체 서열 위치 {index}의 아미노산 대응이 다릅니다.")
        else:
            mapping = unique_subsequence_positions(chain["sequence"], full)
            if any(aa not in AA for aa in full):
                mapping = {}  # Unknown components cannot justify a positional guess.
    missing = sorted(set(range(1, len(full) + 1)) - observed_positions) if full and source.startswith("mmcif_") else None
    unknown = [i for i, aa in enumerate(full or "", 1) if aa not in AA]
    chain["full_sequence"] = full
    chain["sequence_review"] = {
        "source": source, "full_length": len(full) if full else None,
        "observed_standard_length": len(chain["sequence"]),
        "observed_polymer_positions": sorted(observed_positions),
        "missing_positions": missing, "unknown_positions": unknown,
        "observed_to_full": mapping, "modifications": modifications,
        "polymer_components": components,
        "conflicts": conflicts,
        "statement": "전체 서열과 관측 좌표는 구분합니다. 수정 잔기 좌표를 표준 잔기로 변환하지 않았습니다.",
    }
    if full and len(full) != len(chain["sequence"]):
        chain["warnings"].append(
            f"체인 {chain['original_id']}: 관측된 표준 잔기 {len(chain['sequence'])}개, "
            f"기록된 전체 서열 {len(full)}개입니다. 말단·내부 결손과 수정 잔기 여부를 확인하세요."
        )


def resolve_template(template: str, sequence: str) -> dict[int, tuple[int, int]]:
    """Map every template token to a unique 1-based sequence span.

    Standard residues are fixed. Unknown tokens may represent one or more
    standard residues (e.g. a multi-residue chromophore). Capped path counts
    reject ambiguity in O(n*m) time with byte-sized rows, never regex backtracking.
    """
    n, m = len(template), len(sequence)
    if not sequence or set(sequence) - AA:
        raise ValueError("X 등 불명확한 잔기를 포함하지 않는 표준 아미노산 전체 서열을 확인하세요.")
    if max(n, m) > MAX_PREDICTION_RESIDUES:
        raise ValueError(f"전체 서열 예측 입력은 합계 {MAX_PREDICTION_RESIDUES}잔기까지 지원합니다.")
    if template == sequence:
        return {i: (i, i) for i in range(1, n + 1)}
    if set(template) <= AA:
        raise ValueError("입력한 전체 서열이 원본에 기록된 서열과 다릅니다. 올바른 construct 서열·구조 대응을 확인하세요.")
    rows = [bytearray(m + 1) for _ in range(n + 1)]
    rows[n][m] = 1
    for i in range(n - 1, -1, -1):
        if template[i] in AA:
            for j in range(m):
                if template[i] == sequence[j]:
                    rows[i][j] = rows[i + 1][j + 1]
        else:
            count = 0
            for j in range(m - 1, -1, -1):
                count = min(2, count + rows[i + 1][j + 1])
                rows[i][j] = count
    if rows[0][0] != 1:
        raise ValueError("원본 서열과 입력 서열의 대응이 없거나 여러 가지입니다. 불명확한 서열을 임의 정렬하지 않았습니다.")
    spans, j = {}, 0
    for i, aa in enumerate(template):
        end = j + 1 if aa in AA else next(k for k in range(j + 1, m + 1) if rows[i + 1][k])
        spans[i + 1] = (j + 1, end)
        j = end
    return spans


def prediction_chain(chain: dict, provided: str | None = None) -> dict:
    if chain.get("polymer_type", "").lower() not in {"", "polypeptide(l)"}:
        raise ValueError("선택한 polymer 유형은 현재 표준 L-아미노산 예측 모델에서 지원하지 않습니다.")
    review = chain.get("sequence_review", {})
    if review.get("conflicts"):
        raise ValueError("전체 서열·좌표 대응을 먼저 해결하세요: " + "; ".join(review["conflicts"]))
    template = chain.get("full_sequence")
    sequence = provided or template
    if not sequence:
        raise ValueError(f"체인 {chain['original_id']}: 전체 서열 정보가 없습니다. 확인한 전체 FASTA를 입력하세요.")
    if len(sequence) > MAX_PREDICTION_RESIDUES or set(sequence) - AA:
        raise ValueError(f"체인 {chain['original_id']}: X 등 불명확한 잔기를 확인한 전체 서열이 필요합니다.")
    mapping, spans = {}, {}
    if template:
        spans = resolve_template(template, sequence)
        for observed, token in review.get("observed_to_full", {}).items():
            start, end = spans[int(token)]
            if start == end:
                mapping[observed] = start
        if review.get("source") == "pdb_seqres":
            mapping = unique_subsequence_positions(chain["sequence"], sequence)
    else:
        mapping = unique_subsequence_positions(chain["sequence"], sequence)
    # A known coordinate residue cannot disappear or change order in the full
    # protein. Repeats may have no unique mapping; auto-hotspot prediction can
    # still use the reviewed sequence without inventing positional assignments.
    cursor = 0
    for aa in chain["sequence"]:
        cursor = sequence.find(aa, cursor) + 1
        if not cursor:
            raise ValueError("제공한 전체 서열과 관측된 잔기의 대응을 확인하세요.")
    return {"id": chain["id"], "name": chain["name"], "original_id": chain["original_id"],
            "author_chain": chain["author_chain"], "sequence": sequence,
            "sequence_origin": "user_reviewed" if provided else review.get("source"),
            "observed_to_full": mapping, "template_spans": spans, "sequence_review": review}
