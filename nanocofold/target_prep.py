from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pandas as pd

from . import hotspots as hotspots_mod
from . import mock as mock_mod
from . import provenance
from .config import cfg_get, cfg_seed, resolve_path
from .utils import msa as msa_utils
from .utils import pdb_io, seq as seq_utils, shell
from .utils.misc import to_native
from .utils.tool_args import BOLTZ_MANAGED, CHAI_MANAGED, validate_extra_args


def chai_target_settings(cfg: dict) -> dict:
    values = {"seed": cfg_seed(cfg, "structure.chai1.seed")}
    for key, default, minimum, maximum in (("num_trunk_recycles", 3, 1, 20),
                                           ("num_diffn_timesteps", 200, 2, 1000),
                                           ("num_diffn_samples", 1, 1, 16)):
        value = cfg_get(cfg, f"structure.chai1.{key}", default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"structure.chai1.{key} must be an integer {minimum}..{maximum}")
        values[key] = value
    return values


def resolve_target_sequence(cfg: dict) -> str:
    sequence = cfg_get(cfg, "target.sequence") or ""
    if sequence:
        sequence = seq_utils.clean_sequence(sequence)
        if not sequence:
            raise ValueError("target.sequence 가 비어 있습니다.")
        return sequence
    fasta = resolve_path(cfg, cfg_get(cfg, "target.fasta"))
    if fasta and fasta.exists():
        records = seq_utils.read_fasta(fasta, candidate_ids=False)
        if not records:
            raise ValueError(f"FASTA에서 서열을 읽지 못했습니다: {fasta}")
        if len(records) != 1:
            raise ValueError("단일 표적 FASTA에는 서열을 1개만 넣으세요. 여러 표적은 배치 모드를 사용하세요.")
        return seq_utils.clean_sequence(next(iter(records.values())))
    pdb_path = resolve_path(cfg, cfg_get(cfg, "target.pdb"))
    if pdb_path and pdb_path.exists():
        chain_id = str(cfg_get(cfg, "target.chain", "A"))
        structure = pdb_io.load_structure(pdb_path)
        chain = pdb_io.get_chain(structure, chain_id)
        pdb_io.validate_polymer_residues(chain)
        sequence = pdb_io.chain_sequence(chain)
        if sequence:
            return sequence
        raise ValueError(f"target.pdb 체인 {chain_id} 에서 서열을 읽지 못했습니다: {pdb_path}")
    raise ValueError("target.sequence, target.fasta 또는 target.pdb 중 하나를 지정하세요.")


def _frozen_msa_record(value: str | Path | None, sequence: str) -> dict:
    if value in (None, "", "empty"):
        return {"path": "empty", "sha256": None}
    path = Path(value).resolve()
    if not path.is_file():
        raise ValueError("Predicted target frozen MSA is missing")
    msa_utils.validate_target_a3m(path, sequence)
    return {"path": str(path), "sha256": provenance.file_digest(path)}


def _raw_prediction_identity(cfg: dict, *, mock: bool, msa: dict) -> dict:
    if type(mock) is not bool:
        raise ValueError("Predicted target mock mode must be true/false")
    if cfg_get(cfg, "target.pdb") not in (None, ""):
        raise ValueError("Predicted target reuse cannot be used with target.pdb")
    provider = "mock" if mock else str(cfg_get(cfg, "structure.provider", "none")).lower()
    if provider not in {"mock", "boltz2", "boltz", "chai1"}:
        raise ValueError("Raw prediction reuse requires a supported predicted target provider")
    sequence = resolve_target_sequence(cfg)
    auxiliary = _parse_aux_chains(cfg)
    chains = {"A": sequence}
    chains.update({chr(ord("B") + index): item["sequence"] for index, item in enumerate(auxiliary)})
    runtime, arguments = {}, {"requested_seed": cfg_seed(cfg, "run.seed")}
    if provider in {"boltz2", "boltz"}:
        env = resolve_path(cfg, cfg_get(cfg, "structure.boltz2.env"))
        extra = validate_extra_args(cfg_get(cfg, "structure.boltz2.extra_args", []),
                                    setting="structure.boltz2.extra_args", managed=BOLTZ_MANAGED)
        arguments.update(seed=cfg_seed(cfg, "structure.boltz2.seed"),
            recycling_steps=int(cfg_get(cfg, "structure.boltz2.recycling_steps", 3)),
            diffusion_samples=int(cfg_get(cfg, "structure.boltz2.diffusion_samples", 1)), extra_args=extra)
        runtime = provenance.runtime_identity(cfg, "boltz2", env=env,
                                               env_vars=cfg_get(cfg, "structure.boltz2.env_vars", {}) or {})
    elif provider == "chai1":
        mode = str(cfg_get(cfg, "structure.chai1.msa_mode", "single"))
        if mode != "single":
            raise ValueError("Chai target raw reuse requires single mode; server MSA has no frozen target receipt")
        env = resolve_path(cfg, cfg_get(cfg, "structure.chai1.env"))
        extra = validate_extra_args(cfg_get(cfg, "structure.chai1.extra_args", []),
                                    setting="structure.chai1.extra_args", managed=CHAI_MANAGED, allow_abbrev=True)
        arguments.update(msa_mode=mode, extra_args=extra, **chai_target_settings(cfg))
        runtime = provenance.runtime_identity(cfg, "chai1", env=env)
    arguments["argument_file_sha256"] = {
        str(Path(value).resolve()): provenance.file_digest(Path(value))
        for value in arguments.get("extra_args", []) if Path(str(value)).is_file()
    }
    sources = {"target_prep.py": Path(__file__), "screen.py": Path(__file__).with_name("screen.py"),
               "pdb_io.py": Path(pdb_io.__file__), "msa.py": Path(msa_utils.__file__)}
    return {"schema_version": 1, "mock": mock, "provider": provider, "chains": chains,
            "aux_chains": auxiliary, "requested_target_chain": str(cfg_get(cfg, "target.chain", "A")),
            "provider_arguments": arguments, "msa_sha256": msa["sha256"],
            "msa_settings": cfg_get(cfg, "structure.msa", {}) or {}, "runtime": runtime,
            "source_sha256": {name: provenance.file_digest(path) for name, path in sources.items()}}


def raw_prediction_receipt(cfg: dict, target: dict, *, mock: bool = False) -> dict:
    """Bind a prepared prediction's raw coordinates to its exact folding inputs.

    This caller-managed receipt is an integrity contract, not evidence that an
    arbitrary supplied file was produced by inference. Pass only the result of
    prepare_target. Hotspots and truncation are deliberately excluded: each arm
    must prepare those from the shared, full-length raw prediction separately.
    """
    sequence = resolve_target_sequence(cfg)
    msa = _frozen_msa_record(target.get("target_msa"), sequence)
    identity = _raw_prediction_identity(cfg, mock=mock, msa=msa)
    qc = target.get("qc") or {}
    if (target.get("sequence") != sequence or qc.get("provider") != identity["provider"]
            or qc.get("confidence_metric") != "plddt"):
        raise ValueError("Prepared target provider, sequence or confidence kind differs from prediction inputs")
    source = target.get("raw_structure")
    if not source or not Path(source).is_file():
        raise ValueError("Prepared raw target prediction is missing")
    source = Path(source).resolve()
    validate_generated_target_identity(source, identity["chains"])
    return {"schema_version": 1, "source_kind": "predicted_target", "identity": identity,
            "identity_sha256": provenance.fingerprint(identity), "raw_structure": str(source),
            "raw_structure_sha256": provenance.file_digest(source), "frozen_msa": msa}


def _verified_raw_prediction(cfg: dict, receipt: dict, *, mock: bool) -> dict:
    if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
            or receipt.get("schema_version") != 1 or receipt.get("source_kind") != "predicted_target"
            or not isinstance(receipt.get("identity"), dict) or not isinstance(receipt.get("frozen_msa"), dict)):
        raise ValueError("Malformed raw target prediction receipt")
    msa = receipt["frozen_msa"]
    if not isinstance(msa.get("path"), str) or set(msa) != {"path", "sha256"}:
        raise ValueError("Malformed predicted target frozen MSA receipt")
    checked_msa = _frozen_msa_record(msa["path"], resolve_target_sequence(cfg))
    if checked_msa != msa:
        raise ValueError("Predicted target frozen MSA hash or path changed")
    identity = _raw_prediction_identity(cfg, mock=mock, msa=checked_msa)
    if (receipt["identity"] != identity
            or receipt.get("identity_sha256") != provenance.fingerprint(identity)
            or receipt.get("identity_sha256") != provenance.fingerprint(receipt["identity"])):
        raise ValueError("Raw target prediction inputs, provider, mock mode or runtime changed")
    source = receipt.get("raw_structure")
    if not isinstance(source, str) or not Path(source).is_absolute() or not Path(source).is_file():
        raise ValueError("Predicted raw target coordinate file is missing or invalid")
    source = Path(source)
    if receipt.get("raw_structure_sha256") != provenance.file_digest(source):
        raise ValueError("Predicted raw target coordinate hash changed")
    validate_generated_target_identity(source, identity["chains"])
    return {"raw_structure": source, "target_msa": msa["path"]}


def prepare_target(
    cfg: dict, run_dir: Path, mock: bool = False, dry_run: bool = False,
    *, predicted_raw_receipt: dict | None = None,
) -> dict:
    """타깃 준비. dry_run은 기존 성공 산출물/QC를 바이트 단위로 보존한다(J01)."""
    from .input_review import validate_reviewed_input
    validate_reviewed_input(cfg)
    if not dry_run:
        return _prepare_target_impl(cfg, run_dir, mock=mock, dry_run=False,
                                    predicted_raw_receipt=predicted_raw_receipt)
    phase_dir = run_dir / "01_target"
    phase_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {item.name: item.read_bytes() for item in phase_dir.iterdir() if item.is_file()}
    try:
        return _prepare_target_impl(cfg, run_dir, mock=mock, dry_run=True,
                                    predicted_raw_receipt=predicted_raw_receipt)
    finally:
        for item in list(phase_dir.iterdir()):
            if item.is_file() and item.name not in snapshot:
                item.unlink()
        for name, data in snapshot.items():
            (phase_dir / name).write_bytes(data)


def _prepare_target_impl(
    cfg: dict, run_dir: Path, mock: bool = False, dry_run: bool = False,
    *, predicted_raw_receipt: dict | None = None,
) -> dict:
    """타깃 구조 확보 → 핫스팟 결정/검증 → truncation → 준비된 PDB/QC 생성.

    핫스팟 번호 체계(canonical):
      - hotspot_labels / hotspot_pdb_labels: 구조 PDB 잔기번호 라벨 (설계·truncation·QC용)
      - hotspot_seq_positions / hotspot_seq_labels: 1-based 서열 인덱스 (restraint·pocket·스코어링용)
    구조가 있으면 숫자 핫스팟은 PDB 번호로, 구조가 없으면 서열 위치로 해석한다.
    'pdb142'/'seq135' 접두사로 항상 명시할 수 있다.
    """
    verified_raw = (_verified_raw_prediction(cfg, predicted_raw_receipt, mock=mock)
                    if predicted_raw_receipt is not None else None)
    phase_dir = run_dir / "01_target"
    phase_dir.mkdir(parents=True, exist_ok=True)

    sequence = resolve_target_sequence(cfg)
    chain_id = str(cfg_get(cfg, "target.chain", "A"))
    provider = "mock" if mock else str(cfg_get(cfg, "structure.provider", "none")).lower()
    warnings: list[str] = []
    mapping_qc: dict = {}
    ambiguity_hint = seq_utils.nucleotide_ambiguity_hint(sequence)
    if ambiguity_hint:
        warnings.append(ambiguity_hint)
    aux_chains = _parse_aux_chains(cfg)
    if aux_chains:
        warnings.append(
            f"보조 체인 {len(aux_chains)}개를 복합체 문맥으로 사용합니다: "
            + ", ".join(str(entry.get("name", "?")) for entry in aux_chains)
        )

    provided_pdb = resolve_path(cfg, cfg_get(cfg, "target.pdb"))
    if provided_pdb and provided_pdb.is_file():
        provided_structure = pdb_io.load_structure(provided_pdb)
        for provided_chain in next(provided_structure.get_models()):
            pdb_io.validate_polymer_residues(provided_chain)
        # Reject incompatible native inputs before MSA requests or inference.
        _structure_index_maps(provided_pdb, chain_id, sequence, warnings=warnings, mapping_qc=mapping_qc)
    sequence_only = provider in {"none", "sequence"} and not (provided_pdb and provided_pdb.exists())

    if verified_raw is None:
        target_msa, msa_warnings = msa_utils.ensure_a3m(
            sequence, cfg_get(cfg, "structure.msa", {}) or {}, run_dir=phase_dir, dry_run=dry_run
        )
    else:
        target_msa, msa_warnings = verified_raw["target_msa"], []
    target_msa = _snapshot_target_msa(target_msa, phase_dir, dry_run)
    if verified_raw is not None and _frozen_msa_record(target_msa, sequence)["sha256"] != predicted_raw_receipt["frozen_msa"]["sha256"]:
        raise ValueError("Predicted target frozen MSA changed while copying")
    warnings.extend(msa_warnings)

    hotspot_spec = cfg_get(cfg, "target.hotspots", "auto")
    # An explicitly empty list means unrestricted exploration. Legacy auto/null
    # values continue to request structure-derived suggestions.
    no_hotspots = isinstance(hotspot_spec, list) and not hotspot_spec
    explicit_entries = hotspots_mod.parse_hotspot_spec(hotspot_spec, chain_id)
    if no_hotspots and bool(cfg_get(cfg, "truncate.enabled", False)):
        raise ValueError("Hotspot 미지정 실행은 hotspot 중심 truncation을 사용할 수 없습니다.")
    if sequence_only:
        return _prepare_sequence_only(
            cfg, phase_dir, sequence, chain_id, warnings, aux_chains, dry_run, explicit_entries, target_msa
        )

    if verified_raw is None:
        raw_structure = _acquire_structure(
            cfg, phase_dir, sequence, provider, dry_run=dry_run, warnings=warnings, aux_chains=aux_chains, msa_path=target_msa
        )
    else:
        source = verified_raw["raw_structure"]
        raw_structure = phase_dir / ("target_raw" + source.suffix)
        if source.resolve() != raw_structure.resolve():
            shutil.copy2(source, raw_structure)
        # Verify the copied bytes as well as the source before using coordinates.
        if provenance.file_digest(raw_structure) != predicted_raw_receipt["raw_structure_sha256"]:
            raise ValueError("Predicted target changed while copying its raw coordinates")
    chain_id, chain_warnings = _validate_target_chain(raw_structure, chain_id, from_provider=provided_pdb is None)
    warnings.extend(chain_warnings)
    raw_model = pdb_io.load_structure(raw_structure)
    for raw_chain in next(raw_model.get_models()):
        pdb_io.validate_polymer_residues(raw_chain)
    modified = [pdb_io.residue_label(res, raw_chain.id) + ":" + res.resname
                for raw_chain in next(raw_model.get_models()) for res in pdb_io.iter_residues(raw_chain)
                if res.id[0] != " "]
    if modified:
        warnings.append("매핑 가능한 변형 잔기의 원본 좌표를 보존합니다: " + ", ".join(modified))

    conservation: list[float] | None = None
    if target_msa and target_msa != "empty" and Path(target_msa).exists():
        try:
            conservation = msa_utils.column_conservation(target_msa, sequence)
            if len(conservation) != len(sequence):
                conservation = None
                warnings.append("MSA 보존도 길이가 서열과 맞지 않아 자동 핫스팟에서 제외합니다.")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"MSA 보존도 계산 실패 → 보존도 없이 진행: {exc}")

    experimental = provided_pdb is not None
    confidence_metric = "bfactor" if experimental else "plddt"
    label_to_seq, seq_to_label, seq_positions_map = _structure_index_maps(
        raw_structure, chain_id, sequence, warnings=warnings, mapping_qc=mapping_qc)
    table = hotspots_mod.hotspot_table(
        raw_structure,
        chain_id,
        conservation=conservation,
        seq_positions=seq_positions_map,
        confidence_kind=confidence_metric,
    )
    table.to_csv(phase_dir / "hotspot_table.csv", index=False)

    auto_sets: list[dict] = []
    if no_hotspots:
        pdb_labels, seq_positions, seq_labels = [], [], []
        hotspot_sets = [{"set_id": "none", "residues": [], "residues_seq": [], "score": None}]
        warnings.append("Hotspot을 지정하지 않았습니다. 결합 위치를 제한하지 않고 탐색합니다.")
    elif explicit_entries:
        pdb_labels, seq_positions, seq_labels, resolve_warnings = _resolve_explicit_hotspots(
            explicit_entries, chain_id, sequence, label_to_seq, seq_to_label
        )
        warnings.extend(resolve_warnings)
        hotspot_sets = [{"set_id": "user", "residues": pdb_labels, "residues_seq": seq_positions, "score": None}]
    else:
        auto_sets = hotspots_mod.auto_hotspot_sets(table, cfg_get(cfg, "hotspots.auto", {}) or {}, raw_structure, chain_id)
        if not auto_sets:
            raise ValueError("자동 핫스팟 후보를 찾지 못했습니다. target.hotspots 를 직접 지정하세요.")
        preferred = cfg_get(cfg, "target.hotspot_set")
        if preferred:
            chosen = next((s for s in auto_sets if s["set_id"] == preferred), None)
            if chosen is None:
                available = ", ".join(str(s["set_id"]) for s in auto_sets)
                raise ValueError(
                    f"target.hotspot_set='{preferred}' 후보가 없습니다. 사용 가능: {available}"
                )
        else:
            chosen = auto_sets[0]
        hotspot_sets = [chosen] + [s for s in auto_sets if s is not chosen]
        pdb_labels = list(chosen["residues"])
        seq_positions = [int(value) for value in (chosen.get("residues_seq") or [])]
        if not seq_positions:
            seq_positions = [label_to_seq[label] for label in pdb_labels if label in label_to_seq]
        seq_labels = [f"{chain_id}:{position}" for position in seq_positions]

    chosen = hotspot_sets[0]
    validation = {"records": [], "warnings": []} if no_hotspots else hotspots_mod.validate_hotspots(
        raw_structure,
        chain_id,
        pdb_labels,
        enforce_plddt=not experimental,
        confidence_kind=confidence_metric,
    )
    warnings.extend(validation["warnings"])

    prepared = raw_structure
    truncate_info = {"enabled": False}
    if bool(cfg_get(cfg, "truncate.enabled", False)):
        prepared = phase_dir / "target_prepared.pdb"
        truncate_info = {"enabled": True}
        truncate_info.update(_run_truncate(cfg, raw_structure, chain_id, pdb_labels, prepared))
        warnings.extend(f"truncation: 핫스팟 {label} 누락" for label in truncate_info.get("missing_hotspots", []))
    else:
        prepared = _to_pdb_from_file(Path(raw_structure), phase_dir / "target_prepared.pdb")

    confidence = pdb_io.parse_plddt(pdb_io.load_structure(prepared), normalize=not experimental)
    hotspot_confidence = [confidence.get(label, float("nan")) for label in pdb_labels]
    qc = {
        "sequence_length": len(sequence),
        "sequence_structure_mapping": mapping_qc,
        "chain_id": chain_id,
        "provider": provider,
        "confidence_metric": confidence_metric,
        "raw_structure": str(raw_structure),
        "prepared_structure": str(prepared),
        "mean_plddt": round(float(pd.Series(list(confidence.values())).mean()), 1) if confidence and not experimental else None,
        "mean_bfactor": round(float(pd.Series(list(confidence.values())).mean()), 1) if confidence and experimental else None,
        "hotspot_mean_plddt": round(float(pd.Series(hotspot_confidence).dropna().mean()), 1) if hotspot_confidence and not experimental else None,
        "hotspot_mean_bfactor": round(float(pd.Series(hotspot_confidence).dropna().mean()), 1) if hotspot_confidence and experimental else None,
        "hotspot_set_id": chosen["set_id"],
        "hotspots": pdb_labels,
        "hotspot_seq_positions": seq_positions,
        "hotspot_validation": validation["records"],
        "truncation": truncate_info,
        "warnings": warnings,
    }
    if predicted_raw_receipt is not None:
        qc["raw_prediction_reuse"] = {
            "identity_sha256": predicted_raw_receipt["identity_sha256"],
            "raw_structure_sha256": predicted_raw_receipt["raw_structure_sha256"],
            "source_raw_structure": predicted_raw_receipt["raw_structure"],
            "confidence_kind": "plddt",
        }

    hotspot_rows = []
    for entry in hotspot_sets:
        entry_labels = list(entry.get("residues") or [])
        entry_positions = list(entry.get("residues_seq") or [])
        for index, residue in enumerate(entry_labels):
            position = entry_positions[index] if index < len(entry_positions) else None
            hotspot_rows.append({"set_id": entry["set_id"], "residue": residue, "seq_position": position, "score": entry.get("score")})
    pd.DataFrame(hotspot_rows, columns=["set_id", "residue", "seq_position", "score"]).to_csv(phase_dir / "hotspot_sets.csv", index=False)
    (phase_dir / "target_qc.json").write_text(
        json.dumps(to_native(qc), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (phase_dir / "target_sequence.fasta").write_text(f">{cfg_get(cfg, 'target.name', 'target')}\n{sequence}\n", encoding="utf-8")

    return {
        "sequence": sequence,
        "chain_id": chain_id,
        "raw_structure": Path(raw_structure),
        "structure": Path(prepared),
        "target_msa": target_msa,
        "aux_chains": aux_chains,
        "hotspot_labels": pdb_labels,
        "hotspot_mode": "none" if no_hotspots else "specified",
        "hotspot_pdb_labels": pdb_labels,
        "hotspot_seq_positions": seq_positions,
        "hotspot_seq_labels": seq_labels,
        "hotspot_sets": hotspot_sets,
        "auto_sets": auto_sets,
        "qc": qc,
        "warnings": warnings,
    }


def _structure_index_maps(
    structure_path: str | Path,
    chain_id: str,
    input_sequence: str | None = None,
    *, warnings: list[str] | None = None, mapping_qc: dict | None = None,
) -> tuple[dict[str, int], dict[int, str], list[int]]:
    """구조 잔기(PDB 번호) → 입력 서열 위치 매핑.

    입력 서열이 구조 서열과 다르면 PairwiseAligner로 정렬해 매핑한다(F09).
    반환: (label_to_seq, seq_to_label, seq_positions_ordered)
    """
    structure = pdb_io.load_structure(structure_path)
    chain = pdb_io.get_chain(structure, chain_id)
    pdb_io.validate_polymer_residues(chain)
    labels = [pdb_io.residue_label(res, chain_id) for res in pdb_io.iter_residues(chain)]
    structure_sequence = pdb_io.chain_sequence(chain)
    if not structure_sequence:
        raise ValueError("제공 구조에 매핑 가능한 단백질 서열이 없습니다.")
    if input_sequence is not None:
        input_sequence = seq_utils.clean_sequence(input_sequence)

    if mapping_qc is not None:
        mapping_qc["coverage_definition"] = "input positions aligned to observed standard protein residues; atom completeness is assessed separately"
    label_to_seq: dict[str, int] = {}
    seq_to_label: dict[int, str] = {}
    if input_sequence is None or seq_utils.clean_sequence(input_sequence) == structure_sequence:
        if mapping_qc is not None:
            mapping_qc.update(identity=1.0, structure_coverage=1.0, input_coverage=1.0,
                              mapped_residues=len(labels), partial_input_coverage=False,
                              input_coordinate_coverage=1.0, unmapped_input_residues=0,
                              unmapped_input_ranges=[])
        for index, label in enumerate(labels, start=1):
            label_to_seq[label] = index
            seq_to_label[index] = label
        return label_to_seq, seq_to_label, list(range(1, len(labels) + 1))

    from Bio.Align import PairwiseAligner

    aligner = PairwiseAligner()
    aligner.mode = "global"
    aligner.match_score = 2
    aligner.mismatch_score = -1
    aligner.open_gap_score = -5
    aligner.extend_gap_score = -0.5
    alignment = aligner.align(structure_sequence, seq_utils.clean_sequence(input_sequence))[0]
    aligned = [(s_start + offset, q_start + offset)
               for (s_start, s_end), (q_start, q_end) in zip(alignment.aligned[0], alignment.aligned[1])
               for offset in range(s_end - s_start)]
    identity = sum(structure_sequence[i] == input_sequence[j] for i, j in aligned) / max(1, len(aligned))
    coverage = len(aligned) / max(1, len(structure_sequence))
    input_coverage = len(aligned) / max(1, len(input_sequence))
    if mapping_qc is not None:
        mapping_qc.update(identity=identity, structure_coverage=coverage, input_coverage=input_coverage,
                          mapped_residues=len(aligned), partial_input_coverage=input_coverage < 1.0,
                          input_coordinate_coverage=input_coverage,
                          unmapped_input_residues=len(input_sequence) - len(aligned),
                          unmapped_input_ranges=_position_ranges(
                              sorted(set(range(1, len(input_sequence) + 1)) - {j + 1 for _, j in aligned})))
    if identity < 0.9 or coverage < 0.8:
        raise ValueError(f"구조와 입력 서열의 동일성이 부족합니다 (identity={identity:.3f}, 구조 coverage={coverage:.3f}; 최소 0.9/0.8).")
    if input_coverage < 1.0 and warnings is not None:
        message = (f"입력 서열의 {input_coverage:.1%}만 제공 구조에 대응합니다 "
                   f"({len(aligned)}/{len(input_sequence)}잔기). 나머지 {len(input_sequence) - len(aligned)}잔기는 "
                   "제공 좌표로 평가하지 못합니다. 결손·부분 구조를 확인하세요. 전체 입력 서열은 보존합니다.")
        if message not in warnings:
            warnings.append(message)
    seq_positions: list[int] = [0] * len(labels)
    for (s_start, s_end), (q_start, q_end) in zip(alignment.aligned[0], alignment.aligned[1]):
        for offset in range(s_end - s_start):
            label = labels[s_start + offset]
            position = q_start + offset + 1
            label_to_seq[label] = position
            seq_to_label[position] = label
            seq_positions[s_start + offset] = position
    if len(label_to_seq) == 0:
        raise ValueError(
            "제공 구조와 target.sequence 를 정렬하지 못했습니다. 서열/체인을 확인하세요."
        )
    return label_to_seq, seq_to_label, seq_positions


def _position_ranges(positions: list[int]) -> list[list[int]]:
    """Compact inclusive 1-based ranges for retained mapping diagnostics."""
    ranges: list[list[int]] = []
    for position in positions:
        if ranges and position == ranges[-1][1] + 1:
            ranges[-1][1] = position
        else:
            ranges.append([position, position])
    return ranges


def _validate_target_chain(structure_path: str | Path, chain_id: str, from_provider: bool) -> tuple[str, list[str]]:
    available = pdb_io.chain_ids(pdb_io.load_structure(structure_path))
    if chain_id in available:
        return chain_id, []
    if from_provider and available:
        return available[0], [
            f"설정 체인 '{chain_id}' 이(가) 예측 구조에 없어 '{available[0]}'(으)로 대체합니다 (예측기는 A부터 명명)."
        ]
    raise ValueError(f"타깃 체인 '{chain_id}' 이(가) 구조에 없습니다. 사용 가능: {available}")


def _resolve_explicit_hotspots(
    entries: list[dict],
    chain_id: str,
    sequence: str,
    label_to_seq: dict[str, int],
    seq_to_label: dict[int, str],
) -> tuple[list[str], list[int], list[str], list[str]]:
    pdb_labels: list[str] = []
    seq_positions: list[int] = []
    seq_labels: list[str] = []
    warnings: list[str] = []
    for entry in entries:
        chain = entry["chain"]
        kind = entry["kind"]
        number = int(entry["number"])
        if kind is None and not warnings:
            warnings.append("접두사 없는 핫스팟 번호는 제공 구조의 PDB 잔기 번호로 해석했습니다. "
                            "번호 체계를 고정하려면 pdbN 또는 seqN으로 입력하세요.")
        if chain != chain_id:
            raise ValueError(f"핫스팟 체인 '{chain}' 이 타깃 체인 '{chain_id}' 과 다릅니다.")
        if kind == "seq":
            position = number
            if not 1 <= position <= len(sequence):
                raise ValueError(f"핫스팟 서열 위치 {position} 이 서열 범위(1-{len(sequence)})를 벗어났습니다.")
            if position not in seq_to_label:
                raise ValueError(
                    f"핫스팟 서열 위치 {position} 이 제공 구조에 존재하지 않습니다 "
                    "(구조에 없는 잔기를 라벨로 합성하지 않습니다, F09)."
                )
            label = seq_to_label[position]
        else:
            label = f"{chain_id}:{number}"
            if label not in label_to_seq:
                raise ValueError(
                    f"핫스팟 PDB 잔기 {label} 이 구조에 없습니다. 서열 위치로 지정하려면 'seq{number}' 접두사를 사용하세요."
                )
            position = label_to_seq[label]
        pdb_labels.append(label)
        seq_positions.append(position)
        seq_labels.append(f"{chain_id}:{position}")
    return pdb_labels, seq_positions, seq_labels, warnings


def _prepare_sequence_only(
    cfg: dict,
    phase_dir: Path,
    sequence: str,
    chain_id: str,
    warnings: list[str],
    aux_chains: list[dict],
    dry_run: bool,
    explicit_entries: list[dict] | None = None,
    target_msa: str | None = None,
) -> dict:
    """구조 없이 타깃 서열만으로 준비. 빈 목록은 hotspot 미지정이다."""
    if explicit_entries is None:
        explicit_entries = hotspots_mod.parse_hotspot_spec(cfg_get(cfg, "target.hotspots", "auto"), chain_id)
    hotspot_spec = cfg_get(cfg, "target.hotspots", "auto")
    no_hotspots = isinstance(hotspot_spec, list) and not hotspot_spec
    if hotspots_mod.requests_automatic_hotspots(hotspot_spec, chain_id):
        raise ValueError(
            "타깃 구조가 없어 자동 핫스팟을 계산할 수 없습니다. target.hotspots 에 잔기 번호를 지정하거나 "
            "target.pdb / structure.provider(boltz2|chai1)를 설정하세요."
        )
    seq_positions: list[int] = []
    if any(entry["kind"] is None for entry in explicit_entries):
        warnings.append("구조가 없어 접두사 없는 핫스팟 번호는 1-based 서열 위치로 해석했습니다. "
                        "번호 체계를 고정하려면 seqN으로 입력하세요.")
    for entry in explicit_entries:
        chain = entry["chain"]
        kind = entry["kind"]
        number = int(entry["number"])
        if chain != chain_id:
            raise ValueError(f"핫스팟 체인 '{chain}' 이 타깃 체인 '{chain_id}' 과 다릅니다.")
        if kind == "pdb":
            raise ValueError("구조가 없어 PDB 번호 핫스팟을 해석할 수 없습니다. 서열 위치('seqN')로 지정하세요.")
        if not 1 <= number <= len(sequence):
            raise ValueError(f"핫스팟이 타깃 서열 범위(1-{len(sequence)})를 벗어났습니다: {number}")
        seq_positions.append(number)
    seq_labels = [f"{chain_id}:{position}" for position in seq_positions]
    warnings.append(f"구조 없이 서열 전용 모드로 진행합니다 (핫스팟 {len(seq_positions)}개, 구조 QC 생략).")

    if target_msa is None:
        target_msa, msa_warnings = msa_utils.ensure_a3m(
            sequence, cfg_get(cfg, "structure.msa", {}) or {}, run_dir=phase_dir, dry_run=dry_run
        )
        warnings.extend(msa_warnings)
    target_msa = _snapshot_target_msa(target_msa, phase_dir, dry_run)

    set_id = "none" if no_hotspots else "user"
    pd.DataFrame([
        {"set_id": "user", "residue": label, "seq_position": position, "score": None}
        for label, position in zip(seq_labels, seq_positions)
    ], columns=["set_id", "residue", "seq_position", "score"]).to_csv(phase_dir / "hotspot_sets.csv", index=False)
    (phase_dir / "target_sequence.fasta").write_text(f">{cfg_get(cfg, 'target.name', 'target')}\n{sequence}\n", encoding="utf-8")
    qc = {
        "sequence_length": len(sequence),
        "chain_id": chain_id,
        "provider": "sequence",
        "raw_structure": None,
        "prepared_structure": None,
        "mean_plddt": None,
        "hotspot_mean_plddt": None,
        "hotspot_set_id": set_id,
        "hotspots": seq_labels,
        "hotspot_seq_positions": seq_positions,
        "hotspot_validation": [],
        "truncation": {"enabled": False},
        "warnings": warnings,
    }
    (phase_dir / "target_qc.json").write_text(
        json.dumps(to_native(qc), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "sequence": sequence,
        "chain_id": chain_id,
        "raw_structure": None,
        "structure": None,
        "target_msa": target_msa,
        "aux_chains": aux_chains,
        "hotspot_labels": seq_labels,
        "hotspot_mode": "none" if no_hotspots else "specified",
        "hotspot_pdb_labels": None,
        "hotspot_seq_positions": seq_positions,
        "hotspot_seq_labels": seq_labels,
        "hotspot_sets": [{"set_id": set_id, "residues": seq_labels, "residues_seq": seq_positions, "score": None}],
        "auto_sets": [],
        "qc": qc,
        "warnings": warnings,
    }


def _snapshot_target_msa(target_msa: str | None, phase_dir: Path, dry_run: bool) -> str:
    """Keep the exact target MSA used by a run inside its portable artifact directory."""
    if dry_run:
        return target_msa or "empty"
    snapshot = phase_dir / "target_msa.a3m"
    if not target_msa or target_msa == "empty":
        snapshot.unlink(missing_ok=True)
        return "empty"
    source = Path(target_msa)
    if not source.is_file():
        return target_msa
    if source.resolve() != snapshot.resolve():
        shutil.copy2(source, snapshot)
    return str(snapshot)


def _parse_aux_chains(cfg: dict) -> list[dict]:
    """target.aux_chains: [{name, sequence}] 를 검증해 반환."""
    entries = cfg_get(cfg, "target.aux_chains", []) or []
    parsed: list[dict] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            sequence = seq_utils.clean_sequence(str(entry.get("sequence", "")))
            name = str(entry.get("name", f"aux{index + 1}"))
        elif isinstance(entry, str):
            sequence = seq_utils.clean_sequence(entry)
            name = f"aux{index + 1}"
        else:
            raise ValueError(f"target.aux_chains 형식 오류: {entry}")
        if not sequence:
            raise ValueError(f"target.aux_chains[{index}] 서열이 비어 있습니다.")
        parsed.append({"name": name, "sequence": sequence})
    return parsed


def _run_truncate(cfg: dict, raw_structure: Path, chain_id: str, hotspot_labels: list[str], out_path: Path) -> dict:
    from .truncate import truncate_target

    return truncate_target(
        raw_structure,
        chain_id,
        hotspot_labels,
        out_path,
        radius=float(cfg_get(cfg, "truncate.radius", 30.0)),
        min_segment=int(cfg_get(cfg, "truncate.min_segment", 5)),
        keep_hetatm=bool(cfg_get(cfg, "truncate.keep_hetatm", False)),
        keep_other_chains=bool(cfg_get(cfg, "truncate.keep_other_chains", False)),
    )


def _acquire_structure(
    cfg: dict,
    phase_dir: Path,
    sequence: str,
    provider: str,
    dry_run: bool,
    warnings: list[str],
    aux_chains: list[dict] | None = None,
    msa_path: str = "empty",
) -> Path:
    aux_chains = aux_chains or []
    provided = resolve_path(cfg, cfg_get(cfg, "target.pdb"))
    if provided:
        if not provided.exists():
            raise FileNotFoundError(f"target.pdb 를 찾을 수 없습니다: {provided}")
        destination = phase_dir / f"target_raw{provided.suffix}"
        shutil.copy(provided, destination)
        return destination

    if provider == "none":
        raise ValueError("target.pdb 가 없고 structure.provider=none 입니다. 구조를 지정하거나 provider를 설정하세요.")

    if provider == "mock":
        return mock_mod.make_mock_target_only(sequence, phase_dir / "target_raw.pdb")

    if provider in {"boltz2", "boltz"}:
        env = resolve_path(cfg, cfg_get(cfg, "structure.boltz2.env"))
        extra = cfg_get(cfg, "structure.boltz2.extra_args", [])
        return _fold_boltz2(cfg, phase_dir, sequence, env, msa_path, extra, dry_run, aux_chains)

    if provider == "chai1":
        env = resolve_path(cfg, cfg_get(cfg, "structure.chai1.env"))
        extra = cfg_get(cfg, "structure.chai1.extra_args", [])
        return _fold_chai1(cfg, phase_dir, sequence, env, extra, dry_run, aux_chains)

    raise ValueError(f"지원하지 않는 structure.provider: {provider}")


def find_boltz_prediction_dir(out_dir: Path) -> Path | None:
    """Boltz 출력(boltz_results_*/predictions/<stem>)에서 예측 디렉터리를 찾는다."""
    matches = sorted(out_dir.glob("boltz_results_*/predictions/*"))
    if matches:
        return matches[0]
    matches = sorted(out_dir.glob("predictions/*"))
    return matches[0] if matches else None


def validate_generated_target_identity(path: str | Path, records: dict[str, str]) -> None:
    """Generated coordinates must contain exactly the submitted protein chains.

    Experimental structures have their own explicit sequence/number mapping;
    this stricter check applies only to provider-generated full-length output.
    """
    expected = {str(chain): seq_utils.clean_sequence(sequence) for chain, sequence in records.items()}
    if not expected or any(not sequence for sequence in expected.values()):
        raise ValueError("Generated target expected chain sequences cannot be empty")
    structure = pdb_io.load_structure(path)
    models = list(structure.get_models())
    if len(models) != 1:
        raise ValueError("Generated target structure must contain exactly one chain model")
    actual = {chain.id: pdb_io.chain_sequence(chain) for chain in models[0]}
    if actual != expected:
        raise ValueError("Generated target chain IDs or sequences differ from the exact submitted target and partner sequences")
    for chain in models[0]:
        if any(residue.id[0] == " " and not pdb_io.is_standard_residue(residue) for residue in chain):
            raise ValueError("Generated target chain contains an unsupported protein residue")
        for residue in pdb_io.iter_residues(chain):
            if "CA" not in residue or any(not math.isfinite(float(value)) for atom in residue for value in atom.coord):
                raise ValueError("Generated target chain coordinates are missing or nonfinite")


def _fold_boltz2(
    cfg: dict,
    phase_dir: Path,
    sequence: str,
    env: Path | None,
    msa_path: str,
    extra: list,
    dry_run: bool,
    aux_chains: list[dict] | None = None,
) -> Path:
    from .screen import write_boltz_yaml

    extra = validate_extra_args(extra, setting="structure.boltz2.extra_args", managed=BOLTZ_MANAGED)
    yaml_path = phase_dir / "target_boltz.yaml"
    out_dir = phase_dir / "boltz_out"
    chains = [{"id": "A", "sequence": sequence, "msa": msa_path}]
    for index, entry in enumerate(aux_chains or []):
        chains.append({"id": chr(ord("B") + index), "sequence": entry["sequence"], "msa": "empty"})
    write_boltz_yaml(chains, yaml_path)

    if not dry_run:
        provenance.archive_existing([out_dir, phase_dir / "boltz_fold.log"], phase_dir)
    executable = shell.env_executable(env, "boltz", dry_run=dry_run)
    if not dry_run:
        extra.append("--override")
    argv = [
        str(executable), "predict", str(yaml_path),
        "--out_dir", str(out_dir),
        "--output_format", "pdb",
        "--recycling_steps", str(int(cfg_get(cfg, "structure.boltz2.recycling_steps", 3))),
        "--diffusion_samples", str(int(cfg_get(cfg, "structure.boltz2.diffusion_samples", 1))),
        "--seed", str(cfg_seed(cfg, "structure.boltz2.seed")),
        *extra,
    ]
    shell.run_in_env(env, argv, extra_env=cfg_get(cfg, "structure.boltz2.env_vars", {}) or {},
                     log_path=phase_dir / "boltz_fold.log", dry_run=dry_run)
    if dry_run:
        placeholder = phase_dir / "target_raw.pdb"
        if not placeholder.exists():
            print("  [target] dry-run: 실제 예측 산출물이 없어 placeholder 구조로 준비 단계를 검증합니다(J01).")
            mock_mod.make_mock_target_only(sequence, placeholder)
        return placeholder
    prediction_dir = find_boltz_prediction_dir(out_dir)
    candidates = sorted(prediction_dir.glob("*_model_0.pdb")) if prediction_dir else []
    if not candidates and prediction_dir:
        candidates = sorted(prediction_dir.glob("*_model_0.cif"))
    if not candidates:
        raise FileNotFoundError(f"Boltz-2 타깃 예측 결과를 찾지 못했습니다: {out_dir}")
    validate_generated_target_identity(candidates[0], {entry["id"]: entry["sequence"] for entry in chains})
    return _to_pdb_from_file(candidates[0], phase_dir / "target_raw.pdb")


def _fold_chai1(
    cfg: dict,
    phase_dir: Path,
    sequence: str,
    env: Path | None,
    extra: list,
    dry_run: bool,
    aux_chains: list[dict] | None = None,
) -> Path:
    from .screen import write_chai_fasta

    extra = validate_extra_args(extra, setting="structure.chai1.extra_args", managed=CHAI_MANAGED, allow_abbrev=True)
    settings = chai_target_settings(cfg)
    records = {"A": sequence}
    for index, entry in enumerate(aux_chains or []):
        records[chr(ord("B") + index)] = entry["sequence"]
    fasta = write_chai_fasta(records, phase_dir / "target_chai.fasta")
    out_dir = phase_dir / "chai_out"
    if not dry_run:
        provenance.archive_existing([out_dir, phase_dir / "chai_fold.log"], phase_dir)
    msa_mode = str(cfg_get(cfg, "structure.chai1.msa_mode", "single"))
    # Use the same seeded, deterministic adapter as complex evaluation. The
    # upstream CLI previously ignored run.seed in this target-only path.
    argv = [str(shell.env_python(env)) if not dry_run else "python",
            str(Path(__file__).with_name("scripts") / "chai_fold.py"),
            "--fasta", str(fasta), "--out-dir", str(out_dir),
            "--seed", str(settings["seed"]),
            "--num-trunk-recycles", str(settings["num_trunk_recycles"]),
            "--num-diffn-timesteps", str(settings["num_diffn_timesteps"]),
            "--num-diffn-samples", str(settings["num_diffn_samples"])]
    if msa_mode == "msa":
        if not bool(cfg_get(cfg, "structure.allow_public_msa_server", False)):
            raise ValueError(
                "Chai-1 --use-msa-server 는 ColabFold 공용 서버를 호출합니다. LICENSE_MANIFEST에서 상업적 사용이 "
                "불명확해 기본 차단했습니다. 명시 허용: structure.allow_public_msa_server: true "
                "(권장: msa_mode: single 또는 Boltz-2 + 로컬 MMseqs MSA)."
            )
        argv.append("--use-msa-server")
    argv.extend(extra)
    from .diagnostics import _engine_extra_env
    shell.run_in_env(env, argv, extra_env=_engine_extra_env(cfg, "chai1"),
                     log_path=phase_dir / "chai_fold.log", dry_run=dry_run)
    if dry_run:
        placeholder = phase_dir / "target_raw.pdb"
        if not placeholder.exists():
            print("  [target] dry-run: 실제 예측 산출물이 없어 placeholder 구조로 준비 단계를 검증합니다(J01).")
            mock_mod.make_mock_target_only(sequence, placeholder)
        return placeholder
    candidates = sorted(out_dir.glob("pred.model_idx_*.cif"))
    if not candidates:
        raise FileNotFoundError(f"Chai-1 타깃 예측 결과를 찾지 못했습니다: {out_dir}")
    validate_generated_target_identity(candidates[0], records)
    return _to_pdb_from_file(candidates[0], phase_dir / "target_raw.pdb")


def _to_pdb_from_file(source: Path, destination: Path) -> Path:
    structure = pdb_io.load_structure(source)
    models = list(structure.get_models())
    for other in models[1:]:
        structure.detach_child(other.id)
    pdb_io.save_structure(structure, destination)
    return destination


def _to_pdb(structure, destination: Path) -> Path:
    pdb_io.save_structure(structure, destination)
    return destination
