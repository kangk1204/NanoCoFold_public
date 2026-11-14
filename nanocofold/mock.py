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


def make_mock_engine_output(out_dir, identifier, chains, hotspots, *, engine, seed=42, samples=1):
    """Synthetic multi-chain fixtures in the selected engine's retained layout.

    No upstream model is invoked. Each receipt explicitly records mock=True.
    Existing two-chain generators remain available for historical test fixtures.
    """
    import json
    from . import provenance
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    letters = list(chains)
    n = sum(map(len, chains.values()))
    for index in range(samples):
        q = _quality(identifier, seed + index)
        structure = build_complex_structure(chains[letters[0]], chains[letters[-1]], hotspots,
                                            jitter=.15 * (1-q), seed=seed + index,
                                            pull_closer=2.4*(1-q) if q < .35 else 0.)
        model = next(structure.get_models())
        binder = model['B']; model.detach_child('B'); binder.id = letters[-1]
        for i, letter in enumerate(letters[1:-1]):
            model.add(_build_chain(letter, chains[letter], np.array([0., 12. + 8.*i, 0.])))
        model.add(binder)
        confidence = .55 + .4*q
        nt, nb = len(chains[letters[0]]), len(chains[letters[-1]])
        old_plddt = np.clip(40.+55.*q+np.random.default_rng(seed+index+len(identifier)).normal(0,3,nt+nb),5,99).astype(np.float32)
        plddt = np.full(n, 85., dtype=np.float32)
        mapping = np.r_[np.arange(nt), np.arange(n-nb,n)]
        plddt[mapping] = old_plddt
        for atom in structure.get_atoms(): atom.set_bfactor(100*(.60+.40*q))
        pae = np.full((n,n), 20.-4.*q, dtype=np.float32); np.fill_diagonal(pae, 1.5)
        pae[np.ix_(mapping,mapping)] = _pae_matrix(nt,nb,q,hotspots,list(range(int(nb*.45),int(nb*.75))))
        axis = np.full((len(letters), len(letters)), confidence, dtype=np.float32)
        if engine == 'chai1':
            path = write_cif(structure, out_dir/f'pred.model_idx_{index}.cif')
            np.savez_compressed(out_dir/f'scores.model_idx_{index}.npz', pae=pae, plddt=plddt,
                                ptm=np.float32(confidence), iptm=np.float32(confidence), per_chain_pair_iptm=axis,
                                aggregate_score=np.float32(confidence))
            receipt = {'schema_version':1, 'mock':True, 'model_index':index, 'source':'SampleRanking.asym_ids',
                'chain_mapper':'chai_lab.data.io.cif_utils.get_chain_letter', 'pair_axes':['query_chain','key_chain'],
                'asym_ids':list(range(1,len(letters)+1)), 'chain_ids':letters,
                'chain_mapper_sha256':provenance.file_digest(Path(__file__)), 'chai_version':'synthetic-not-an-engine-run',
                'structure_sha256':provenance.file_digest(path)}
            (out_dir/f'chain_axis_idx_{index}.json').write_text(json.dumps(receipt))
        else:
            stem = f'{engine}_model_{index}' if engine in {'colabfold','opendde'} else f'{identifier}_model_{index}'
            if engine == 'colabfold': write_pdb(structure, out_dir/(stem+'.pdb'))
            else: write_cif(structure, out_dir/(stem+'.cif'))
            native = {'mock':True, 'source':engine, 'ptm':confidence, 'iptm':confidence,
                      'complex_plddt':float(plddt.mean()/100), 'plddt':float(plddt.mean())}
            if engine == 'boltz2': native['pair_chains_iptm'] = {str(i):{str(j):float(axis[i,j]) for j in range(len(letters))} for i in range(len(letters))}
            if engine == 'opendde': native.update(chain_axis=letters,chain_pair_iptm=axis.tolist())
            if engine == 'colabfold': native.update(rank=index+1,raw_model=f'{identifier}_unrelaxed_rank_{index+1:03d}_model_1_seed_{seed+index}.pdb')
            (out_dir/('confidence_'+stem+'.json')).write_text(json.dumps(native))
            np.savez_compressed(out_dir/('pae_'+stem+'.npz'),pae=pae)
    (out_dir/'synthetic_output.json').write_text(json.dumps({'mock':True,'engine_layout':engine,'models':samples,'chains':letters}))


def make_mock_vhh(vhh_seq: str, path: Path, seed: int = 11) -> Path:
    structure = Structure.Structure("mock")
    structure.add(Model(0))
    chain = _build_chain("H", vhh_seq, np.zeros(3))
    next(structure.get_models()).add(chain)
    for res in structure.get_residues():
        for atom in res:
            atom.set_bfactor(80.0)
    return write_pdb(structure, path)
