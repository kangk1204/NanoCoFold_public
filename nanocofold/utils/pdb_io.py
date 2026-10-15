from __future__ import annotations

import gzip
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
from Bio.Data.IUPACData import atom_weights
from Bio.PDB import MMCIFParser, PDBIO, PDBParser, Select, Structure
from Bio.PDB.Chain import Chain
from Bio.PDB.Residue import Residue

from .seq import AA3TO1

STANDARD_AA = set(AA3TO1)

# Standard side-chain charge states only. Neutral Asn/Gln amides and His
# (whose protonation is not specified by these structures) are not ions.
_CHARGED_SIDECHAIN_ATOMS = {
    "LYS": (1, frozenset({"NZ"})),
    "ARG": (1, frozenset({"NE", "NH1", "NH2"})),
    "ASP": (-1, frozenset({"OD1", "OD2"})),
    "GLU": (-1, frozenset({"OE1", "OE2"})),
}
GEOMETRY_METHODS = {
    "n_hbonds_method": "NO_heavy_atom_distance_proximity_no_angle_v1",
    "n_salt_bridges_method": "LYS_ARG_to_ASP_GLU_opposite_charge_atom_distance_v2",
    "n_clashes_method": "heavy_atom_pair_fixed_distance_no_vdw_or_bond_exceptions_v1",
    "n_hbonds_unit": "atom_pairs",
    "n_salt_bridges_unit": "atom_pairs",
    "n_clashes_unit": "atom_pairs",
}

_PARSERS = {".pdb": PDBParser, ".ent": PDBParser, ".cif": MMCIFParser, ".mmcif": MMCIFParser}


def load_structure(path: str | Path) -> Structure:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".gz":
        suffix = Path(path.stem).suffix.lower()
    parser_cls = _PARSERS.get(suffix)
    if parser_cls is None:
        raise ValueError(f"지원하지 않는 구조 형식: {path} (pdb/cif 지원)")
    parser = parser_cls(QUIET=True)
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return parser.get_structure(path.stem, handle)
    return parser.get_structure(path.stem, str(path))


def get_chain(structure: Structure, chain_id: str) -> Chain:
    model = next(structure.get_models())
    for chain in model:
        if chain.id == chain_id:
            return chain
    available = [chain.id for chain in model]
    raise KeyError(f"체인 '{chain_id}' 없음. 사용 가능: {available}")


def chain_ids(structure: Structure) -> list[str]:
    model = next(structure.get_models())
    return [chain.id for chain in model]


def is_standard_residue(res: Residue) -> bool:
    return res.id[0] != "W" and res.resname in STANDARD_AA


def has_peptide_backbone(res: Residue) -> bool:
    """Include composite chromophores with numbered peptide atom names."""
    atoms = set(res.child_dict)
    return any({f"N{suffix}", f"CA{suffix}", f"C{suffix}"} <= atoms
               for suffix in ("", "1", "2", "3"))


def validate_polymer_residues(chain: Chain) -> None:
    """Do not silently drop unknown modified residues with peptide backbones."""
    from Bio.PDB.Polypeptide import is_aa

    unsupported = [residue_label(res, chain.id) + ":" + res.resname for res in chain
                   if not is_standard_residue(res) and (is_aa(res, standard=False) or has_peptide_backbone(res))]
    if unsupported:
        raise ValueError("지원하지 않는 변형 단백질 잔기가 있습니다: " + ", ".join(unsupported) +
                         ". 원본 좌표를 임의 삭제·변환하지 않았습니다. 검증한 전체 단백질 서열을 "
                         "target.sequence 또는 target.fasta로 입력하고 target.pdb=null, "
                         "structure.provider=boltz2|chai1로 새 구조를 예측하세요. "
                         "서열 예측은 변형 잔기의 화학 구조를 재현하지 않습니다.")


def iter_residues(chain: Chain) -> Iterator[Residue]:
    for res in chain:
        if is_standard_residue(res):
            yield res


def iter_heavy_atoms(res: Residue):
    for atom in res.get_atoms():
        element = (atom.element or "").strip().upper()
        if element and element not in {"H", "D"}:
            yield atom
        elif not element:
            # PDB aligns a two-letter element at column 13, unlike a
            # one-letter hydrogen atom name (e.g. "HG  " vs " HG ").
            fullname = atom.get_fullname()
            two_letter = (len(fullname) == 4 and fullname[2:] == "  "
                          and fullname[:2].title() in atom_weights)
            if two_letter or not atom.get_name().startswith(("H", "D")):
                yield atom


def residue_label(res: Residue, chain_id: str, with_icode: bool = True) -> str:
    het, resseq, icode = res.id
    suffix = icode.strip() if with_icode else ""
    return f"{chain_id}:{resseq}{suffix}"


def residue_key(res: Residue) -> tuple[int, str]:
    return int(res.id[1]), str(res.id[2]).strip()


def chain_sequence(chain: Chain) -> str:
    return "".join(AA3TO1.get(res.resname, "X") for res in iter_residues(chain))


def ca_coord(res: Residue) -> np.ndarray | None:
    if "CA" in res:
        return res["CA"].get_coord().astype(float)
    return None


def chain_ca_coords(chain: Chain) -> tuple[list[str], np.ndarray]:
    labels: list[str] = []
    coords: list[np.ndarray] = []
    for res in iter_residues(chain):
        coord = ca_coord(res)
        if coord is not None:
            labels.append(residue_label(res, chain.id))
            coords.append(coord)
    if not coords:
        return [], np.zeros((0, 3))
    return labels, np.vstack(coords)


def residue_min_distance(res_a: Residue, res_b: Residue) -> float:
    best = float("inf")
    for atom_a in iter_heavy_atoms(res_a):
        coord_a = atom_a.get_coord()
        for atom_b in iter_heavy_atoms(res_b):
            dist = float(np.linalg.norm(coord_a - atom_b.get_coord()))
            if dist < best:
                best = dist
    return best


def contact_pairs(
    chain_a: Chain,
    chain_b: Chain,
    cutoff: float = 5.0,
    atom_mode: str = "heavy",
) -> list[dict]:
    """두 체인 간 접촉 잔기 쌍. atom_mode=ca 는 CA-CA 기준, heavy 는 중원자 최소거리."""
    results: list[dict] = []
    if atom_mode == "ca":
        labels_a, coords_a = chain_ca_coords(chain_a)
        labels_b, coords_b = chain_ca_coords(chain_b)
        if len(coords_a) == 0 or len(coords_b) == 0:
            return results
        dist_mat = np.linalg.norm(coords_a[:, None, :] - coords_b[None, :, :], axis=-1)
        for i, j in zip(*np.where(dist_mat <= cutoff)):
            results.append({"a": labels_a[i], "b": labels_b[j], "dist": float(dist_mat[i, j])})
        return results

    residues_b = list(iter_residues(chain_b))
    for res_a in iter_residues(chain_a):
        coord_a = np.array([atom.get_coord() for atom in iter_heavy_atoms(res_a)])
        if len(coord_a) == 0:
            continue
        for res_b in residues_b:
            coord_b = np.array([atom.get_coord() for atom in iter_heavy_atoms(res_b)])
            if len(coord_b) == 0:
                continue
            dist_mat = np.linalg.norm(coord_a[:, None, :] - coord_b[None, :, :], axis=-1)
            min_dist = float(dist_mat.min())
            if min_dist <= cutoff:
                results.append({
                    "a": residue_label(res_a, chain_a.id),
                    "b": residue_label(res_b, chain_b.id),
                    "dist": min_dist,
                    "res_a": res_a,
                    "res_b": res_b,
                })
    return results


def atom_pair_geometry(
    res_a: Residue,
    res_b: Residue,
    hbond_cutoff: float = 3.5,
    salt_bridge_cutoff: float = 4.0,
    clash_cutoff: float = 2.0,
) -> dict:
    """Distance-only interface diagnostics, all counted as heavy-atom pairs.

    ``n_hbonds`` is a legacy name for N/O proximity pairs, including backbone
    atoms. It does not establish hydrogen bonds: donor/acceptor chemistry,
    hydrogen positions and angles are not tested. Salt bridges require
    oppositely charged standard Lys/Arg and Asp/Glu side-chain atoms; His
    protonation is unknown and neutral Asn/Gln atoms are excluded. Clashes
    use a fixed distance rather than element-specific van der Waals radii,
    and do not infer covalent or disulfide bonds. These are diagnostic counts,
    not interaction energies or experimental binding evidence.
    """
    hbonds: list[dict] = []
    clashes: list[dict] = []
    salt_bridges: list[dict] = []

    polar_a = [(a.get_name(), a) for a in iter_heavy_atoms(res_a) if (a.element or "").strip().upper() in ("N", "O")]
    polar_b = [(a.get_name(), a) for a in iter_heavy_atoms(res_b) if (a.element or "").strip().upper() in ("N", "O")]
    charge_a, charged_names_a = _CHARGED_SIDECHAIN_ATOMS.get(res_a.resname, (0, frozenset()))
    charge_b, charged_names_b = _CHARGED_SIDECHAIN_ATOMS.get(res_b.resname, (0, frozenset()))
    charged_a = [(a.get_name(), a) for a in iter_heavy_atoms(res_a) if a.get_name() in charged_names_a]
    charged_b = [(a.get_name(), a) for a in iter_heavy_atoms(res_b) if a.get_name() in charged_names_b]

    for name_a, atom_a in polar_a:
        for name_b, atom_b in polar_b:
            dist = float(np.linalg.norm(atom_a.get_coord() - atom_b.get_coord()))
            if dist <= hbond_cutoff:
                hbonds.append({"atom_a": name_a, "atom_b": name_b, "dist": round(dist, 2)})

    for name_a, atom_a in charged_a:
        for name_b, atom_b in charged_b:
            if charge_a * charge_b != -1:
                continue
            dist = float(np.linalg.norm(atom_a.get_coord() - atom_b.get_coord()))
            if dist <= salt_bridge_cutoff:
                salt_bridges.append({"atom_a": name_a, "atom_b": name_b, "dist": round(dist, 2)})

    for atom_a in iter_heavy_atoms(res_a):
        for atom_b in iter_heavy_atoms(res_b):
            dist = float(np.linalg.norm(atom_a.get_coord() - atom_b.get_coord()))
            if dist < clash_cutoff:
                clashes.append({"atom_a": atom_a.get_name(), "atom_b": atom_b.get_name(), "dist": round(dist, 2)})

    return {
        "hbonds": hbonds,
        "salt_bridges": salt_bridges,
        "clashes": clashes,
        "n_hbonds": len(hbonds),
        "n_salt_bridges": len(salt_bridges),
        "n_clashes": len(clashes),
        **GEOMETRY_METHODS,
    }


def compute_residue_sasa(structure: Structure, probe_radius: float = 1.4) -> dict[str, float]:
    """Shrake-Rupley SASA. 반환: {'A:34': sasa_Å²} (Biopython, MIT-호환 라이선스)."""
    from Bio.PDB.SASA import ShrakeRupley

    structure = _copy_structure(structure)
    # Models describe alternative conformers, not simultaneous occluders.
    # Water/ligands are handled separately (e.g. glycan-distance diagnostics).
    models = list(structure.get_models())
    if not models:
        return {}
    for other in models[1:]:
        structure.detach_child(other.id)
    model = models[0]
    for chain in list(model):
        for res in list(chain):
            if not is_standard_residue(res):
                chain.detach_child(res.id)
        if not len(chain):
            model.detach_child(chain.id)
    if not len(model):
        return {}
    ShrakeRupley(probe_radius=probe_radius).compute(model, level="R")
    result: dict[str, float] = {}
    model = next(structure.get_models())
    for chain in model:
        for res in chain:
            if is_standard_residue(res):
                result[residue_label(res, chain.id)] = float(res.sasa)
    return result


def parse_plddt(structure: Structure, normalize: bool = True) -> dict[str, float]:
    """B-factor에서 잔기 평균 pLDDT/신뢰도 값을 읽는다.

    normalize=True 는 예측 구조의 0-1 confidence 값을 기존처럼 0-100 pLDDT
    스케일로 올린다. 실험 구조 B-factor는 normalize=False 로 원값을 읽는다.
    """
    raw: dict[str, list[float]] = {}
    for model in structure.get_models():
        for chain in model:
            for res in chain:
                if not is_standard_residue(res):
                    continue
                values = [float(atom.get_bfactor()) for atom in res.get_atoms()]
                if values:
                    raw[residue_label(res, chain.id)] = values
        break
    out: dict[str, float] = {}
    for label, values in raw.items():
        mean = float(np.mean(values))
        out[label] = mean
    if normalize and out and all(np.isfinite(v) for v in out.values()) and max(out.values()) <= 1.0:
        out = {key: value * 100.0 for key, value in out.items()}
    return out


def metric_confidence_atom(residue: Residue, atom_mode: str):
    """Return the calibrated metric atom: CA, or CB with CA for Gly only."""
    if atom_mode not in {"ca", "cb"}:
        raise ValueError("Confidence atom mode must be ca or cb")
    name = "CA" if atom_mode == "ca" or residue.get_resname() == "GLY" else "CB"
    return residue[name] if name in residue else None


def parse_atom_plddt(structure: Structure, atom_mode: str = "ca",
                     normalize: bool = True) -> dict[str, float]:
    """Read contact-atom confidence required by the pDockQ/pDockQ2 authors.

    This is deliberately separate from the residue-mean confidence used for
    chain/interface summaries. CB mode uses CA only for Gly; a missing CB in
    another residue cannot silently change the calibrated atom definition.
    As for parse_plddt, normalize predicted 0–1 confidence to 0–100.
    """
    # Validate even when the structure has no standard residues.
    if atom_mode not in {"ca", "cb"}:
        raise ValueError("Confidence atom mode must be ca or cb")
    out = {}
    for chain in next(structure.get_models()):
        for residue in iter_residues(chain):
            atom = metric_confidence_atom(residue, atom_mode)
            if atom is not None:
                out[residue_label(residue, chain.id)] = float(atom.get_bfactor())
    if normalize and out and all(np.isfinite(v) for v in out.values()) and max(out.values()) <= 1.0:
        out = {key: value * 100.0 for key, value in out.items()}
    return out


def kabsch_rmsd(coords_a: np.ndarray, coords_b: np.ndarray) -> float:
    if coords_a.shape != coords_b.shape or coords_a.shape[0] < 3:
        raise ValueError("RMSD 계산에는 동일 길이(≥3) 좌표가 필요합니다.")
    center_a = coords_a - coords_a.mean(axis=0)
    center_b = coords_b - coords_b.mean(axis=0)
    cov = center_a.T @ center_b
    u, _, vt = np.linalg.svd(cov)
    sign = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1.0, 1.0, sign]) @ u.T
    rotated = (rotation @ center_a.T).T
    return float(np.sqrt(((rotated - center_b) ** 2).sum(axis=1).mean()))


def shared_ca_coords(structure_a: Structure, structure_b: Structure, chain_id: str, labels: Sequence[str] | None = None) -> tuple[np.ndarray, np.ndarray, list[str]]:
    chain_a = get_chain(structure_a, chain_id)
    chain_b = get_chain(structure_b, chain_id)
    map_a = {residue_label(res, chain_id): res for res in iter_residues(chain_a)}
    map_b = {residue_label(res, chain_id): res for res in iter_residues(chain_b)}
    common = [label for label in map_a if label in map_b and (labels is None or label in labels)]
    coords_a, coords_b, used = [], [], []
    for label in common:
        ca_a = ca_coord(map_a[label])
        ca_b = ca_coord(map_b[label])
        if ca_a is None or ca_b is None:
            continue
        coords_a.append(ca_a)
        coords_b.append(ca_b)
        used.append(label)
    if not coords_a:
        return np.zeros((0, 3)), np.zeros((0, 3)), []
    return np.vstack(coords_a), np.vstack(coords_b), used


class _ResidueSelect(Select):
    def __init__(self, chain_id: str | None = None, labels: set[str] | None = None, drop_hetatm: bool = True):
        self.chain_id = chain_id
        self.labels = labels
        self.drop_hetatm = drop_hetatm

    def accept_residue(self, residue: Residue) -> bool:  # noqa: N802
        if self.drop_hetatm and not is_standard_residue(residue):
            return False
        if self.labels is not None and residue.resname in STANDARD_AA:
            label = residue_label(residue, self.chain_id or residue.get_parent().id)
            if label not in self.labels:
                return False
        return True


def save_structure(structure: Structure, path: str | Path, chain_id: str | None = None, labels: set[str] | None = None, drop_hetatm: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    io = PDBIO()
    io.set_structure(structure)
    io.save(str(path), select=_ResidueSelect(chain_id, labels, drop_hetatm))
    return path


def chain_coords_by_label(chain: Chain) -> dict[str, np.ndarray]:
    coords: dict[str, np.ndarray] = {}
    for res in iter_residues(chain):
        coord = ca_coord(res)
        if coord is not None:
            coords[residue_label(res, chain.id)] = coord
    return coords


def _copy_structure(structure: Structure) -> Structure:
    return structure.copy()
