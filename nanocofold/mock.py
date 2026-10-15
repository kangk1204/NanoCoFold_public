"""GPU/모델 없이 파이프라인 전체를 검증하기 위한 합성 구조·예측 생성기.

실제 모델 출력(Chai-1 cif+npz, Boltz-2 cif+json+npz)과 동일한 파일 레이아웃을
만들어 score/report/rank 단계를 CPU에서 그대로 테스트할 수 있게 한다.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from Bio.PDB import Structure
from Bio.PDB.Atom import Atom
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.Residue import Residue

from .utils.seq import AA1TO3

_BACKBONE = ("N", "CA", "C", "O", "CB")


def _quality(design_id: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}:{design_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _residue_atoms(center: np.ndarray, direction: np.ndarray, up: np.ndarray, side: np.ndarray, pull: float) -> dict[str, np.ndarray]:
    return {
        "N": center - 1.20 * direction + 0.45 * up,
        "CA": center,
        "C": center + 1.20 * direction + 0.45 * up,
        "O": center + 1.20 * direction + 0.45 * up + 0.60 * side,
        "CB": center + 0.70 * side + pull * up,
    }


def _build_chain(chain_id: str, sequence: str, origin: np.ndarray, step: float = 3.6, offset: np.ndarray | None = None, pull_at: set[int] | None = None) -> Chain:
    chain = Chain(chain_id)
    pull_at = pull_at or set()
    for index, aa in enumerate(sequence):
        center = origin + np.array([index * step, 0.0, 0.0]) + np.array([0.0, 0.35 * math.sin(index * 1.1), 0.25 * math.cos(index * 0.7)])
        direction = np.array([1.0, 0.0, 0.0])
        up = np.array([0.0, 0.0, 1.0])
        side = np.array(offset if offset is not None else [0.0, 1.0, 0.0], dtype=float)
        side = side / np.linalg.norm(side)
        pull = 1.1 if index in pull_at else 0.0
        coords = _residue_atoms(center, direction, up, side, pull)
        residue = Residue((" ", index + 1, " "), AA1TO3.get(aa, "ALA"), "")
        for atom_name, coord in coords.items():
            element = atom_name[0]
            residue.add(Atom(atom_name, np.asarray(coord, dtype=float), 0.0, 1.0, " ", atom_name, 1, element))
        chain.add(residue)
    return chain


def build_complex_structure(target_seq: str, binder_seq: str, hotpots: list[int], jitter: float = 0.0, seed: int = 0, pull_closer: float = 0.0) -> Structure:
    """타깃(A)–나노바디(B) 합성 복합체. CDR 부위가 결합 부위와 접촉하도록 배치."""
    rng = np.random.default_rng(seed)
    structure = Structure.Structure("mock")
    structure.add(Model(0))
    model = next(structure.get_models())

    target = _build_chain("A", target_seq, np.array([0.0, 0.0, 0.0]))
    model.add(target)

    binder_hot_at = {max(0, h - 1) for h in hotpots}
    cdr_at = set(range(int(len(binder_seq) * 0.45), int(len(binder_seq) * 0.75)))
    pull_at = cdr_at if cdr_at else binder_hot_at
    binder_y = 5.4 - pull_closer
    binder = _build_chain("B", binder_seq, np.array([0.0, binder_y, 0.0]), offset=[0.0, -1.0, 0.0], pull_at=pull_at)
    if jitter:
        for res in binder:
            for atom in res:
                atom.set_coord(atom.get_coord() + rng.normal(0.0, jitter, 3))
    model.add(binder)
    return structure


def write_cif(structure: Structure, path: Path) -> Path:
    from Bio.PDB import MMCIFIO

    path.parent.mkdir(parents=True, exist_ok=True)
    io = MMCIFIO()
    io.set_structure(structure)
    io.save(str(path))
    return path


def write_pdb(structure: Structure, path: Path) -> Path:
    from Bio.PDB import PDBIO

    path.parent.mkdir(parents=True, exist_ok=True)
    io = PDBIO()
    io.set_structure(structure)
    io.save(str(path))
    return path


def _pae_matrix(n_target: int, n_binder: int, quality: float, hotpots: list[int], binder_cdr: list[int]) -> np.ndarray:
    n_total = n_target + n_binder
    same_chain = 20.0 - 4.0 * quality
    cross_chain = 1.0 + 13.0 * (1.0 - quality)
    pae = np.full((n_total, n_total), same_chain, dtype=np.float32)
    pae[:n_target, n_target:] = cross_chain
    pae[n_target:, :n_target] = cross_chain
    np.fill_diagonal(pae, 1.5)
    for hotspot in hotpots:
        for cdr in binder_cdr:
            # hotpots 는 1-based 서열 위치, binder_cdr 는 0-based 잔기 인덱스(구조 배치와 동일 기준).
            i = hotspot - 1
            j = n_target + cdr
            if 0 <= i < n_total and 0 <= j < n_total:
                value = max(1.0, cross_chain - 1.0)
                pae[i, j] = value
                pae[j, i] = value
    return pae


def make_mock_chai_output(
    out_dir: Path,
    design_id: str,
    target_seq: str,
    binder_seq: str,
    hotpots: list[int],
    seed: int = 42,
    model_idx: int = 0,
    quality: float | None = None,
) -> dict:
    quality = _quality(design_id, seed) if quality is None else float(quality)
    complex_structure = build_complex_structure(
        target_seq, binder_seq, hotpots,
        jitter=0.15 * (1 - quality),
        seed=seed,
        pull_closer=2.4 * (1 - quality) if quality < 0.35 else 0.0,
    )
    for res in complex_structure.get_residues():
        for atom in res:
            atom.set_bfactor(100.0 * (0.60 + 0.40 * quality))
    out_dir.mkdir(parents=True, exist_ok=True)
    cif_path = write_cif(complex_structure, out_dir / f"pred.model_idx_{model_idx}.cif")
    n_target, n_binder = len(target_seq), len(binder_seq)
    binder_cdr = list(range(int(n_binder * 0.45), int(n_binder * 0.75)))
    pae = _pae_matrix(n_target, n_binder, quality, hotpots, binder_cdr)
    plddt = np.clip(40.0 + 55.0 * quality + np.random.default_rng(seed + len(design_id)).normal(0, 3, n_target + n_binder), 5, 99).astype(np.float32)
    np.savez(
        out_dir / f"scores.model_idx_{model_idx}.npz",
        pae=pae,
        pde=pae.copy(),
        plddt=plddt,
        ptm=np.float32(0.55 + 0.40 * quality),
        iptm=np.float32(0.55 + 0.40 * quality),
        aggregate_score=np.float32(0.55 + 0.40 * quality),
    )
    return {"cif": cif_path, "quality": quality}


def make_mock_boltz_output(
    out_dir: Path,
    design_id: str,
    target_seq: str,
    binder_seq: str,
    hotpots: list[int],
    seed: int = 42,
    model_idx: int = 0,
    quality: float | None = None,
) -> dict:
    import json

    quality = _quality(design_id, seed) if quality is None else float(quality)
    complex_structure = build_complex_structure(
        target_seq, binder_seq, hotpots,
        jitter=0.12 * (1 - quality),
        seed=seed + 1,
        pull_closer=2.2 * (1 - quality) if quality < 0.35 else 0.0,
    )
    for res in complex_structure.get_residues():
        for atom in res:
            atom.set_bfactor(0.60 + 0.40 * quality)
    out_dir.mkdir(parents=True, exist_ok=True)
    cif_path = write_cif(complex_structure, out_dir / f"{design_id}_model_{model_idx}.cif")
    n_target, n_binder = len(target_seq), len(binder_seq)
    binder_cdr = list(range(int(n_binder * 0.45), int(n_binder * 0.75)))
    pae = _pae_matrix(n_target, n_binder, quality, hotpots, binder_cdr)
    np.savez(out_dir / f"pae_{design_id}_model_{model_idx}.npz", pae=pae)
    confidence = {
        "confidence_score": 0.6 + 0.35 * quality,
        "ptm": 0.55 + 0.40 * quality,
        "iptm": 0.55 + 0.40 * quality,
        "protein_iptm": 0.55 + 0.40 * quality,
        "complex_plddt": 0.60 + 0.40 * quality,
        "complex_iplddt": 0.58 + 0.40 * quality,
    }
    (out_dir / f"confidence_{design_id}_model_{model_idx}.json").write_text(json.dumps(confidence), encoding="utf-8")
    return {"cif": cif_path, "quality": quality}


def make_mock_target_only(target_seq: str, path: Path, seed: int = 7) -> Path:
    structure = Structure.Structure("mock")
    structure.add(Model(0))
    chain = _build_chain("A", target_seq, np.zeros(3))
    next(structure.get_models()).add(chain)
    for res in structure.get_residues():
        for atom in res:
            atom.set_bfactor(85.0)
    return write_pdb(structure, path)


def make_mock_vhh(vhh_seq: str, path: Path, seed: int = 11) -> Path:
    structure = Structure.Structure("mock")
    structure.add(Model(0))
    chain = _build_chain("H", vhh_seq, np.zeros(3))
    next(structure.get_models()).add(chain)
    for res in structure.get_residues():
        for atom in res:
            atom.set_bfactor(80.0)
    return write_pdb(structure, path)
