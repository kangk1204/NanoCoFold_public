from __future__ import annotations

import json
import hashlib
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import cfg_get, resolve_path
from .utils import pdb_io, shell
from .utils.metric_provenance import PDOCKQ_METHOD, PDOCKQ2_METHOD


@dataclass
class PredictionFiles:
    cif: Path
    scores_npz: Path | None = None
    confidence_json: Path | None = None
    pae_npz: Path | None = None
    source: str = "generic"
    sample_index: int | None = None
    retained_sample_count: int = 1
    sample_selection: str = "explicit_structure_file"


@dataclass
class ScoringContext:
    """Read-only per-model inputs; never shared between prediction files."""
    pred: PredictionFiles
    structure: object
    labels: list[str]
    index_map: dict[str, int]
    pae: np.ndarray | None
    confidence: dict
    plddt: dict[str, float]
    ca_plddt: dict[str, float]
    cb_plddt: dict[str, float]
    chain_sasa: dict[str, float] = field(default_factory=dict)


def prepare_scoring_context(pred: PredictionFiles, *, structure=None) -> ScoringContext:
    structure = pdb_io.load_structure(pred.cif) if structure is None else structure
    labels, index_map = build_index_map(structure)
    pae = load_pae(pred)
    if pae is not None:
        validate_pae(pae, len(labels))
    maps = [pdb_io.parse_plddt(structure),
            pdb_io.parse_atom_plddt(structure, atom_mode="ca"),
            pdb_io.parse_atom_plddt(structure, atom_mode="cb")]
    maps = [values if any(value > 0 for value in values.values()) else {} for values in maps]
    return ScoringContext(pred, structure, labels, index_map, pae, load_confidence(pred), *maps)


def find_prediction_files(path: str | Path, name: str | None = None, *, model_index: int | None = None) -> PredictionFiles:
    """Chai-1 / Boltz-2 / 일반 cif 출력에서 구조·점수 파일을 찾는다."""
    path = Path(path)
    if model_index is not None:
        if isinstance(model_index, (bool, np.bool_)) or not isinstance(model_index, (int, np.integer)) or model_index < 0:
            raise ValueError("모델 번호는 0 이상의 정수여야 합니다.")
        model_index = int(model_index)
    if path.is_file() and path.suffix.lower() in {".cif", ".pdb"}:
        match = re.search(r"(?:pred\.model_idx_|_model_)(\d+)$", path.stem)
        sample_index = int(match.group(1)) if match else None
        if model_index is not None and sample_index != model_index:
            raise ValueError("요청한 모델 번호와 구조 파일의 모델 번호가 일치하지 않거나 확인할 수 없습니다. "
                             "해당 모델 파일을 지정하거나 모델 번호 설정을 제거하세요.")
        return PredictionFiles(path, source="generic", sample_index=sample_index,
                               sample_selection="explicit_model_index" if model_index is not None else "explicit_structure_file")
    if not path.is_dir():
        raise FileNotFoundError(path)

    cif_candidates = sorted(path.glob("pred.model_idx_*.cif")) or sorted(path.glob("*_model_*.cif")) or sorted(path.glob("*.cif"))
    if not cif_candidates:
        cif_candidates = sorted(path.glob("*.pdb"))
    if not cif_candidates:
        raise FileNotFoundError(f"cif/pdb 파일을 찾을 수 없습니다: {path}")
    retained_sample_count = len(cif_candidates)
    if model_index is not None:
        cif_candidates = [p for p in cif_candidates if p.stem.endswith(f"_model_{model_index}") or p.stem == f"pred.model_idx_{model_index}"]
        if not cif_candidates:
            raise FileNotFoundError(f"model {model_index} 출력이 없습니다: {path}")
    cif = cif_candidates[0]
    sample_index = int(cif.stem.rsplit("_", 1)[-1]) if cif.stem.rsplit("_", 1)[-1].isdigit() else None
    sample_selection = "explicit_model_index" if model_index is not None else "first_retained_model"

    if cif.name.startswith("pred.model_idx_"):
        idx = cif.stem.split("_")[-1]
        npz = path / f"scores.model_idx_{idx}.npz"
        extra = path / f"scores_extra_idx_{idx}.npz"
        if not extra.exists() and str(idx) == "0":
            extra = path / "scores_extra.npz"
        return PredictionFiles(
            cif,
            scores_npz=npz if npz.exists() else None,
            pae_npz=extra if extra.exists() else None,
            source="chai1", sample_index=sample_index, retained_sample_count=retained_sample_count,
            sample_selection=sample_selection,
        )

    confidence = path / f"confidence_{cif.stem}.json"
    pae = path / f"pae_{cif.stem}.npz"
    return PredictionFiles(
        cif,
        confidence_json=confidence if confidence.exists() else None,
        pae_npz=pae if pae.exists() else None,
        source="opendde" if cif.name.startswith("opendde_model_") else "colabfold" if cif.name.startswith("colabfold_model_") else "boltz2",
        sample_index=sample_index, retained_sample_count=retained_sample_count, sample_selection=sample_selection,
    )


def build_index_map(structure) -> tuple[list[str], dict[str, int]]:
    """표준 잔기 토큰 순서(체인 등장 순) → PAE 행렬 인덱스 매핑."""
    labels: list[str] = []
    for model in structure.get_models():
        for chain in model:
            for res in pdb_io.iter_residues(chain):
                labels.append(pdb_io.residue_label(res, chain.id))
        break
    return labels, {label: i for i, label in enumerate(labels)}


def load_pae(pred: PredictionFiles) -> np.ndarray | None:
    for path in (pred.scores_npz, pred.pae_npz):
        if path and path.exists():
            with np.load(path, allow_pickle=False) as data:
                for key in ("pae", "PAE"):
                    if key in data:
                        array = np.asarray(data[key], dtype=np.float64)
                        validate_pae(array)
                        return array
    return None


def validate_pae(pae: np.ndarray, residue_count: int | None = None) -> None:
    if (pae.ndim != 2 or pae.shape[0] == 0 or pae.shape[0] != pae.shape[1]
            or not np.isfinite(pae).all() or np.any(pae < 0)):
        raise ValueError("PAE 행렬은 비음수 유한값의 정방 행렬이어야 합니다.")
    if residue_count is not None and pae.shape != (residue_count, residue_count):
        raise ValueError(f"PAE 크기 {pae.shape}와 구조 잔기 수 {residue_count}가 일치하지 않습니다.")


def load_confidence(pred: PredictionFiles) -> dict:
    if pred.confidence_json and pred.confidence_json.exists():
        return json.loads(pred.confidence_json.read_text(encoding="utf-8"))
    if pred.scores_npz and pred.scores_npz.exists():
        out: dict = {}
        with np.load(pred.scores_npz, allow_pickle=False) as data:
            for key in ("iptm", "ptm", "aggregate_score", "confidence_score", "plddt"):
                if key in data:
                    value = data[key]
                    out[key] = float(np.mean(value)) if np.ndim(value) else float(value)
            for key in ("per_chain_ptm", "per_chain_pair_iptm", "has_inter_chain_clashes", "chain_chain_clashes"):
                if key in data:
                    out[key] = data[key].tolist()
        if pred.source == "chai1":
            metadata, reason = chai_chain_axis(pred)
            if metadata is not None:
                out["chain_axis"] = metadata
            else:
                out["chain_axis_unavailable"] = reason
        return out
    return {}


def chai_chain_axis(pred: PredictionFiles) -> tuple[dict | None, str | None]:
    """Accept only explicitly recorded ranking axes bound to the exact raw CIF."""
    index = pred.cif.stem.split("_")[-1]
    path = pred.cif.parent / f"chain_axis_idx_{index}.json"
    try:
        if path.is_symlink():
            raise ValueError("Chai chain-axis receipt must be a local retained file")
        metadata = json.loads(path.read_text())
        asym_ids, chain_ids = metadata["asym_ids"], metadata["chain_ids"]
        if (metadata.get("schema_version") != 1 or type(metadata.get("model_index")) is not int
                or metadata.get("model_index") != int(index)
                or metadata.get("source") != "SampleRanking.asym_ids"
                or metadata.get("chain_mapper") != "chai_lab.data.io.cif_utils.get_chain_letter"
                or metadata.get("pair_axes") != ["query_chain", "key_chain"]
                or not isinstance(asym_ids, list) or not isinstance(chain_ids, list) or not asym_ids
                or len(asym_ids) != len(chain_ids) or len(set(asym_ids)) != len(asym_ids)
                or len(set(chain_ids)) != len(chain_ids) or any(type(i) is not int or i <= 0 for i in asym_ids)
                or any(not isinstance(c, str) or not c for c in chain_ids)
                or not isinstance(metadata.get("chain_mapper_sha256"), str)
                or len(metadata["chain_mapper_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in metadata["chain_mapper_sha256"])
                or not isinstance(metadata.get("chai_version"), str) or not metadata["chai_version"]
                or metadata.get("structure_sha256") != hashlib.sha256(pred.cif.read_bytes()).hexdigest()):
            raise ValueError("Chai chain-axis receipt differs from model or contains ambiguous identities")
        return metadata, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"Chai native chain-axis mapping unavailable: {exc}"


def _d0_scalar(n_residues: int) -> float:
    """공식 calc_d0: L>27 이면 공식, 아니면 1.0, 최솟값 1.0 (chain-level d0chn/d0dom)."""
    length = int(n_residues)
    if length > 27:
        d0 = 1.24 * (length - 15) ** (1.0 / 3.0) - 1.8
    else:
        d0 = 1.0
    return max(1.0, d0)


def _d0_residue(n_valid: int) -> float:
    """공식 calc_d0_array: L=max(26,L), 최솟값 1.0 (residue-wise d0res = ipSAE 본값)."""
    length = max(26.0, float(n_valid))
    return max(1.0, 1.24 * (length - 15) ** (1.0 / 3.0) - 1.8)


def _ipsae_core(pae: np.ndarray, idx_aligned: list[int], idx_scored: list[int], d0, pae_cutoff: float) -> float:
    """공식과 동일: 각 aligned 잔기 i에 대해 PAE<cutoff 인 j만 평균낸다."""
    if not idx_aligned or not idx_scored:
        return 0.0
    sub = pae[np.ix_(idx_aligned, idx_scored)]
    valid_mask = sub < pae_cutoff
    if not valid_mask.any():
        return 0.0
    best = 0.0
    for row, mask in zip(sub, valid_mask):
        if not mask.any():
            continue
        d0_value = d0 if np.isscalar(d0) else float(d0)
        values = row[mask]
        score = float(np.mean(1.0 / (1.0 + (values / d0_value) ** 2)))
        best = max(best, score)
    return best


def ipsae_from_pae(
    pae: np.ndarray,
    idx_aligned: list[int],
    idx_scored: list[int],
    pae_cutoff: float = 10.0,
) -> float:
    """ipSAE (Dunbrack v4) — residue-wise d0res. 각 행의 유효 잔기 수로 d0를 계산한다."""
    if not idx_aligned or not idx_scored:
        return 0.0
    sub = pae[np.ix_(idx_aligned, idx_scored)]
    counts = (sub < pae_cutoff).sum(axis=1)
    best = 0.0
    for row, count in zip(sub, counts):
        if count == 0:
            continue
        d0 = _d0_residue(int(count))
        values = row[row < pae_cutoff]
        best = max(best, float(np.mean(1.0 / (1.0 + (values / d0) ** 2))))
    # Thresholds use the computed value. Display rounding must not promote a
    # just-below-threshold model into the eligible pool.
    return best


def ipsae_d0chn_from_pae(
    pae: np.ndarray,
    idx_aligned: list[int],
    idx_scored: list[int],
    pae_cutoff: float = 10.0,
) -> float:
    """ipSAE_d0chn — d0는 두 체인 잔기 수 합(n0chn)으로 고정한다."""
    if not idx_aligned or not idx_scored:
        return 0.0
    d0 = _d0_scalar(len(idx_aligned) + len(idx_scored))
    return round(_ipsae_core(pae, idx_aligned, idx_scored, d0, pae_cutoff), 4)


def ipsae_d0dom_from_pae(
    pae: np.ndarray,
    idx_aligned: list[int],
    idx_scored: list[int],
    pae_cutoff: float = 10.0,
) -> float:
    """ipSAE_d0dom with full residue-token identity, including insertions.

    The pinned upstream v4 auxiliary implementation deduplicates PDB integer
    residue numbers or mmCIF label_seq_id without insertion codes. A PDB
    43/43A pair is merged there; a standard mmCIF pair with distinct
    label_seq_id values is not. Each verified PAE axis token here remains a
    distinct residue; the main d0res/ipSAE is unaffected by this auxiliary
    d0dom convention. No 3D-distance filter is applied.
    """
    if not idx_aligned or not idx_scored:
        return 0.0
    sub = pae[np.ix_(idx_aligned, idx_scored)]
    mask = sub < pae_cutoff
    n0dom = int(mask.any(axis=1).sum() + mask.any(axis=0).sum())
    d0 = _d0_scalar(n0dom)
    return round(_ipsae_core(pae, idx_aligned, idx_scored, d0, pae_cutoff), 4)


def interface_pae(pae: np.ndarray, index_map: dict[str, int], contacts: list[dict], cutoff: float = 10.0) -> float:
    """Mean bidirectional PAE over geometric contacts, including high-PAE pairs.

    ``cutoff`` is retained for legacy call compatibility and is not a PAE filter.
    Removing high PAE pairs here would conceal low-confidence interfaces.
    """
    values: list[float] = []
    for contact in contacts:
        i = index_map.get(contact["a"])
        j = index_map.get(contact["b"])
        if i is None or j is None:
            continue
        values.append(float(pae[i, j]))
        values.append(float(pae[j, i]))
    if not values:
        return float("nan")
    return round(float(np.mean(values)), 3)


def interface_pae_summary(pae, index_map, contacts) -> dict:
    """Directional and pooled PAE (Å) for the existing heavy-atom contact set.

    ``ab`` means matrix row target / column binder; PAE is asymmetric.
    Percentiles use NumPy's linear interpolation and do not discard high PAE.
    """
    keys = ("interface_pae_ab", "interface_pae_ba", "interface_pae_median", "interface_pae_p90")
    if pae is None:
        return dict.fromkeys(keys, float("nan"))
    pairs = [(index_map[c["a"]], index_map[c["b"]]) for c in contacts
             if c["a"] in index_map and c["b"] in index_map]
    if not pairs:
        return dict.fromkeys(keys, float("nan"))
    ab = np.array([pae[i, j] for i, j in pairs], dtype=float)
    ba = np.array([pae[j, i] for i, j in pairs], dtype=float)
    pooled = np.concatenate((ab, ba))
    return dict(zip(keys, [round(float(v), 3) for v in
                          (ab.mean(), ba.mean(), np.median(pooled), np.percentile(pooled, 90))]))


def pdockq2_from_structure(structure, target_chain, binder_chain, plddt, pae, index_map,
                          cutoff=8.0) -> dict:
    """Zhu et al. 2023 CA8/contact-weighted formula for the selected chain pair.

    https://gitlab.com/ElofssonLab/afm-benchmark/-/blob/main/src/pdockq2.py
    ``plddt`` must contain CA-atom confidence (not the residue atom mean).
    Each directional score uses that chain's contact-pair-weighted pLDDT
    (0..100) and mean(1/(1+(directional PAE/10Å)^2)). The conservative
    pair summary is our conservative minimum, without refitting or clipping.
    The author script aggregates each chain against all contacting partners;
    this selected-pair result excludes auxiliary chains. It is not the
    CB/unique-interface variant in the pinned ipSAE auxiliary implementation.
    Calibration is AlphaFold-Multimer; other engines are exploratory.
    Missing confidence or absent interface is unassessed, not a zero score.
    """
    result = {"pdockq2": float("nan"), "pdockq2_target": float("nan"),
              "pdockq2_binder": float("nan"), "pdockq2_n_contacts": 0,
              "pdockq2_method": PDOCKQ2_METHOD,
              "pdockq2_scope": "selected_target_binder_pair",
              "pdockq2_summary": "minimum_of_directional_pair_scores; NanoCoFold summary",
              "pdockq2_status": "missing_pae" if pae is None else "no_interface"}
    residues = [list(pdb_io.iter_residues(pdb_io.get_chain(structure, c)))
                for c in (target_chain, binder_chain)]
    atoms = [[r for r in rs if "CA" in r] for rs in residues]
    if not atoms[0] or not atoms[1]:
        return result
    coordinates = [np.asarray([r["CA"].get_coord() for r in rs], dtype=float) for rs in atoms]
    pairs = np.argwhere(np.linalg.norm(coordinates[0][:, None, :] - coordinates[1][None, :, :], axis=-1) <= cutoff)
    result["pdockq2_n_contacts"] = int(len(pairs))
    if not len(pairs) or pae is None:
        return result
    labels = [[pdb_io.residue_label(r, c) for r in rs]
              for c, rs in zip((target_chain, binder_chain), atoms)]
    used = {labels[0][i] for i, _ in pairs} | {labels[1][j] for _, j in pairs}
    if any(l not in plddt or not math.isfinite(plddt[l]) or not 0 <= plddt[l] <= 100 for l in used):
        result["pdockq2_status"] = "missing_plddt"
        return result
    if any(l not in index_map for l in used):
        raise ValueError("pDockQ2 contact residues are absent from the verified PAE axis")
    scores = []
    for direction in (0, 1):
        values = [pae[index_map[labels[direction][p[direction]]],
                      index_map[labels[1-direction][p[1-direction]]]] for p in pairs]
        average_plddt = np.mean([plddt[labels[direction][p[direction]]] for p in pairs])
        x = float(average_plddt * np.mean(1 / (1 + (np.asarray(values) / 10.0) ** 2)))
        scores.append(1.31034849 / (1 + math.exp(-0.0747157696 * (x - 84.7326239))) + 0.00501886443)
    result.update(pdockq2=round(min(scores), 4), pdockq2_target=round(scores[0], 4),
                  pdockq2_binder=round(scores[1], 4), pdockq2_status="evaluated")
    return result


def pdockq_from_structure(
    structure,
    target_chain: str,
    binder_chain: str,
    plddt: dict[str, float],
    cutoff: float = 8.0,
) -> float:
    """Bryant et al. 2022 pDockQ 원 공식과 저자 CB confidence 입력.

    x = ⟨pLDDT⟩_interface(0-100, CB 기준) × log10(접촉 수)
    pDockQ = 0.724 / (1 + exp(-0.052 (x - 152.611))) + 0.018
    ``plddt`` is the CB-atom confidence map (CA for Gly only).
    This compatibility function preserves the author's raw 0 for no CB8
    interface. Use ``pdockq_metrics_from_structure`` for assessment status
    and a NaN canonical value when the interface cannot be assessed.
    https://gitlab.com/ElofssonLab/FoldDock/-/blob/main/src/pdockq.py
    """
    return pdockq_metrics_from_structure(
        structure, target_chain, binder_chain, plddt, cutoff,
    )["pdockq_raw_author_value"]


def pdockq_metrics_from_structure(
    structure,
    target_chain: str,
    binder_chain: str,
    plddt: dict[str, float],
    cutoff: float = 8.0,
) -> dict:
    """Author pDockQ with explicit availability and unchanged raw value.

    No interface or missing contact atoms is unassessed in the canonical
    ``pdockq`` column. ``pdockq_raw_author_value`` retains the author's zero
    convention for callers requiring raw-code parity, without turning that
    placeholder into a measured low-confidence interface.
    """
    result = {
        "pdockq": float("nan"), "pdockq_raw_author_value": 0.0,
        "pdockq_method": PDOCKQ_METHOD, "pdockq_status": "missing_contact_atoms",
        "pdockq_n_contacts": 0,
    }

    def cb_coords(chain):
        labels, coords = [], []
        for res in pdb_io.iter_residues(chain):
            atom = pdb_io.metric_confidence_atom(res, "cb")
            if atom is None:
                continue
            labels.append(pdb_io.residue_label(res, chain.id))
            coords.append(atom.get_coord().astype(float))
        return labels, np.asarray(coords)

    labels_a, coords_a = cb_coords(pdb_io.get_chain(structure, target_chain))
    labels_b, coords_b = cb_coords(pdb_io.get_chain(structure, binder_chain))
    if len(coords_a) == 0 or len(coords_b) == 0:
        return result
    dists = np.linalg.norm(coords_a[:, None, :] - coords_b[None, :, :], axis=-1)
    pairs = np.argwhere(dists <= cutoff)
    result.update(pdockq_n_contacts=int(pairs.shape[0]), pdockq_status="no_interface")
    if pairs.shape[0] < 1:
        return result
    used = [labels_a[i] for i in np.unique(pairs[:, 0])] + [labels_b[j] for j in np.unique(pairs[:, 1])]
    if any(label not in plddt or not math.isfinite(plddt[label])
           or not 0 <= plddt[label] <= 100 for label in used):
        # 접촉 잔기 하나라도 confidence가 없으면 부분 평균을 만들지 않는다.
        result.update(pdockq_raw_author_value=float("nan"), pdockq_status="missing_plddt")
        return result
    values = [plddt[label] for label in used]
    avg_plddt = float(np.mean(values))
    x = avg_plddt * math.log10(int(pairs.shape[0]))
    score = round(0.724 / (1.0 + math.exp(-0.052 * (x - 152.611))) + 0.018, 4)
    result.update(pdockq=score, pdockq_raw_author_value=score, pdockq_status="evaluated")
    return result


def buried_sasa(structure, chain_a: str, chain_b: str, n_contacts: int = 0,
                *, chain_sasa_cache: dict[str, float] | None = None) -> dict:
    """타깃–바인더 두 체인만의 매몰 면적 (보조 체인은 양쪽 항에서 동일하게 제외, F21).

    정의: SASA(A) + SASA(B) - SASA(A+B).
    """
    if n_contacts == 0:
        # The heavy-atom cutoff does not imply non-overlapping solvent spheres.
        # Skip the expensive calculation without fabricating a zero buried area.
        return {"buried_sasa": float("nan"), "buried_sasa_per_contact": float("nan"),
                "buried_sasa_status": "not_assessed_no_contacts", "buried_sasa_assessed": False}
    def _total(structure_obj) -> float:
        return sum(pdb_io.compute_residue_sasa(structure_obj).values())

    model = next(structure.get_models())
    other_chains = [chain.id for chain in model if chain.id not in (chain_a, chain_b)]

    complex_ab = structure.copy()
    for chain_id in other_chains:
        complex_ab[0].detach_child(chain_id)
    total_ab = _total(complex_ab)

    copy_a = complex_ab.copy()
    copy_a[0].detach_child(chain_b)
    cache = chain_sasa_cache if chain_sasa_cache is not None else {}
    if chain_a not in cache:
        cache[chain_a] = _total(copy_a)
    total_a = cache[chain_a]

    copy_b = complex_ab.copy()
    copy_b[0].detach_child(chain_a)
    if chain_b not in cache:
        cache[chain_b] = _total(copy_b)
    total_b = cache[chain_b]

    buried = max(0.0, total_a + total_b - total_ab)
    return {
        "buried_sasa": round(buried, 1),
        "buried_sasa_per_contact": round(buried / max(1, n_contacts), 2),
        "buried_sasa_status": "evaluated", "buried_sasa_assessed": True,
    }


def _normalize_hotspot_requests(hotspots, target_chain: str) -> list[str]:
    """요청 결합 부위를 필터링 없이 라벨로 정규화한다(G06)."""
    labels: list[str] = []
    for item in hotspots or []:
        text = str(item).strip()
        if not text:
            continue
        labels.append(text if ":" in text else f"{target_chain}:{text}")
    return list(dict.fromkeys(labels))


def resolve_hotspot_labels(hotspots, target_chain: str, index_map: dict[str, int]) -> list[str]:
    labels: list[str] = []
    for item in hotspots:
        text = str(item).strip()
        if not text:
            continue
        if ":" in text:
            labels.append(text)
        else:
            labels.append(f"{target_chain}:{text}")
    return [label for label in labels if label in index_map]


def _finite_confidence(value) -> float:
    if isinstance(value, (bool, np.bool_)) or value is None:
        return float("nan")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return float("nan")
    return numeric if np.isfinite(numeric) and 0 <= numeric <= 1 else float("nan")


def target_binder_iptm(confidence: dict, target_chain: str, binder_chain: str,
                       chain_ids: list[str], *, source: str = "generic") -> tuple[float, str, str | None]:
    """Use pair confidence only with the engine's established chain-axis mapping."""
    values = None
    pair_source = "unavailable"
    matrix = confidence.get("pair_chains_iptm")
    if isinstance(matrix, dict):
        forward, reverse = matrix.get(target_chain), matrix.get(binder_chain)
        if isinstance(forward, dict) and isinstance(reverse, dict):
            values = forward.get(binder_chain), reverse.get(target_chain)
            pair_source = "pair_chains_iptm_chain_labels_directional_minimum"
        elif source == "boltz2" and chain_ids == [chr(65 + i) for i in range(len(chain_ids))]:
            # Boltz confidence JSON uses zero-based asym indices; the adapter
            # validates output chain/sequence order against this canonical input.
            a, b = str(chain_ids.index(target_chain)), str(chain_ids.index(binder_chain))
            forward, reverse = matrix.get(a), matrix.get(b)
            if isinstance(forward, dict) and isinstance(reverse, dict):
                values = forward.get(b), reverse.get(a)
                pair_source = "boltz2_zero_based_asym_pair_directional_minimum"
    axis = confidence.get("chain_axis")
    ids = axis.get("chain_ids") if isinstance(axis, dict) else axis
    if values is None and isinstance(ids, list) and all(isinstance(i, str) for i in ids):
        if len(ids) == len(chain_ids) and len(set(ids)) == len(ids) and set(ids) == set(chain_ids):
            key = "per_chain_pair_iptm" if isinstance(axis, dict) else "chain_pair_iptm"
            if isinstance(axis, dict) or (source == "opendde" and confidence.get("engine") == "opendde"):
                try:
                    pairs = np.asarray(confidence.get(key), dtype=float)
                    if pairs.ndim == 3 and pairs.shape[0] == 1:
                        pairs = pairs[0]
                    if pairs.shape == (len(ids), len(ids)):
                        a, b = ids.index(target_chain), ids.index(binder_chain)
                        values = pairs[a, b], pairs[b, a]
                        pair_source = ("chai1_native_axis_pair_directional_minimum" if isinstance(axis, dict)
                                       else "opendde_verified_axis_pair_directional_minimum")
                except (ValueError, TypeError):
                    pass
    if values is not None:
        forward, reverse = map(_finite_confidence, values)
        if np.isfinite(forward) and np.isfinite(reverse):
            return min(forward, reverse), pair_source, None
    if len(chain_ids) == 2 and set(chain_ids) == {target_chain, binder_chain}:
        value = _finite_confidence(confidence.get("iptm", confidence.get("protein_iptm")))
        return value, "native_global_iptm_two_chain_complex", None if np.isfinite(value) else "native ipTM 미제공 또는 유효하지 않음"
    return float("nan"), "unavailable", "다중 체인의 표적–결합자 쌍 ipTM 또는 검증된 native 체인 축이 제공되지 않았습니다."


def iptm_availability_warnings(cfg: dict, *, target_chain_count: int | None = None) -> list[str]:
    """Explain a known native-output limitation before VHH inference, without changing gates."""
    if cfg_get(cfg, "run.mode") == "complex":
        return []
    if target_chain_count is None:
        auxiliary = cfg_get(cfg, "target.aux_chains", []) or []
        target_chain_count = 1 + len(auxiliary) if isinstance(auxiliary, list) else None
    if isinstance(target_chain_count, (bool, np.bool_)) or not isinstance(target_chain_count, (int, np.integer)) or target_chain_count < 2:
        return []
    engines = [str(cfg_get(cfg, "screen.engine", "colabfold")).lower()]
    if cfg_get(cfg, "screen.comparison.enabled", False):
        engines = [str(engine).lower() for engine in (cfg_get(cfg, "screen.comparison.engines", []) or [])]
    if "colabfold" not in engines:
        return []
    return ["ColabFold의 3체인 이상 예측은 native 표적–VHH 쌍 ipTM을 제공하지 않습니다. "
            "ipTM을 미평가로 기록하고 ipTM 게이트 및 기록 적격을 통과시키지 않습니다. "
            "전체 복합체 ipTM(complex_iptm)을 쌍 값으로 대체하지 않습니다. "
            "쌍 지표를 제공하는 엔진의 독립 결과와 함께 확인하세요."]


def iptm_missing_warnings(rows: list[dict]) -> list[str]:
    """Surface saved unavailable reasons, including report regeneration of older runs."""
    reasons: dict[str, int] = {}
    for row in rows:
        reason = row.get("iptm_unavailable_reason")
        if isinstance(reason, str) and reason.strip() and not np.isfinite(_finite_confidence(row.get("iptm"))):
            reasons[reason] = reasons.get(reason, 0) + 1
    return [f"표적–VHH 쌍 ipTM 미평가 {count}개 모델: {reason} "
            "전체 복합체 ipTM은 이 게이트의 대체값이 아닙니다."
            for reason, count in reasons.items()]


def score_complex(
    structure_path: str | Path,
    target_chain: str,
    binder_chain: str,
    hotspots,
    cfg: dict,
    pred: PredictionFiles | None = None,
    label: str = "",
    context: ScoringContext | None = None,
) -> dict:
    """예측 복합체 하나에 대한 전체 계면 지표 계산."""
    structure_path = Path(structure_path)
    pred = pred or find_prediction_files(structure_path)
    context = context or prepare_scoring_context(pred)
    if context.pred.cif.resolve() != pred.cif.resolve():
        raise ValueError("채점 사전 로드 입력과 예측 모델 파일이 다릅니다.")
    structure = context.structure
    chain_ids = pdb_io.chain_ids(structure)
    if target_chain not in chain_ids or binder_chain not in chain_ids:
        raise ValueError(f"체인 지정 오류: target={target_chain}, binder={binder_chain}, 구조={chain_ids}")

    target = pdb_io.get_chain(structure, target_chain)
    binder = pdb_io.get_chain(structure, binder_chain)

    contact_cutoff = float(cfg.get("score", {}).get("contact_cutoff", 5.0))
    contacts = pdb_io.contact_pairs(target, binder, cutoff=contact_cutoff, atom_mode="heavy")
    contacts_ca8 = pdb_io.contact_pairs(target, binder, cutoff=8.0, atom_mode="ca")

    score_cfg = cfg.get("score", {})
    hbond_cutoff = float(score_cfg.get("hbond_cutoff", 3.5))
    salt_cutoff = float(score_cfg.get("salt_bridge_cutoff", 4.0))
    clash_cutoff = float(score_cfg.get("clash_cutoff", 2.0))
    geometry = {"n_hbonds": 0, "n_salt_bridges": 0, "n_clashes": 0}
    # These cutoffs are independently configurable. Limiting geometry to
    # the interface-contact set would silently miss a clash/proximity pair
    # whenever its cutoff exceeds contact_cutoff.
    geometry_cutoff = max(contact_cutoff, hbond_cutoff, salt_cutoff, clash_cutoff)
    geometry_contacts = (contacts if geometry_cutoff == contact_cutoff else
                         pdb_io.contact_pairs(target, binder, cutoff=geometry_cutoff, atom_mode="heavy"))
    for contact in geometry_contacts:
        pair = pdb_io.atom_pair_geometry(
            contact["res_a"], contact["res_b"],
            hbond_cutoff=hbond_cutoff, salt_bridge_cutoff=salt_cutoff, clash_cutoff=clash_cutoff,
        )
        geometry["n_hbonds"] += pair["n_hbonds"]
        geometry["n_salt_bridges"] += pair["n_salt_bridges"]
        geometry["n_clashes"] += pair["n_clashes"]

    # Primary interface metrics remain target-specific; steric rejection must
    # also account for partners retained in the assembled complex.
    context_clashes = 0
    for chain_id in chain_ids:
        if chain_id in (target_chain, binder_chain):
            continue
        partner = pdb_io.get_chain(structure, chain_id)
        for contact in pdb_io.contact_pairs(partner, binder, cutoff=clash_cutoff, atom_mode="heavy"):
            context_clashes += pdb_io.atom_pair_geometry(
                contact["res_a"], contact["res_b"], clash_cutoff=clash_cutoff,
            )["n_clashes"]

    interface_target = {contact["a"] for contact in contacts}
    interface_binder = {contact["b"] for contact in contacts}
    interface_labels = interface_target | interface_binder

    plddt, ca_plddt, cb_plddt = context.plddt, context.ca_plddt, context.cb_plddt
    if plddt and not any(value > 0 for value in plddt.values()):
        # 예측기가 B-factor 에 pLDDT 를 쓰지 않은 구조(예: RF2 추출 PDB 는 전 원자 0.0)는 신뢰도가
        # '0' 인 것이 아니라 '없는' 것이다. 0/하한값(pDockQ 0.0183)을 측정값처럼 기록하지 않는다.
        plddt = {}
    if ca_plddt and not any(value > 0 for value in ca_plddt.values()):
        ca_plddt = {}
    if cb_plddt and not any(value > 0 for value in cb_plddt.values()):
        cb_plddt = {}
    interface_values = [plddt[label] for label in sorted(interface_labels) if label in plddt]
    interface_plddt = float(np.mean(interface_values)) if interface_values else float("nan")

    labels, index_map, pae, confidence = context.labels, context.index_map, context.pae, context.confidence
    pair_iptm, pair_iptm_source, pair_iptm_reason = target_binder_iptm(
        confidence, target_chain, binder_chain,
        [chain.id for chain in next(structure.get_models()) if any(pdb_io.iter_residues(chain))],
        source=pred.source)

    hotspot_labels = resolve_hotspot_labels(hotspots, target_chain, index_map)
    hotspot_min_dist: dict[str, float] = {}
    hotspot_raw_dist: dict[str, float] = {}
    binder_residues = list(pdb_io.iter_residues(binder))
    for label_h in hotspot_labels:
        chain_id, _resseq = label_h.split(":")
        res = None
        for candidate in pdb_io.iter_residues(target):
            if pdb_io.residue_label(candidate, target_chain) == label_h:
                res = candidate
                break
        if res is None:
            continue
        best = float("inf")
        for res_b in binder_residues:
            best = min(best, pdb_io.residue_min_distance(res, res_b))
        hotspot_raw_dist[label_h] = best
        hotspot_min_dist[label_h] = round(best, 2)
    # Rounding is for display only: 5.004 Å must not pass a 5 Å contact gate.
    contacted = [label_h for label_h, dist in hotspot_raw_dist.items() if dist <= contact_cutoff]
    requested_labels = _normalize_hotspot_requests(hotspots, target_chain)
    missing_hotspots = [label_h for label_h in requested_labels if label_h not in hotspot_min_dist]
    mapping_failed = bool(missing_hotspots)
    # Keep the canonical count ratio for gates/ranking; only the UI rounds it.
    # For example, 2/3 must not pass a configured 0.6667 contact threshold.
    # Missing residue mappings must never increase the apparent contact ratio.
    # Keep this diagnostic on the requested-site denominator; mapping failure
    # remains explicit and holds any ranking that weights this assessment.
    hotspot_contact_frac = len(contacted) / len(requested_labels) if hotspot_min_dist and requested_labels else float("nan")

    binder_values = [plddt[key] for key in sorted(plddt) if key.startswith(binder_chain + ":")]

    metrics: dict = {
        "label": label or pred.cif.stem,
        "source": pred.source,
        "cif_path": str(pred.cif),
        "target_chain": target_chain,
        "binder_chain": binder_chain,
        "n_contacts": len(contacts),
        "interface_assessment_status": "contacting" if contacts else "no_contacts",
        "n_contacts_ca8": len(contacts_ca8),
        "n_interface_target": len(interface_target),
        "n_interface_binder": len(interface_binder),
        "n_hbonds": geometry["n_hbonds"],
        "n_salt_bridges": geometry["n_salt_bridges"],
        "n_clashes": geometry["n_clashes"] + context_clashes,
        "n_target_clashes": geometry["n_clashes"],
        "n_context_clashes": context_clashes,
        "interface_plddt": interface_plddt,
        "binder_plddt": float(np.mean(binder_values)) if binder_values else float("nan"),
        "iptm": pair_iptm,
        "iptm_source": pair_iptm_source,
        "iptm_unavailable_reason": pair_iptm_reason,
        "iptm_scope": "selected_target_binder_pair",
        "iptm_chain_a": target_chain,
        "iptm_chain_b": binder_chain,
        "iptm_aggregation": ("native_global_two_chain_equivalent" if pair_iptm_source == "native_global_iptm_two_chain_complex"
                             else "minimum_native_directional_pair" if pair_iptm_reason is None else "not_assessed"),
        "ipsae_scope": "selected_chain_pair",
        "ipsae_chain_a": target_chain,
        "ipsae_chain_b": binder_chain,
        "ipsae_aggregation": "max_of_directional_pair_ipsae",
        "complex_iptm": _finite_confidence(confidence.get("iptm", confidence.get("protein_iptm"))),
        "ptm": _finite_confidence(confidence.get("ptm")),
        "prediction_sample_index": pred.sample_index,
        "prediction_retained_sample_count": pred.retained_sample_count,
        "prediction_sample_selection": pred.sample_selection,
        "prediction_sample_note": (f"예측 샘플 {pred.retained_sample_count}개가 보존되어 있으며 이 행은 "
                                   f"{'샘플 ' + str(pred.sample_index) if pred.sample_index is not None else '선택한 구조'}만 "
                                   "채점합니다. 다른 샘플은 이 행의 평가에 포함하지 않았습니다."
                                   if pred.retained_sample_count > 1 else None),
        "confidence_summary_precision": "unrounded_float64",
        "hotspot_contact_frac": hotspot_contact_frac,
        "n_hotspots": len(hotspot_min_dist),
        "n_hotspots_requested": len(requested_labels),
        "n_hotspots_contacted": len(contacted),
        "missing_hotspots": ";".join(missing_hotspots),
        "hotspot_mapping_failed": mapping_failed,
        **pdb_io.GEOMETRY_METHODS,
        "n_hbonds_cutoff_angstrom": hbond_cutoff,
        "n_salt_bridges_cutoff_angstrom": salt_cutoff,
        "n_clashes_cutoff_angstrom": clash_cutoff,
        "ipsae_d0dom_method": "Dunbrack_v4_PAE_mask_full_residue_token_identity_v1",
        "ipsae_d0dom_residue_identity": "chain_resseq_insertion",
        "ipsae_d0dom_upstream_residue_identity": "PDB_resseq_or_mmCIF_label_seq_id_without_insertions",
        "ipsae_d0dom_author_insertions_present": any(
            res.id[2].strip() for chain in (target, binder) for res in pdb_io.iter_residues(chain)
        ),
    }
    metrics.update(buried_sasa(structure, target_chain, binder_chain, n_contacts=len(contacts),
                              chain_sasa_cache=context.chain_sasa))
    metrics.update(pdockq_metrics_from_structure(structure, target_chain, binder_chain, cb_plddt))
    metrics.update(pdockq2_from_structure(structure, target_chain, binder_chain, ca_plddt, pae, index_map))
    metrics.update(interface_pae_summary(pae, index_map, contacts))
    for name, chain in (("target_plddt", target_chain), ("complex_plddt", None)):
        values = [v for l, v in plddt.items() if chain is None or l.startswith(chain + ":")]
        metrics[name] = float(np.mean(values)) if values else float("nan")

    if pae is not None:
        all_target = [index_map[l] for l in labels if l.startswith(target_chain + ":")]
        all_binder = [index_map[l] for l in labels if l.startswith(binder_chain + ":")]
        pae_cutoff = float(cfg.get("score", {}).get("interface_pae_cutoff", 10.0))
        metrics["interface_pae"] = interface_pae(pae, index_map, contacts)
        metrics["ipsae_ab"] = ipsae_from_pae(pae, all_target, all_binder, pae_cutoff)
        metrics["ipsae_ba"] = ipsae_from_pae(pae, all_binder, all_target, pae_cutoff)
        metrics["ipsae"] = max(metrics["ipsae_ab"], metrics["ipsae_ba"])
        metrics["ipsae_self"] = metrics["ipsae"]
        # A failed optional scorer must not erase which value is being used.
        metrics["ipsae_source"] = "self"
        metrics["ipsae_d0chn"] = max(
            ipsae_d0chn_from_pae(pae, all_target, all_binder, pae_cutoff),
            ipsae_d0chn_from_pae(pae, all_binder, all_target, pae_cutoff),
        )
        metrics["ipsae_d0dom"] = max(
            ipsae_d0dom_from_pae(pae, all_target, all_binder, pae_cutoff),
            ipsae_d0dom_from_pae(pae, all_binder, all_target, pae_cutoff),
        )
        official_script = resolve_path(cfg, cfg_get(cfg, "score.ipsae.official_script"))
        if official_script is not None and bool(cfg_get(cfg, "score.ipsae.use_official", True)):
            official_details: dict = {}
            try:
                official = run_official_ipsae(
                    official_script, pred.cif, pae, labels, target_chain, binder_chain,
                    work_dir=official_score_directory(pred.cif),
                    pae_cutoff=pae_cutoff,
                    dist_cutoff=float(cfg_get(cfg, "score.ipsae.dist_cutoff", 10.0)),
                    plddt=np.array([ca_plddt.get(label, float("nan")) for label in labels]),
                    diagnostics=official_details,
                )
                metrics["ipsae_official_status"] = "evaluated" if official is not None else "unavailable"
                if official_details.get("error"):
                    metrics["ipsae_official_error"] = official_details["error"]
            except ValueError as exc:
                # An optional adapter's incompatible confidence cannot erase
                # the independently computed geometry and self ipSAE. Keep its
                # validation strict; disclose this fallback rather than invent
                # confidence or absorb execution-control exceptions.
                official = None
                metrics["ipsae_official_status"] = "invalid_input"
                metrics["ipsae_official_error"] = str(exc)
            metrics["ipsae_official"] = official if official is not None else float("nan")
            if official is not None:
                metrics["ipsae"] = official
                metrics["ipsae_source"] = "official"
        else:
            metrics["ipsae_official"] = float("nan")
            metrics["ipsae_official_status"] = "not_requested"
            metrics["ipsae_source"] = "self"
    else:
        metrics["interface_pae"] = float("nan")
        metrics["ipsae"] = float("nan")
        metrics["ipsae_ab"] = float("nan")
        metrics["ipsae_ba"] = float("nan")
        metrics["ipsae_self"] = float("nan")
        metrics["ipsae_d0chn"] = float("nan")
        metrics["ipsae_d0dom"] = float("nan")
        metrics["ipsae_official"] = float("nan")
        metrics["ipsae_official_status"] = "missing_pae"
        metrics["ipsae_source"] = "none"

    metrics["ipsae_precision"] = ("upstream_output_precision" if metrics.get("ipsae_source") == "official"
                                  else "unrounded_float64" if metrics.get("ipsae_source") == "self" else "unavailable")
    metrics.update(pass_flags(metrics, cfg))
    return metrics


def pass_flags(metrics: dict, cfg: dict) -> dict:
    thresholds = cfg.get("score", {}).get("thresholds", {})

    def value(key, default=float("nan")):
        raw = metrics.get(key, default)
        if isinstance(raw, (bool, np.bool_)):
            return float("nan")
        try:
            number = float(raw)
            return number if math.isfinite(number) else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    raw_hotspot = metrics.get("hotspot_contact_frac")
    try:
        hotspot_missing = raw_hotspot is None or (
            not isinstance(raw_hotspot, (bool, np.bool_)) and math.isnan(float(raw_hotspot)))
    except (TypeError, ValueError):
        hotspot_missing = False
    hotspot_optional = (hotspot_missing and
                        metrics.get("n_hotspots_requested", metrics.get("n_hotspots", 0)) == 0)

    ipsae = value("ipsae")
    ipsae_pass = thresholds.get("ipsae", 0.6)
    ipsae_gray = thresholds.get("ipsae_gray", 0.4)
    strict_available = (math.isfinite(ipsae)
                        and not metrics.get("hotspot_mapping_failed", False)
                        and (hotspot_optional or math.isfinite(value("hotspot_contact_frac"))))
    strict_pass = (bool(ipsae >= ipsae_pass and (hotspot_optional or
                   value("hotspot_contact_frac") >= thresholds.get("hotspot_contact_frac", 0.5)))
                   if strict_available else None)
    return {
        "pass_ipsae": bool(ipsae >= ipsae_pass),
        "gray_ipsae": bool(ipsae_gray <= ipsae < ipsae_pass),
        "pass_iptm": bool(value("iptm") >= thresholds.get("iptm", 0.7)),
        "pass_plddt": bool(value("interface_plddt") >= thresholds.get("plddt_interface", 70.0)),
        "pass_hotspot": bool(
            not metrics.get("hotspot_mapping_failed", False)
            and (
                hotspot_optional
                or value("hotspot_contact_frac") >= thresholds.get("hotspot_contact_frac", 0.5)
            )
        ),
        "pass_clash": bool(value("n_clashes") <= thresholds.get("max_clashes", 0)),
        "pass_ipsae_strict": strict_pass,
        "pass_ipsae_strict_status": "not_assessed" if strict_pass is None else "met" if strict_pass else "unmet",
        "pass_ipsae_strict_role": "diagnostic_only_not_selection_gate",
    }


def official_score_directory(structure_path: Path) -> Path | None:
    """Return a run-owned receipt directory; None requests an owned temporary workspace."""
    path = Path(structure_path).resolve()
    for parent in path.parents:
        if (parent / "run_state.json").is_file() or (parent / "run_config.yaml").is_file():
            return parent / "03_score" / "ipsae_official"
    # Standalone scoring has no run boundary. The caller owns the temporary
    # directory lifetime, including failures and execution interruptions.
    return None


def run_official_ipsae(
    script_path: str | Path,
    cif_path: str | Path,
    pae: np.ndarray,
    labels: list[str],
    target_chain: str,
    binder_chain: str,
    work_dir: Path | None,
    pae_cutoff: float = 10.0,
    dist_cutoff: float = 10.0,
    plddt: np.ndarray | None = None,
    diagnostics: dict | None = None,
) -> float | None:
    """Preserve explicit receipts and clean standalone temporary workspaces."""
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="nanocofold-ipsae-") as directory:
            return _run_official_ipsae(script_path, cif_path, pae, labels, target_chain, binder_chain,
                                       Path(directory), pae_cutoff, dist_cutoff, plddt, diagnostics)
    return _run_official_ipsae(script_path, cif_path, pae, labels, target_chain, binder_chain,
                               work_dir, pae_cutoff, dist_cutoff, plddt, diagnostics)


def _run_official_ipsae(
    script_path: str | Path,
    cif_path: str | Path,
    pae: np.ndarray,
    labels: list[str],
    target_chain: str,
    binder_chain: str,
    work_dir: Path,
    pae_cutoff: float,
    dist_cutoff: float,
    plddt: np.ndarray | None,
    diagnostics: dict | None,
) -> float | None:
    """공식 ipSAE 스크립트(DunbrackLab, MIT)를 Boltz 출력 규약으로 실행하고 ipSAE(max)를 파싱한다.

    공식 스크립트는 `pae_*_model_0.npz`(+`plddt_*`, `confidence_*`)를 기대하므로
    임시 디렉터리에 규약대로 기록한 뒤 실행한다. ``plddt`` 는 검증된 PAE 축
    순서의 CA confidence(0–100)이다. 없는 confidence 는 NaN 으로 표시하며 0을
    관측값으로 만들지 않는다. 이 호환 호출에서 읽는 공식 값은 ipSAE 뿐이다.
    함께 기록되는 pDockQ/pDockQ2 는 upstream 의 Boltz 호환 CA confidence 경로이며
    NanoCoFold 의 별도 CB/CA 원저자 지표를 대체하지 않는다. pair ipTM 은 제공하지
    않으므로 NaN 이다. dist_cutoff 는 거리 보조 열에만 적용되고 ipSAE 에는 무영향이다.
    실패 시 None을 반환한다.
    """
    script_path = Path(script_path).resolve()
    if not script_path.exists():
        if diagnostics is not None:
            diagnostics.update(error=f"Official ipSAE script not found: {script_path}", stage="script_lookup")
        return None
    pae_array = np.asarray(pae, dtype=np.float32)
    validate_pae(pae_array, len(labels))
    plddt_array = (np.asarray(plddt, dtype=np.float32) if plddt is not None
                   else np.full(len(labels), np.nan, dtype=np.float32))
    if (plddt_array.shape != (len(labels),) or np.isinf(plddt_array).any()
            or np.any(plddt_array < 0) or np.any(plddt_array > 100)):
        raise ValueError("Official ipSAE CA confidence must match the PAE axis in 0–100 units; NaN denotes missing data")
    # This pipeline treats an all-zero B-factor field as absent confidence.
    if not np.any(plddt_array > 0):
        plddt_array = np.full(len(labels), np.nan, dtype=np.float32)
    known_confidence = int(np.isfinite(plddt_array).sum())
    confidence_status = ("missing_plddt" if known_confidence == 0 else
                         "partial_plddt" if known_confidence != len(labels) else "evaluated")
    # Upstream guesses normalized confidence from max<=1. A complete valid
    # 0–100 vector can also have genuinely low values<=1, so encode every
    # complete vector as 0–1 consistently. Partial NaN vectors must stay
    # 0–100 because upstream's max(NaN) disables its automatic rescaling.
    normalized_payload = known_confidence == len(labels)
    plddt_payload = (plddt_array.astype(np.float64) / 100.0
                     if normalized_payload else plddt_array)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    # A fresh invocation directory prevents prior cutoff/script/model results
    # from being mistaken for this run's output, including after failures.
    tmp = Path(tempfile.mkdtemp(prefix="invocation-", dir=work_dir))

    def record_failure(stage: str, error: Exception):
        details = {"error": f"{type(error).__name__}: {error}", "stage": stage}
        if diagnostics is not None:
            diagnostics.update(details)
        (tmp / "invocation_receipt.json").write_text(json.dumps({
            "schema_version": 2, "status": "unavailable", **details,
            "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
            "target_chain": target_chain, "binder_chain": binder_chain,
            "pae_cutoff": pae_cutoff, "dist_cutoff": dist_cutoff,
        }, indent=2) + "\n")
    tag = "design"
    np.savez(tmp / f"pae_{tag}_model_0.npz", pae=pae_array)
    np.savez(tmp / f"plddt_{tag}_model_0.npz", plddt=plddt_payload)
    chain_ids = sorted({label.split(":", 1)[0] for label in labels})
    pair_iptm = {
        # The pinned Python upstream reader accepts IEEE NaN. This private
        # compatibility input is deliberately not a canonical JSON report;
        # NaN prevents absent native chain-pair ipTM from appearing as zero.
        str(i): {str(j): float("nan") for j in range(len(chain_ids))}
        for i in range(len(chain_ids))
    }
    (tmp / f"confidence_{tag}_model_0.json").write_text(
        json.dumps({"pair_chains_iptm": pair_iptm}), encoding="utf-8"
    )
    # A PDB cannot be relabelled as .cif: the upstream parser dispatches by
    # suffix. Keep source coordinates byte-identical and preserve the format.
    structure_suffix = ".pdb" if Path(cif_path).suffix.lower() in {".pdb", ".ent"} else ".cif"
    cif_copy = tmp / f"{tag}_model_0{structure_suffix}"
    try:
        shutil.copy(cif_path, cif_copy)
    except OSError as exc:
        record_failure("structure_copy", exc)
        return None

    argv = [
        sys.executable, str(script_path),
        f"pae_{tag}_model_0.npz", cif_copy.name,
        f"{pae_cutoff:g}", f"{dist_cutoff:g}",
    ]
    try:
        # Upstream derives companion filenames by replacing "pae" in the
        # whole argument string. Relative leaf names prevent a parent folder
        # containing "pae" from hiding the real confidence file.
        result = shell.run(argv, cwd=tmp, log_path=tmp / "ipsae_official.log", check=False, quiet=True)
    except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired):
        # User cancellation and the enclosing computation deadline are
        # execution-control signals, not permission to continue with a score
        # fallback and publish the job as completed.
        raise
    except Exception as exc:  # noqa: BLE001
        record_failure("launch", exc)
        return None
    outputs = sorted(tmp.glob(f"{tag}_model_0_*.txt"))
    (tmp / "invocation_receipt.json").write_text(json.dumps({
        "schema_version": 2,
        "structure_sha256": hashlib.sha256(Path(cif_path).read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "pae_sha256": hashlib.sha256(pae_array.tobytes()).hexdigest(),
        "pae_cutoff": pae_cutoff, "dist_cutoff": dist_cutoff,
        "dist_cutoff_applies_to": "distance_auxiliary_columns_only_not_ipSAE",
        "plddt_sha256": hashlib.sha256(plddt_payload.tobytes()).hexdigest(),
        "plddt_input_sha256": hashlib.sha256(plddt_array.tobytes()).hexdigest(),
        "plddt_status": confidence_status, "plddt_known_residues": known_confidence,
        "plddt_axis": "verified_PAE_residue_order", "plddt_atom": "CA",
        "plddt_input_scale": "0-100", "plddt_scale": "0-1" if normalized_payload else "0-100",
        "structure_format": structure_suffix[1:],
        "pair_iptm_status": "not_provided",
        "canonical_output_columns": ["ipSAE"],
        "auxiliary_outputs_canonical": False,
        "auxiliary_metric_caveat": "Upstream Boltz compatibility uses CA confidence for auxiliary pDockQ/pDockQ2; use NanoCoFold canonical author-atom metrics instead",
        "target_chain": target_chain, "binder_chain": binder_chain,
        "returncode": result.returncode, "outputs": [p.name for p in outputs],
    }, indent=2) + "\n")
    if not outputs:
        return None
    if result.returncode != 0:
        # 공식 스크립트가 부가 지표(iptm 등)에서 실패해도 ipSAE 표는 이미 기록된다.
        pass
    text = outputs[0].read_text(encoding="utf-8", errors="ignore")
    return _parse_ipsae_table(text, target_chain, binder_chain)


def _parse_ipsae_table(text: str, target_chain: str, binder_chain: str) -> float | None:
    header_index: int | None = None
    columns: list[str] = []
    best: float | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = stripped.split()
        if header_index is None:
            if "Chn1" in tokens and "Chn2" in tokens and "ipSAE" in tokens:
                header_index = 0
                columns = tokens
            continue
        if len(tokens) != len(columns):
            continue
        try:
            value = float(tokens[columns.index("ipSAE")])
        except (ValueError, IndexError):
            continue
        if not math.isfinite(value) or not 0 <= value <= 1:
            continue
        chain_pair = {tokens[columns.index("Chn1")], tokens[columns.index("Chn2")]}
        if chain_pair != {target_chain, binder_chain}:
            continue
        if "Type" in columns and tokens[columns.index("Type")] not in ("max", "asym"):
            continue
        best = value if best is None else max(best, value)
        if "Type" in columns and tokens[columns.index("Type")] == "max":
            break
    return best


def score_directory(pred_dir: str | Path, target_chain: str, binder_chain: str, hotspots, cfg: dict) -> list[dict]:
    pred_dir = Path(pred_dir)
    rows: list[dict] = []
    for sub in sorted(p for p in pred_dir.iterdir() if p.is_dir()):
        try:
            pred = find_prediction_files(sub)
            rows.append(score_complex(pred.cif, target_chain, binder_chain, hotspots, cfg, pred=pred, label=sub.name))
        except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired):
            raise
        except Exception as exc:  # noqa: BLE001
            rows.append({"label": sub.name, "error": str(exc)})
    return rows
