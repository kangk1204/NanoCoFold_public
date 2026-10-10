from __future__ import annotations

from pathlib import Path

import numpy as np
from Bio.PDB import Structure
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model

from .utils import pdb_io


def truncate_target(
    structure_path: str | Path,
    chain_id: str,
    hotspot_labels: list[str],
    out_path: str | Path,
    radius: float = 30.0,
    min_segment: int = 5,
    keep_hetatm: bool = False,
    keep_other_chains: bool = False,
) -> dict:
    """핫스팟 주변만 남긴 타깃 구조를 생성한다 (RFantibody 권장: 이차구조 보존, ≥10Å 마진).

    keep_other_chains: 다른 단백질 파트너 체인도 핫스팟 근처 잔기만 남겨 유지.
    단일 사슬만으로는 접힘이 불안정한 타깃(코일드코일 등)에서 구조 맥락을 보존한다.
    """
    structure = pdb_io.load_structure(structure_path)
    chain = pdb_io.get_chain(structure, chain_id)

    hotspot_coords = []
    for res in pdb_io.iter_residues(chain):
        if pdb_io.residue_label(res, chain_id) in set(hotspot_labels):
            coord = pdb_io.ca_coord(res)
            if coord is not None:
                hotspot_coords.append(coord)
    if not hotspot_coords:
        raise ValueError(f"지정한 핫스팟이 타깃 체인 {chain_id}에서 발견되지 않았습니다: {hotspot_labels}")
    hotspot_coords = np.vstack(hotspot_coords)

    residues = list(pdb_io.iter_residues(chain))
    keep_residues = []
    for res in residues:
        coord = pdb_io.ca_coord(res)
        if coord is None:
            continue
        if float(np.linalg.norm(hotspot_coords - coord, axis=1).min()) <= radius:
            keep_residues.append(res)

    segments = _split_segments(keep_residues)
    kept: list = []
    for segment in segments:
        if len(segment) >= min_segment:
            kept.extend(segment)

    kept_labels = {pdb_io.residue_label(res, chain_id) for res in kept}
    missing_hotspots = [label for label in hotspot_labels if label not in kept_labels]
    if not kept:
        raise ValueError(
            f"truncation 결과 잔기가 0개입니다 (radius={radius}, min_segment={min_segment}). "
            "radius를 키우거나 min_segment를 줄이세요."
        )
    if missing_hotspots:
        raise ValueError(
            f"truncation 후 지정 핫스팟이 사라졌습니다: {missing_hotspots}. "
            "radius를 키우거나 min_segment를 줄이세요 (빈 구조 저장 방지)."
        )

    new_structure = Structure.Structure("target_prepared")
    new_structure.add(Model(0))
    new_model = next(new_structure.get_models())
    new_chain = Chain(chain_id)
    new_model.add(new_chain)
    # Source polymer order is authoritative; PDB author numbers can go backwards.
    for res in kept:
        new_chain.add(res)

    if keep_hetatm:
        for het in chain:
            if pdb_io.is_standard_residue(het):
                continue
            coords = np.array([atom.get_coord() for atom in het.get_atoms()])
            if len(coords) and float(np.linalg.norm(coords[:, None, :] - hotspot_coords[None, :, :], axis=-1).min()) <= radius:
                new_chain.add(het)

    other_chain_residues = 0
    if keep_other_chains:
        model = next(structure.get_models())
        for other in model:
            if other.id == chain_id:
                continue
            other_chain = Chain(other.id)
            for res in pdb_io.iter_residues(other):
                coord = pdb_io.ca_coord(res)
                if coord is None:
                    continue
                if float(np.linalg.norm(hotspot_coords - coord, axis=1).min()) <= radius:
                    other_chain.add(res)
                    other_chain_residues += 1
            if len(other_chain) > 0:
                new_model.add(other_chain)

    out_path = Path(out_path)
    pdb_io.save_structure(new_structure, out_path, drop_hetatm=not keep_hetatm)

    ranges = [
        f"{segment[0].id[1]}-{segment[-1].id[1]}" for segment in segments if len(segment) >= min_segment
    ]
    return {
        "out_path": str(out_path),
        "original_residues": len(residues),
        "kept_residues": len(kept),
        "kept_other_chain_residues": other_chain_residues,
        "kept_ranges": ranges,
        "missing_hotspots": missing_hotspots,
    }


def _split_segments(residues: list, max_gap: int = 2) -> list[list]:
    segments: list[list] = []
    current: list = []
    previous_number: int | None = None
    for res in residues:
        number = int(res.id[1])
        if previous_number is not None and number - previous_number > max_gap:
            segments.append(current)
            current = []
        current.append(res)
        previous_number = number
    if current:
        segments.append(current)
    return segments
