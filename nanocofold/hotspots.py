from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .utils import pdb_io
from .utils.seq import KYTE_DOOLITTLE

MAX_ASA = {
    "A": 129.0, "R": 274.0, "N": 195.0, "D": 193.0, "C": 167.0, "Q": 225.0, "E": 223.0,
    "G": 104.0, "H": 224.0, "I": 197.0, "L": 201.0, "K": 236.0, "M": 224.0, "F": 240.0,
    "P": 159.0, "S": 155.0, "T": 172.0, "W": 285.0, "Y": 263.0, "V": 174.0,
}
GLYCAN_RESIDUES = {"NAG", "MAN", "BMA", "FUC", "GAL", "GLC", "SIA", "NDG"}


def parse_hotspot_spec(spec, default_chain: str = "A") -> list[dict]:
    """결합 부위 지정을 [{'chain','kind','number'}] 로 정규화한다.

    kind: 'pdb' | 'seq' | None(문맥에 따라 결정). 접두사로 명시 가능:
      "A:pdb142" (PDB 번호) · "A:seq135" (서열 인덱스) · "A142" · "142"
    """
    if spec in (None, "", "auto", []):
        return []
    items = spec if isinstance(spec, (list, tuple)) else [spec]
    entries: list[dict] = []
    for item in items:
        text = str(item).strip()
        for token in text.replace(";", ",").split(","):
            token = token.strip()
            if not token or token.lower() == "auto":
                continue
            if ":" in token:
                chain, rest = token.split(":", 1)
            else:
                chain, rest = _split_chain_prefix(token, default_chain)
            chain = chain.strip()
            if not chain or any(ch.isspace() for ch in chain):
                raise ValueError(f"결합 부위 체인이 유효하지 않습니다: '{token}'")
            match = re.match(r"(?i)^(pdb|seq)?[:_]?\s*(\d+)$", rest.strip())
            if not match:
                raise ValueError(f"결합 부위 형식을 해석할 수 없습니다: '{token}'")
            kind = (match.group(1) or "").lower() or None
            entries.append({"chain": chain, "kind": kind, "number": int(match.group(2))})
    return entries


def hotspot_spec_labels(entries: list[dict]) -> list[str]:
    """Render labels only; this compatibility helper does not resolve pdb/seq axes."""
    return [f"{entry['chain']}:{entry['number']}" for entry in entries]


def requests_automatic_hotspots(spec, default_chain: str = "A") -> bool:
    """Match target preparation: only an explicit [] disables empty/auto input."""
    return not (isinstance(spec, list) and not spec) and not parse_hotspot_spec(spec, default_chain)


def _split_chain_prefix(token: str, default_chain: str) -> tuple[str, str]:
    if token and token[0].isalpha() and len(token) > 1 and token[1:].strip().replace("-", "").isdigit():
        return token[0], token[1:]
    return default_chain, token


def hotspot_table(
    structure_path: str | Path,
    chain_id: str,
    conservation: list[float] | None = None,
    seq_positions: list[int] | None = None,
    confidence_kind: str = "plddt",
) -> pd.DataFrame:
    """잔기별 노출/소수성/신뢰도/보존도 테이블.

    label 은 구조의 PDB 잔기번호, seq_position 은 **입력 서열**의 1-based 인덱스다.
    seq_positions 를 주면 구조 잔기 순서 → 입력 서열 위치 매핑으로 사용한다(F09).
    conservation 은 입력 서열 위치 기준으로 매핑된다.
    """
    if confidence_kind not in {"plddt", "bfactor"}:
        raise ValueError("confidence_kind 는 'plddt' 또는 'bfactor' 이어야 합니다.")
    structure = pdb_io.load_structure(structure_path)
    chain = pdb_io.get_chain(structure, chain_id)
    sasa = pdb_io.compute_residue_sasa(structure)
    confidence = pdb_io.parse_plddt(structure, normalize=confidence_kind == "plddt")
    glycan_atoms = _glycan_atoms(structure)

    rows: list[dict] = []
    for order, res in enumerate(pdb_io.iter_residues(chain), start=1):
        label = pdb_io.residue_label(res, chain_id)
        if seq_positions is None:
            sequence_index = order
        elif order - 1 < len(seq_positions) and int(seq_positions[order - 1]) > 0:
            sequence_index = int(seq_positions[order - 1])
        else:
            sequence_index = None
        aa = pdb_io.AA3TO1.get(res.resname, "X")
        res_sasa = sasa.get(label, float("nan"))
        rel_sasa = res_sasa / MAX_ASA.get(aa, 200.0) if aa in MAX_ASA else float("nan")
        kd = KYTE_DOOLITTLE.get(aa, 0.0)
        cons = float("nan")
        if conservation and sequence_index is not None:
            index = sequence_index - 1
            if 0 <= index < len(conservation):
                cons = conservation[index]
        glycan_dist = _min_distance_to_atoms(pdb_io.ca_coord(res), glycan_atoms) if glycan_atoms.size else float("nan")
        rows.append({
            "label": label,
            "seq_position": sequence_index,
            "position": int(res.id[1]),
            "aa": aa,
            "relative_sasa": rel_sasa,
            "kyte_doolittle": kd,
            "conservation": cons,
            "plddt": confidence.get(label, float("nan")),
            "confidence_kind": confidence_kind,
            "dist_to_glycan": round(glycan_dist, 1) if not math.isnan(glycan_dist) else float("nan"),
        })
    return pd.DataFrame(rows)


def auto_hotspot_sets(
    table: pd.DataFrame,
    auto_cfg: dict,
    structure_path: str | Path,
    chain_id: str,
) -> list[dict]:
    exposure_weight = float(auto_cfg.get("exposure_weight", 1.0))
    hydro_weight = float(auto_cfg.get("hydrophobicity_weight", 0.6))
    cons_weight = float(auto_cfg.get("conservation_weight", 0.3))
    min_rel_sasa = float(auto_cfg.get("min_relative_sasa", 0.25))
    patch_radius = float(auto_cfg.get("patch_radius", 12.0))
    residues_range = auto_cfg.get("residues_per_set", [3, 6])
    num_sets = int(auto_cfg.get("num_sets", 3))
    exclude_terminal = int(auto_cfg.get("exclude_terminal", 2))

    working = table.copy().reset_index(drop=True)
    if "seq_position" in working.columns:
        positions = pd.to_numeric(working["seq_position"], errors="coerce")
        working = working.loc[positions.notna() & positions.gt(0)].copy().reset_index(drop=True)
    if working.empty:
        return []
    if exclude_terminal > 0 and len(working) > 2 * exclude_terminal:
        working = working.iloc[exclude_terminal : len(working) - exclude_terminal].reset_index(drop=True)
    excluded = auto_cfg.get("exclude_positions", [])
    if not isinstance(excluded, list) or any(type(p) is not int or p < 1 for p in excluded):
        raise ValueError("hotspots.auto.exclude_positions는 양의 서열 위치 정수 목록이어야 합니다.")
    if excluded:
        working = working.loc[~working["seq_position"].isin(excluded)].copy().reset_index(drop=True)
        if working.empty:
            return []

    surface = working[working["relative_sasa"] >= min_rel_sasa].copy()
    if surface.empty:
        surface = working.nlargest(min(6, len(working)), "relative_sasa").copy()

    surface["hydrophobicity_norm"] = surface["kyte_doolittle"].apply(lambda v: (v + 4.5) / 9.0)
    conservation_fill = float(surface["conservation"].mean()) if surface["conservation"].notna().any() else 0.5
    conservation_missing = int(surface["conservation"].isna().sum())
    conservation = surface["conservation"].fillna(conservation_fill)
    surface["conservation_norm"] = conservation.clip(0, 1)
    surface["score"] = (
        exposure_weight * surface["relative_sasa"].clip(0, 1)
        + hydro_weight * surface["hydrophobicity_norm"]
        + cons_weight * surface["conservation_norm"]
    )

    structure = pdb_io.load_structure(structure_path)
    chain = pdb_io.get_chain(structure, chain_id)
    coords = pdb_io.chain_coords_by_label(chain)

    # 반경 기반 patch 클러스터링: 상위 점수 잔기를 seed로 주변 표면 잔기를 묶는다.
    candidates = list(surface.sort_values("score", ascending=False, kind="stable")["label"])
    clusters: list[list[str]] = []
    for seed in candidates:
        if seed not in coords:
            continue
        if any(seed in cluster for cluster in clusters):
            continue
        members = [
            label for label in candidates
            if label in coords and _distance(coords[seed], coords[label]) <= patch_radius
        ]
        clusters.append(members)

    info = surface.set_index("label")
    seq_lookup = working.set_index("label")["seq_position"].to_dict()
    scored: list[dict] = []
    used: set[str] = set()
    for cluster in clusters:
        available = [label for label in cluster if label not in used]
        if not available:
            continue
        cluster_scores = info.loc[available, "score"].sort_values(ascending=False, kind="stable")
        min_res, max_res = int(residues_range[0]), int(residues_range[1])
        selected = list(cluster_scores.index[: min(max_res, len(cluster_scores))])
        if len(selected) < min_res:
            continue
        mean_sasa = float(info.loc[selected, "relative_sasa"].mean())
        if mean_sasa < min_rel_sasa * 1.2:
            continue
        hydrophobic_count = int((info.loc[selected, "kyte_doolittle"] > 0).sum())
        cluster_penalty = max(0.0, 1.0 - 0.05 * max(0, len(cluster) - 10))
        glycan_penalty = 1.0
        for label in selected:
            dist = info.loc[label, "dist_to_glycan"]
            if not math.isnan(dist) and dist < 8.0:
                glycan_penalty = 0.5
        cluster_score = float(cluster_scores.head(len(selected)).mean()) * cluster_penalty * glycan_penalty
        used.update(selected)
        scored.append({
            "method": "uncalibrated_surface_patch_heuristic_v1",
            "calibrated_binding_probability": False,
            "heuristic_parameters": {"exposure_weight": exposure_weight, "hydrophobicity_weight": hydro_weight,
                                     "conservation_weight": cons_weight, "min_relative_sasa": min_rel_sasa,
                                     "selected_sasa_multiplier": 1.2, "patch_radius": patch_radius,
                                     "cluster_size_penalty_start": 10, "cluster_size_penalty_per_residue": 0.05,
                                     "glycan_distance_angstrom": 8.0, "near_glycan_multiplier": 0.5,
                                     "conservation_imputation": "surface_population_observed_mean_or_0.5_if_all_missing",
                                     "conservation_imputation_value": conservation_fill,
                                     "conservation_imputed_surface_residues": conservation_missing,
                                     "conservation_population_size": len(surface),
                                     "residue_tie_break": "stable_recorded_input_residue_order",
                                     "set_tie_break": "stable_patch_seed_order_after_recorded_score_rounding"},
            "cluster_size_penalty": cluster_penalty,
            "cluster_size_penalty_scope": "all patch cluster members; mean score uses selected residues",
            "residues": selected,
            "residues_seq": [int(seq_lookup[label]) for label in selected],
            "score": round(cluster_score, 3),
            "cluster_size": len(cluster),
            "hydrophobic_count": hydrophobic_count,
            "glycan_risk": glycan_penalty < 1.0,
            "mean_relative_sasa": round(mean_sasa, 3),
            "mean_plddt": round(float(info.loc[selected, "plddt"].mean()), 1),
        })

    scored.sort(key=lambda item: item["score"], reverse=True)
    sets: list[dict] = []
    for index, entry in enumerate(scored[:num_sets], start=1):
        entry["set_id"] = f"auto{index}"
        sets.append(entry)
    return sets


def validate_hotspots(
    structure_path: str | Path,
    chain_id: str,
    labels: list[str],
    plddt_min: float = 70.0,
    min_relative_sasa: float = 0.15,
    enforce_plddt: bool = True,
    confidence_kind: str = "plddt",
) -> dict:
    table = hotspot_table(structure_path, chain_id, confidence_kind=confidence_kind)
    lookup = table.set_index("label")
    records: list[dict] = []
    warnings: list[str] = []
    for label in labels:
        if label not in lookup.index:
            warnings.append(f"{label}: 구조에 존재하지 않음")
            records.append({"label": label, "status": "missing"})
            continue
        row = lookup.loc[label]
        status = "ok"
        if not math.isnan(row["relative_sasa"]) and row["relative_sasa"] < min_relative_sasa:
            status = "buried"
            warnings.append(f"{label}: 매몰 잔기(relative SASA={row['relative_sasa']}). 노출 잔기로 교체 권장.")
        if confidence_kind == "plddt" and enforce_plddt and not math.isnan(row["plddt"]) and row["plddt"] < plddt_min:
            status = "low_plddt" if status == "ok" else status
            warnings.append(f"{label}: pLDDT={row['plddt']} (구조 신뢰도 낮음). 다른 에피토프 고려.")
        if not math.isnan(row["dist_to_glycan"]) and row["dist_to_glycan"] < 8.0:
            warnings.append(f"{label}: 글리칸과 {row['dist_to_glycan']} Å 거리. 당질 근처 에피토프는 결합 설계가 어려움.")
        records.append({
            "label": label,
            "status": status,
            "aa": row["aa"],
            "relative_sasa": row["relative_sasa"],
            "kyte_doolittle": row["kyte_doolittle"],
            "plddt": row["plddt"],
            "confidence_kind": row.get("confidence_kind", confidence_kind),
            "dist_to_glycan": row["dist_to_glycan"],
        })
    hydrophobic_count = sum(1 for rec in records if rec.get("kyte_doolittle", 0) and rec["kyte_doolittle"] > 0)
    if len(records) >= 3 and hydrophobic_count < 3:
        warnings.append(
            f"소수성 잔기가 {hydrophobic_count}개입니다. 자동 결합 부위의 소수성 패치 기준(≥3)은 "
            "탐색용 휴리스틱이며, 실제 결합이나 설계 성공을 보장하지 않습니다."
        )
    return {"records": records, "warnings": warnings, "hydrophobic_count": hydrophobic_count}


def _glycan_atoms(structure) -> np.ndarray:
    coords: list[np.ndarray] = []
    for model in structure.get_models():
        for chain in model:
            for res in chain:
                if res.id[0] != " " and res.resname.strip() in GLYCAN_RESIDUES:
                    for atom in res.get_atoms():
                        coords.append(atom.get_coord())
        break
    return np.vstack(coords) if coords else np.zeros((0, 3))


def _min_distance_to_atoms(coords: np.ndarray | None, atoms: np.ndarray) -> float:
    if coords is None or len(atoms) == 0:
        return float("nan")
    return float(np.linalg.norm(atoms - coords, axis=1).min())


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))
