from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from Bio.PDB import MMCIFParser, PDBIO, PDBParser
from Bio.PDB.PDBExceptions import PDBConstructionException, PDBIOException
from Bio.PDB.Atom import Atom
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.Residue import Residue
from Bio.PDB.Structure import Structure
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from nanocofold.utils.seq import AA3TO1

RCSB_ASSEMBLY_URL = "https://files.rcsb.org/download/{pdb_id}-assembly{assembly_id}.cif.gz"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_ATOMS = 250_000
MAX_RESIDUES = 50_000
MAX_CHAINS = 62
MAX_PDB_EXPORT_ATOMS = 99_999
MAX_PDB_CHAIN_RESIDUES = 9_999

STANDARD_AA3 = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}
WATER_NAMES = {"HOH", "WAT", "DOD", "H2O"}
BACKBONE = ("N", "CA", "C", "O")
CANONICAL_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def fetch_pdb_assembly(pdb_id: str, assembly_id: str, output_dir: Path) -> Path:
    """Download an official RCSB prebuilt biological assembly mmCIF gzip."""
    normalized_pdb_id = _validate_pdb_id(pdb_id)
    normalized_assembly_id = _validate_assembly_id(assembly_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{normalized_pdb_id}-assembly{normalized_assembly_id}.cif.gz"
    sidecar = destination.with_suffix(destination.suffix + ".source.json")
    if destination.exists() or sidecar.exists():
        raise FileExistsError(f"출력 파일이 이미 있습니다. 덮어쓰지 않습니다: {destination}")

    url = RCSB_ASSEMBLY_URL.format(pdb_id=normalized_pdb_id, assembly_id=normalized_assembly_id)
    tmp_path: Path | None = None
    started = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=output_dir, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                    total = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise ValueError(
                                f"RCSB 다운로드가 너무 큽니다({total} bytes). PDB ID/assembly 번호를 확인하세요."
                            )
                        if time.monotonic() - started > DOWNLOAD_TIMEOUT_SECONDS:
                            raise TimeoutError("RCSB 다운로드 시간이 초과되었습니다. 네트워크 상태나 PDB ID를 확인하세요.")
                        tmp.write(chunk)
            except urllib.error.HTTPError as exc:
                raise ValueError(
                    f"RCSB assembly 파일을 찾지 못했습니다: {normalized_pdb_id} assembly {normalized_assembly_id} "
                    f"(HTTP {exc.code}). PDB ID와 biological assembly 번호를 확인하세요."
                ) from exc
            except urllib.error.URLError as exc:
                raise ValueError(f"RCSB 다운로드 실패: {exc.reason}. 네트워크 연결을 확인하세요.") from exc
        _validate_gzip_size(tmp_path, MAX_DECOMPRESSED_BYTES)
        os.replace(tmp_path, destination)
        tmp_path = None
        _write_json_no_overwrite(sidecar, {
            "type": "rcsb_pdb_assembly",
            "pdb_id": normalized_pdb_id,
            "assembly_id": normalized_assembly_id,
            "url": url,
            "format": "mmcif.gz",
            "sha256": _sha256(destination),
            "downloaded_at_unix": int(time.time()),
        })
        return destination
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def inspect_structure(path: Path, output_dir: Path, source: dict | None = None, *, model_index: int | None = None) -> dict:
    """Inspect a local PDB/mmCIF input and export a normalized protein-only PDB."""
    path = Path(path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        raise FileNotFoundError(f"구조 파일을 찾을 수 없습니다: {path}")

    parse_path, cleanup_path = _materialize_if_gzip(path, output_dir)
    try:
        structure = _load_structure(parse_path)
        cif = _read_cif_dict(parse_path) if _structure_format(parse_path) == "mmcif" else None
        models = list(structure.get_models())
        model_source = {}
        if model_index is not None:
            if type(model_index) is not int or not 1 <= model_index <= len(models):
                raise ValueError(f"구조 model 번호는 1–{len(models)} 범위의 정수여야 합니다.")
            if sum(1 for _ in structure.get_atoms()) > MAX_ATOMS:
                raise ValueError("전체 model의 원자가 입력 한도를 넘습니다. 필요한 model을 별도 파일로 준비하세요.")
            chosen = models[model_index - 1]
            model_source = {"model_index": model_index, "model_serial_number": chosen.serial_num,
                            "model_count": len(models)}
            selected_structure = Structure("selected_model")
            selected_structure.add(chosen.copy())
            structure = selected_structure
            if cif and "_atom_site.pdbx_PDB_model_num" in cif:
                numbers = _cif_list(cif, "_atom_site.pdbx_PDB_model_num")
                keep = [i for i, number in enumerate(numbers) if number == str(chosen.serial_num)]
                cif = {key: [value[i] for i in keep] if key.startswith("_atom_site.") and isinstance(value, list)
                       and len(value) == len(numbers) else value for key, value in cif.items()}
        _validate_global_structure(structure)

        metadata = _metadata(cif, parse_path)
        from .protein_input import annotate_chain, sequence_metadata
        metadata.update(sequence_metadata(cif, parse_path))
        model = next(structure.get_models())
        normalized = Structure("nanocofold_import")
        normalized_model = Model(0)
        normalized.add(normalized_model)
        context = Structure("nanocofold_context")
        context_model = Model(0)
        context.add(context_model)

        warnings: list[str] = []
        if model_source.get("model_count", 1) > 1:
            warnings.append(f"여러 model 중 {model_index}번을 선택했습니다. 다른 model은 원본에 보존했으며 앙상블 평균으로 취급하지 않습니다.")
        removed: list[dict[str, Any]] = []
        chains: list[dict[str, Any]] = []
        protein_index = 0
        any_context = False

        # 단백질 체인 ID를 파일 순서와 무관하게 먼저 확보한다(context 체인이 ID를 먼저 차지해
        # 같은 ID 가 두 번 배정되어 조회가 실패하는 것을 방지).
        protein_original_ids = [chain.id for chain in model
                                if any(_is_standard_protein_residue(res) for res in chain)]
        used_context_ids: set[str] = {CANONICAL_CHAIN_IDS[i] for i in range(len(protein_original_ids))}

        for original_chain in model:
            standard_residues = [res for res in original_chain if _is_standard_protein_residue(res)]
            nonstandard_polymer = [res for res in original_chain if _is_nonstandard_polymer_residue(res)]
            context_residues = [res for res in original_chain if _is_context_residue(res)]
            hetatm_standard = [res for res in original_chain
                               if res.id[0] != " " and res.resname in STANDARD_AA3]
            water_residues = [res for res in original_chain if _is_water_residue(res)]

            for residue in nonstandard_polymer:
                removed.append(_removed_record("modified_residue", original_chain.id, residue, "표준 20개 아미노산이 아니어서 단백질 서열로 변환하지 않았습니다."))
            for residue in context_residues:
                removed.append(_removed_record("context_separated", original_chain.id, residue, "structure.pdb에서는 제외하고 context.pdb에 보존했습니다."))
            for residue in water_residues:
                removed.append(_removed_record("water_removed", original_chain.id, residue, "물 분자는 structure.pdb/context.pdb에서 제외했습니다."))

            if hetatm_standard:
                # HETATM 으로 기록된 표준 잔기는 컨텍스트로 밀어내지 않고 설계 blocker 로 남긴다.
                for residue in hetatm_standard:
                    removed.append(_removed_record(
                        "hetatm_standard_residue", original_chain.id, residue,
                        "HETATM 으로 기록된 표준 아미노산이라 단백질 체인에 포함하지 않았습니다. "
                        "원본의 TER/HETATM 표기 또는 번호를 확인하세요.",
                    ))
            if not standard_residues:
                if context_residues:
                    context_chain_id = _context_chain_id(original_chain.id, used_context_ids, warnings)
                    if context_chain_id is not None:
                        context_chain = Chain(context_chain_id)
                        for residue in context_residues:
                            context_chain.add(residue.copy())
                        context_model.add(context_chain)
                        any_context = True
                    else:
                        for residue in context_residues:
                            removed.append(_removed_record("context_not_exported", original_chain.id, residue, "PDB context 체인 ID 충돌/길이 문제로 별도 파일에 보존하지 못했습니다."))
                if context_residues or nonstandard_polymer:
                    warnings.append(f"체인 {original_chain.id}: 단백질 표준 잔기가 없어 normalized structure.pdb에서 제외했습니다.")
                continue

            if protein_index >= len(CANONICAL_CHAIN_IDS):
                raise ValueError(f"단백질 체인이 너무 많습니다({protein_index + 1}). 최대 {len(CANONICAL_CHAIN_IDS)}개까지 지원합니다.")
            canonical_id = CANONICAL_CHAIN_IDS[protein_index]
            protein_index += 1
            chain_info = _inspect_chain(original_chain, canonical_id, standard_residues, nonstandard_polymer, metadata)
            annotate_chain(chain_info, metadata)
            if hetatm_standard:
                labels = ", ".join(_residue_original_label(res) for res in hetatm_standard[:5])
                chain_info["blockers"].append(
                    f"체인 {original_chain.id}: HETATM 으로 기록된 표준 아미노산 {len(hetatm_standard)}개({labels})를 "
                    "단백질 체인에서 제외했습니다. 잔기 번호가 밀리므로 원본 번호로 핫스팟을 다시 지정하세요."
                )
            normalized_chain = _normalized_chain(canonical_id, standard_residues)
            normalized_model.add(normalized_chain)
            context_chain = _normalized_chain(canonical_id, standard_residues)
            context_model.add(context_chain)
            used_context_ids.add(canonical_id)
            if context_residues:
                for residue in context_residues:
                    context_chain.add(residue.copy())
                any_context = True
                chain_info["design_blockers"].append(
                    f"체인 {original_chain.id}: 비단백질 context {len(context_residues)}개를 structure.pdb에서 분리했습니다. "
                    "prepare/screen은 가능하지만 de novo 설계 안전성은 SASA/당질/리간드 영향 확인 전까지 주장하지 마세요."
                )
            chains.append(chain_info)

        if not chains:
            raise ValueError("표준 단백질 체인을 찾지 못했습니다. PDB/mmCIF 파일과 assembly 선택을 확인하세요.")

        structure_path = output_dir / "structure.pdb"
        _validate_pdb_export_bounds(normalized, "structure.pdb")
        _save_structure_no_overwrite(normalized, structure_path)
        result_source = {**_source_payload(path, source), **model_source}
        result: dict[str, Any] = {
            "structure_path": str(structure_path),
            "source": result_source,
            "chains": chains,
            "warnings": _dedupe(warnings),
            "removed": removed,
            "sha256": _sha256(path),
        }
        if any_context:
            context_path = output_dir / "context.pdb"
            _validate_pdb_export_bounds(context, "context.pdb")
            _save_structure_no_overwrite(context, context_path)
            result["context_path"] = str(context_path)
            result["warnings"].append(
                "context.pdb에는 structure.pdb와 같은 정규화 단백질 체인/잔기 번호에 비단백질 context를 추가 보존했습니다. SASA/당질 영향은 후속 단계에서 별도 확인하세요."
            )
        if any(chain["blockers"] for chain in chains):
            result["warnings"].append("일부 체인은 설계 입력으로 쓰기 전 확인이 필요한 blockers가 있습니다.")
        if any(chain["design_blockers"] for chain in chains):
            result["warnings"].append("일부 체인은 de novo 설계 전 확인이 필요한 design_blockers가 있습니다.")
        _write_json_no_overwrite(output_dir / "structure_input.source.json", result)
        return result
    finally:
        if cleanup_path is not None and cleanup_path.exists():
            cleanup_path.unlink()


def _validate_pdb_id(pdb_id: str) -> str:
    value = str(pdb_id).strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4}", value):
        raise ValueError(f"PDB ID 형식이 올바르지 않습니다: {pdb_id!r} (예: 1BRS)")
    return value


def _validate_assembly_id(assembly_id: str) -> str:
    value = str(assembly_id).strip()
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError(f"assembly_id는 양의 정수여야 합니다: {assembly_id!r}")
    return value


def _validate_gzip_size(path: Path, limit: int) -> None:
    total = 0
    try:
        with gzip.open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError(f"압축 해제된 mmCIF가 너무 큽니다({total} bytes). 다른 assembly인지 확인하세요.")
    except OSError as exc:
        raise ValueError("RCSB 응답이 올바른 gzip mmCIF가 아닙니다. PDB ID/assembly 번호를 확인하세요.") from exc


def _materialize_if_gzip(path: Path, output_dir: Path) -> tuple[Path, Path | None]:
    if path.suffix.lower() != ".gz":
        return path, None
    inner_suffix = Path(path.stem).suffix.lower()
    if inner_suffix not in {".cif", ".mmcif", ".pdb", ".ent"}:
        raise ValueError(f"지원하지 않는 gzip 내부 형식입니다: {path}")
    with tempfile.NamedTemporaryFile(prefix=f".{path.stem}.", suffix=inner_suffix, dir=output_dir, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        try:
            with gzip.open(path, "rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DECOMPRESSED_BYTES:
                        raise ValueError(f"압축 해제된 구조 파일이 너무 큽니다({total} bytes).")
                    tmp.write(chunk)
        except (OSError, EOFError, ValueError) as exc:
            # 실패 시 출력 폴더에 부분 압축해제 임시 파일을 남기지 않는다.
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"gzip 구조 파일이 손상되었거나 잘렸습니다: {path} ({exc})") from exc
    return tmp_path, tmp_path


def _load_structure(path: Path):
    fmt = _structure_format(path)
    if fmt == "mmcif":
        parser = MMCIFParser(QUIET=True, auth_chains=False, auth_residues=True)
    elif fmt == "pdb":
        parser = PDBParser(QUIET=True, PERMISSIVE=False)
    else:
        raise ValueError(f"지원하지 않는 구조 형식: {path} (pdb/cif/mmcif 지원)")
    try:
        return parser.get_structure(path.stem, str(path))
    except (PDBConstructionException, PDBIOException) as exc:
        raise ValueError(
            f"PDB 파싱 실패: 중복 원자/잔기 또는 잘못된 레코드를 조용히 무시하지 않았습니다. "
            f"입력 파일을 정리한 뒤 다시 시도하세요: {path} ({exc})"
        ) from exc
    except (ValueError, IndexError, KeyError, ZeroDivisionError) as exc:
        # 잔기 번호/좌표가 숫자가 아닌 레코드(예: auth_seq_id '1A', Cartn_x 'notanumber').
        raise ValueError(
            f"PDB/mmCIF 필드를 숫자로 읽지 못했습니다(잔기 번호 또는 좌표 형식 오류): {path} ({exc})"
        ) from exc


def _structure_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return "mmcif"
    if suffix in {".pdb", ".ent"}:
        return "pdb"
    return "unknown"


def _read_cif_dict(path: Path) -> dict[str, Any] | None:
    try:
        return MMCIF2Dict(str(path))
    except Exception:
        return None


def _validate_global_structure(structure) -> None:
    models = list(structure.get_models())
    if len(models) != 1:
        raise ValueError(f"구조 파일에는 model이 정확히 1개여야 합니다. 현재 {len(models)}개입니다.")
    chains = list(models[0])
    residues = list(structure.get_residues())
    atoms = list(structure.get_atoms())
    if len(chains) > MAX_CHAINS:
        raise ValueError(f"체인이 너무 많습니다({len(chains)}). 최대 {MAX_CHAINS}개까지 지원합니다.")
    if len(residues) > MAX_RESIDUES:
        raise ValueError(f"잔기가 너무 많습니다({len(residues)}). 최대 {MAX_RESIDUES}개까지 지원합니다.")
    if len(atoms) > MAX_ATOMS:
        raise ValueError(f"원자가 너무 많습니다({len(atoms)}). 최대 {MAX_ATOMS}개까지 지원합니다.")
    for atom in atoms:
        coord = atom.get_coord()
        if any(not math.isfinite(float(value)) for value in coord):
            raise ValueError(f"유효하지 않은 좌표(NaN/Inf)가 있습니다: atom {atom.get_full_id()}")
        for field, value in (("B-factor", atom.get_bfactor()), ("occupancy", atom.get_occupancy())):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(
                    f"유효하지 않은 {field}(NaN/Inf)가 있습니다: atom {atom.get_full_id()}. "
                    "mmCIF 의 해당 필드를 숫자로 수정하거나 제거하세요."
                )


def _metadata(cif: dict[str, Any] | None, path: Path) -> dict[str, Any]:
    if cif is not None:
        return _mmcif_metadata(cif)
    return _pdb_metadata(path)


def _mmcif_metadata(cif: dict[str, Any]) -> dict[str, Any]:
    label_to_auth: dict[str, str] = {}
    label_to_entity: dict[str, str] = {}
    # auth_asym_id / label_entity_id 는 선택 항목이다. 한 열이 없다고 다른 대응까지 버리지 않도록
    # 각각 따로 묶는다(세 열을 한 번에 zip 하면 하나만 없어도 author 체인·이름이 조용히 사라짐).
    labels = _cif_list(cif, "_atom_site.label_asym_id")
    for label, auth in zip(labels, _cif_list(cif, "_atom_site.auth_asym_id")):
        label_to_auth.setdefault(label, auth)
    for label, entity in zip(labels, _cif_list(cif, "_atom_site.label_entity_id")):
        label_to_entity.setdefault(label, entity)

    # Author numbering can deliberately skip numbers (e.g. antibody numbering).
    # Only an unambiguous polymer sequence index can distinguish that from a
    # genuinely unobserved residue. Never infer or fill missing coordinates.
    residue_sequence_positions: dict[str, dict[str, int | None]] = {}
    insertion_codes = _cif_list(cif, "_atom_site.pdbx_PDB_ins_code") or ["?"] * len(labels)
    for chain, author, sequence, insertion in zip(
        labels, _cif_list(cif, "_atom_site.auth_seq_id"),
        _cif_list(cif, "_atom_site.label_seq_id"), insertion_codes,
    ):
        try:
            original_label = str(int(author))
        except ValueError:
            continue
        if insertion not in {"?", "."}:
            original_label += insertion.strip()
        try:
            sequence_position = int(sequence)
            if sequence_position < 1:
                sequence_position = None
        except ValueError:
            sequence_position = None
        positions = residue_sequence_positions.setdefault(chain, {})
        if original_label not in positions:
            positions[original_label] = sequence_position
        elif positions[original_label] != sequence_position:
            positions[original_label] = None  # Conflicting atom rows stay ambiguous.

    entity_names = {
        entity_id: _unknown_if_blank(name)
        for entity_id, name in zip(_cif_list(cif, "_entity.id"), _cif_list(cif, "_entity.pdbx_description"))
    }
    entity_types = {
        entity_id: polymer_type
        for entity_id, polymer_type in zip(_cif_list(cif, "_entity_poly.entity_id"), _cif_list(cif, "_entity_poly.type"))
    }
    organisms: dict[str, str] = {}
    for key in (
        "_entity_src_gen.pdbx_gene_src_scientific_name",
        "_entity_src_nat.pdbx_organism_scientific",
        "_pdbx_entity_src_syn.organism_scientific",
    ):
        for entity_id, organism in zip(_cif_list(cif, key.replace(key.split(".")[-1], "entity_id")), _cif_list(cif, key)):
            if _unknown_if_blank(organism) != "Unknown":
                organisms.setdefault(entity_id, organism)
    return {
        "label_to_auth": label_to_auth,
        "label_to_entity": label_to_entity,
        "entity_names": entity_names,
        "entity_types": entity_types,
        "organisms": organisms,
        "residue_sequence_positions": residue_sequence_positions,
    }


def _pdb_metadata(path: Path) -> dict[str, Any]:
    mol_by_id: dict[str, dict[str, Any]] = {}
    records: dict[str, list[str]] = {"COMPND": [], "SOURCE": []}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        record = raw[:6].strip()
        if record in records:
            records[record].append(raw[10:].strip())
    # PDB continuation numbers occupy columns 8-10. Fields end at a
    # semicolon, rather than at the end of a physical header line.
    for current_kind, pieces in records.items():
        current_id: str | None = None
        payload = " ".join(pieces)
        for item in payload.split(";"):
            text = item.strip()
            if not text:
                continue
            if ":" not in text:
                continue
            key, value = [part.strip() for part in text.split(":", 1)]
            key = key.upper()
            if key == "MOL_ID":
                current_id = value
                mol_by_id.setdefault(current_id, {"chains": []})
                continue
            if current_id is None:
                continue
            bucket = mol_by_id.setdefault(current_id, {"chains": []})
            if current_kind == "COMPND" and key == "CHAIN":
                bucket["chains"] = [chain.strip() for chain in value.split(",") if chain.strip()]
            elif current_kind == "COMPND" and key == "MOLECULE":
                bucket["name"] = value
            elif current_kind == "SOURCE" and key == "ORGANISM_SCIENTIFIC":
                bucket["organism"] = value
    by_chain: dict[str, dict[str, str]] = {}
    for data in mol_by_id.values():
        for chain in data.get("chains", []):
            by_chain[chain] = {
                "name": _unknown_if_blank(data.get("name")),
                "organism": _unknown_if_blank(data.get("organism")),
            }
    return {"pdb_by_chain": by_chain}


def _cif_list(cif: dict[str, Any], key: str) -> list[str]:
    value = cif.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, "?"):
        return []
    return [str(value)]


def _inspect_chain(original_chain: Chain, canonical_id: str, residues: list[Residue], modified: list[Residue], metadata: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    blockers: list[str] = []
    design_blockers: list[str] = []
    residue_rows: list[dict[str, Any]] = []
    sequence: list[str] = []

    if modified:
        blockers.append(
            f"체인 {original_chain.id}: MSE/SEC/PYL 등 비표준 또는 수정 잔기 {len(modified)}개를 표준 아미노산으로 변환하지 않았습니다."
        )

    altlocs = _alternate_location_warnings(original_chain, residues)
    warnings.extend(altlocs)

    last_number: int | None = None
    last_sequence_position: int | None = None
    sequence_positions = metadata.get("residue_sequence_positions", {}).get(original_chain.id, {})
    for position, residue in enumerate(residues, start=1):
        label = _residue_original_label(residue)
        sequence_position = sequence_positions.get(label)
        if residue.is_disordered() == 2:
            # 점돌연변이: 같은 번호에 서로 다른 잔기명. BioPython 은 파일 순서로 하나를 택하므로 서열이 모호하다.
            blockers.append(
                f"체인 {original_chain.id} 원래 잔기 {label}: 번호 하나에 서로 다른 잔기명 "
                f"{sorted(str(n).strip() for n in residue.disordered_get_id_list())}이 기록되어 있습니다. "
                "어느 잔기인지 원본에서 확인하세요."
            )
        missing = [name for name in BACKBONE if name not in residue]
        if missing:
            blockers.append(f"체인 {original_chain.id} 원래 잔기 {label}: backbone 원자 누락 {missing}")
        author_gap = last_number is not None and int(residue.id[1]) > last_number + 1
        if last_sequence_position is not None and sequence_position is not None:
            if sequence_position > last_sequence_position + 1:
                blockers.append(
                    f"체인 {original_chain.id}: 내부 label_seq_id gap {last_sequence_position}→{sequence_position} "
                    f"(원래 잔기 번호 {last_number}→{int(residue.id[1])})"
                )
            elif sequence_position <= last_sequence_position:
                blockers.append(f"체인 {original_chain.id}: label_seq_id 순서가 연속적이지 않습니다: {last_sequence_position}→{sequence_position}")
            elif author_gap:
                warnings.append(
                    f"체인 {original_chain.id}: 원래 잔기 번호 {last_number}→{int(residue.id[1])}는 "
                    "건너뛰지만 label_seq_id는 연속적입니다. 원래 번호를 보존했습니다."
                )
        elif author_gap and not str(residue.id[2]).strip():
            blockers.append(f"체인 {original_chain.id}: 내부 잔기 번호 gap {last_number}→{int(residue.id[1])}")
        last_number = int(residue.id[1])
        last_sequence_position = sequence_position
        ca = None
        if "CA" in residue:
            ca = [round(float(value), 3) for value in residue["CA"].get_coord()]
        sequence.append(AA3TO1[residue.resname])
        residue_rows.append({
            "position": position,
            "original_number": int(residue.id[1]),
            "original_label": _residue_original_label(residue),
            "insertion_code": str(residue.id[2]).strip(),
            "aa": AA3TO1[residue.resname],
            "ca": ca,
        })

    if _metadata_polymer_type(original_chain.id, metadata).lower() not in {"", "polypeptide(l)"}:
        blockers.append(f"체인 {original_chain.id}: mmCIF entity가 polypeptide가 아니어서 mixed-polymer 여부를 확인해야 합니다.")
    return {
        "id": canonical_id,
        "original_id": original_chain.id,
        "entity_id": metadata.get("label_to_entity", {}).get(original_chain.id),
        "polymer_type": _metadata_polymer_type(original_chain.id, metadata),
        "author_chain": _author_chain(original_chain.id, metadata),
        "name": _chain_name(original_chain.id, metadata),
        "organism": _chain_organism(original_chain.id, metadata),
        "sequence": "".join(sequence),
        "length": len(sequence),
        "residues": residue_rows,
        "warnings": _dedupe(warnings),
        "blockers": _dedupe(blockers),
        "design_blockers": _dedupe(design_blockers),
    }


def enrich_rcsb_metadata(info: dict, pdb_id: str) -> None:
    """Assembly files may omit species. Fetch bounded source-entity metadata.

    This optional lookup never changes coordinates or sequences. Failure leaves
    species explicitly unknown; expression-host species is never substituted.
    """
    from concurrent.futures import ThreadPoolExecutor

    pdb_id = _validate_pdb_id(pdb_id)
    entities = sorted({str(c.get("entity_id")) for c in info["chains"]
                       if str(c.get("entity_id", "")).isdigit()})
    if len(entities) > 62:
        raise ValueError("생물종 조회 대상 entity가 너무 많습니다.")

    def fetch(entity_id):
        url = f"https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{entity_id}"
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("entity 응답 크기 초과")
            data = json.loads(raw)
            if data.get("rcsb_id") != f"{pdb_id}_{entity_id}":
                raise ValueError("entity 식별자 불일치")
            names = sorted({str(item.get("ncbi_scientific_name") or item.get("scientific_name"))
                            for item in data.get("rcsb_entity_source_organism", [])
                            if item.get("ncbi_scientific_name") or item.get("scientific_name")})
            return entity_id, "; ".join(names), url, hashlib.sha256(raw).hexdigest(), None
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            return entity_id, "", url, None, str(exc)

    if not entities:
        return
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(fetch, entities))
    provenance = []
    for entity_id, organism, url, digest, error in records:
        if organism:
            for chain in info["chains"]:
                if str(chain.get("entity_id")) == entity_id:
                    chain["organism"] = organism
        if error:
            info.setdefault("warnings", []).append(
                f"entity {entity_id}의 생물종 조회 실패: 정보가 없는 경우 미상으로 표시합니다. ({error})"
            )
        provenance.append({"entity_id": entity_id, "url": url, "sha256": digest,
                           "organism": organism or "Unknown", "status": "error" if error else "retrieved"})
    info["source"]["entity_metadata"] = provenance


def _normalized_chain(canonical_id: str, residues: list[Residue]) -> Chain:
    chain = Chain(canonical_id)
    for position, residue in enumerate(residues, start=1):
        new_residue = Residue((" ", position, " "), residue.resname, residue.segid)
        serial = 1
        for atom in residue.get_atoms():
            copied = Atom(
                atom.name,
                atom.get_coord().copy(),
                atom.get_bfactor(),
                atom.get_occupancy(),
                atom.get_altloc(),
                atom.get_fullname(),
                serial,
                element=atom.element,
            )
            serial += 1
            new_residue.add(copied)
        chain.add(new_residue)
    return chain


def _validate_pdb_export_bounds(structure: Structure, label: str) -> None:
    atoms = list(structure.get_atoms())
    if len(atoms) > MAX_PDB_EXPORT_ATOMS:
        raise ValueError(
            f"{label}로 내보낼 원자가 너무 많습니다({len(atoms)}). PDB 형식 한계({MAX_PDB_EXPORT_ATOMS})를 넘지 않도록 체인을 줄이거나 mmCIF 경로를 사용하세요."
        )
    model = next(structure.get_models())
    for chain in model:
        residues = list(chain)
        if len(residues) > MAX_PDB_CHAIN_RESIDUES:
            raise ValueError(
                f"{label} 체인 {chain.id} 잔기가 너무 많습니다({len(residues)}). PDB 체인 잔기 번호 한계({MAX_PDB_CHAIN_RESIDUES})를 넘지 않도록 분할하세요."
            )


def _alternate_location_warnings(original_chain: Chain, residues: list[Residue]) -> list[str]:
    records: list[str] = []
    for residue in residues:
        if residue.is_disordered() == 2:
            # DisorderedResidue(=2): 같은 번호에 서로 다른 잔기명(점돌연변이). 원자 수준 altloc(=1)과 구분한다.
            children = ",".join(sorted(str(name).strip() for name in residue.disordered_get_id_list()))
            records.append(
                f"{_residue_original_label(residue)}:잔기수준-disorder({children}). "
                "점돌연변이이므로 어느 잔기인지 원본에서 확인하세요."
            )
        for atom in residue.get_atoms():
            if atom.is_disordered():
                altloc = str(atom.get_altloc()).strip() or "blank"
                records.append(f"{_residue_original_label(residue)}:{atom.get_name()}={altloc}")
    if not records:
        return []
    shown = ", ".join(records[:8])
    extra = "" if len(records) <= 8 else f" 외 {len(records) - 8}개"
    return [
        f"체인 {original_chain.id}: alternate atom location {len(records)}개가 있습니다. "
        f"BioPython이 선택한 altloc: {shown}{extra}. 숨은 좌표 선택이므로 원본 확인을 권장합니다."
    ]


def _is_standard_protein_residue(residue: Residue) -> bool:
    return residue.id[0] == " " and residue.resname in STANDARD_AA3


def _is_nonstandard_polymer_residue(residue: Residue) -> bool:
    from Bio.PDB.Polypeptide import is_aa
    from .utils.pdb_io import has_peptide_backbone
    return (residue.resname not in STANDARD_AA3 and residue.resname not in WATER_NAMES
            and (residue.id[0] == " " or is_aa(residue, standard=False) or has_peptide_backbone(residue)))


def _is_context_residue(residue: Residue) -> bool:
    return residue.id[0] != " " and residue.resname not in WATER_NAMES and not _is_nonstandard_polymer_residue(residue)


def _is_water_residue(residue: Residue) -> bool:
    return residue.resname in WATER_NAMES


def _context_chain_id(original_id: str, used: set[str], warnings: list[str]) -> str | None:
    if len(original_id) == 1 and original_id not in used:
        used.add(original_id)
        return original_id
    for candidate in CANONICAL_CHAIN_IDS:
        if candidate not in used:
            used.add(candidate)
            if len(original_id) != 1:
                warnings.append(f"context 체인 {original_id}: PDB 형식 제약 때문에 context.pdb에서 {candidate}로 재지정했습니다.")
            else:
                warnings.append(f"context 체인 {original_id}: 단백질 chain ID와 충돌해 context.pdb에서 {candidate}로 재지정했습니다.")
            return candidate
    warnings.append(f"context 체인 {original_id}: 사용 가능한 PDB chain ID가 없어 context.pdb에 보존하지 못했습니다.")
    return None


def _removed_record(kind: str, chain_id: str, residue: Residue, reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "chain": chain_id,
        "residue": residue.resname,
        "original_number": int(residue.id[1]),
        "original_label": _residue_original_label(residue),
        "insertion_code": str(residue.id[2]).strip(),
        "reason": reason,
    }


def _residue_original_label(residue: Residue) -> str:
    insertion = str(residue.id[2]).strip()
    return f"{int(residue.id[1])}{insertion}"


def _author_chain(chain_id: str, metadata: dict[str, Any]) -> str:
    return _unknown_if_blank(metadata.get("label_to_auth", {}).get(chain_id, chain_id))


def _chain_name(chain_id: str, metadata: dict[str, Any]) -> str:
    if "pdb_by_chain" in metadata:
        return _unknown_if_blank(metadata["pdb_by_chain"].get(chain_id, {}).get("name"))
    entity = metadata.get("label_to_entity", {}).get(chain_id)
    return _unknown_if_blank(metadata.get("entity_names", {}).get(entity))


def _chain_organism(chain_id: str, metadata: dict[str, Any]) -> str:
    if "pdb_by_chain" in metadata:
        return _unknown_if_blank(metadata["pdb_by_chain"].get(chain_id, {}).get("organism"))
    entity = metadata.get("label_to_entity", {}).get(chain_id)
    return _unknown_if_blank(metadata.get("organisms", {}).get(entity))


def _metadata_polymer_type(chain_id: str, metadata: dict[str, Any]) -> str:
    entity = metadata.get("label_to_entity", {}).get(chain_id)
    return str(metadata.get("entity_types", {}).get(entity, ""))


def _unknown_if_blank(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text in {".", "?"}:
        return "Unknown"
    return text


def _source_payload(path: Path, source: dict | None) -> dict:
    payload = dict(source or {})
    payload.setdefault("type", "local_file")
    payload.setdefault("path", str(path))
    payload.setdefault("format", "mmcif.gz" if path.suffix.lower() == ".gz" else _structure_format(path))
    payload.setdefault("sha256", _sha256(path))
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_structure_no_overwrite(structure: Structure, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"출력 파일이 이미 있습니다. 덮어쓰지 않습니다: {path}")
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            writer = PDBIO()
            writer.set_structure(structure)
            writer.save(str(tmp_path))
        except PDBIOException as exc:
            raise ValueError(
                f"PDB 변환 실패: 입력 원자 필드를 PDB 형식으로 기록할 수 없습니다. "
                f"좌표·B-factor·occupancy 및 원자 레코드를 확인하세요: {path} ({exc})"
            ) from exc
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _write_json_no_overwrite(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"출력 파일이 이미 있습니다. 덮어쓰지 않습니다: {path}")
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, mode="w", encoding="utf-8", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
    try:
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
