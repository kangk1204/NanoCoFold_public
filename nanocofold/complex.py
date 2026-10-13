"""General protein complexes, using the same prediction and provenance adapters."""
from __future__ import annotations

import copy
import io
import itertools
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from Bio.PDB import PDBIO, Superimposer

from . import provenance, screen, score_complex as scorer
from .config import cfg_get
from .platform.inputs import AA
from .utils import pdb_io
from .utils.misc import to_native
from .utils.seq import clean_sequence
from .utils.atomic import check_output, write_bytes, write_json, write_csv


def proteins(values):
    if not isinstance(values, list) or not values:
        raise ValueError("단백질 목록이 필요합니다.")
    result = []
    names = [entry.get("name") or f"protein_{i+1}" for i, entry in enumerate(values) if isinstance(entry, dict)]
    for name in names:
        if not isinstance(name, str) or len(name) > 120 or any(ord(char) < 32 for char in name) or name.lstrip().startswith(("=", "+", "-", "@")):
            raise ValueError("단백질 이름은 120자 이하로 입력하고 수식 시작 문자나 제어 문자를 제외하세요.")
    for i, entry in enumerate(values):
        if not isinstance(entry, dict):
            raise ValueError("단백질 입력 형식 오류")
        sequence = clean_sequence(str(entry.get("sequence", "")))
        if not sequence or set(sequence) - AA:
            raise ValueError(f"단백질 {i+1}: 표준 아미노산 20종만 허용합니다. X 등 불명확한 잔기를 해결하세요.")
        start, end, copies = entry.get("start", 1), entry.get("end", len(sequence)), entry.get("copies", 1)
        if any(type(v) is not int for v in (start, end, copies)) or not 1 <= start <= end <= len(sequence) or not 1 <= copies <= 25:
            raise ValueError(f"단백질 {i+1}: 분석 구간 또는 복제 수 오류")
        positions = entry.get("original_positions", list(range(1, len(sequence)+1)))
        if (not isinstance(positions, list) or len(positions) != len(sequence)
                or any(type(n) is not int or not -999 <= n <= 9999 for n in positions) or len(set(positions)) != len(positions)):
            raise ValueError("원본 번호 대응이 모호하거나 PDB 번호 범위를 벗어납니다.")
        for _ in range(copies):
            result.append({**entry, "id": chr(65+len(result)), "sequence": sequence[start-1:end],
                           "original_positions": positions[start-1:end], "start": start, "end": end, "copies": 1,
                           "name": str(entry.get("name") or f"protein_{i+1}")[:120]})
    if len(result) < 2 or len(result) > 25 or sum(len(p["sequence"]) for p in result) > 2000:
        raise ValueError("복합체는 2–25체인, 합계 2,000잔기 이하여야 합니다.")
    return result


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("복합체 설정은 객체여야 합니다.")
    comparison = settings.get("comparison_engines")
    if comparison is not None:
        from .engine_comparison import AVAILABLE_ENGINES
        if "engine" in settings:
            raise ValueError("복합체 비교에는 comparison_engines만 지정하세요. engine과 함께 보낼 수 없습니다.")
        if (not isinstance(comparison, list) or not comparison or any(e not in AVAILABLE_ENGINES for e in comparison)
                or len(set(comparison)) != len(comparison)):
            raise ValueError("비교 엔진은 중복 없는 colabfold, boltz2, chai1, opendde 목록입니다.")
    engine = settings.get("engine", "boltz2")
    if comparison is None and engine not in {"boltz2", "chai1", "opendde"}:
        raise ValueError("엔진은 Boltz-2, Chai-1 또는 선택 OpenDDE Preview입니다.")
    seeds = settings.get("seeds", [42, 123, 2026])
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20 or len(set(seeds)) != len(seeds) or any(type(n) is not int or not 0 <= n < 2**31 for n in seeds):
        raise ValueError("seed는 서로 다른 0–2147483647 정수 1–20개입니다.")
    for key, default, lo, hi in (("recycles", 3, 1, 20), ("diffusion_steps", 200, 1, 1000), ("models_per_seed", 1, 1, 5)):
        v = settings.get(key, default)
        if type(v) is not int or not lo <= v <= hi:
            raise ValueError(f"{key}: {lo}–{hi} 정수가 필요합니다.")
    msa = "precomputed" if comparison is not None else "empty"
    if settings.get("msa", msa) != msa or settings.get("restraints", False) is not False:
        raise ValueError("일반 복합체 비교에는 준비된 MSA, 기존 단일 엔진에는 MSA 없음이 필요합니다. 접촉 제약은 지원하지 않습니다.")
    if (engine == "chai1" or comparison and "chai1" in comparison) and settings.get("models_per_seed", 1) != 1:
        raise ValueError("Chai-1의 현재 PAE 어댑터는 seed당 모델 1개를 지원합니다. seed를 추가하세요.")
    result = {"seeds": seeds, "recycles": settings.get("recycles", 3),
            "diffusion_steps": settings.get("diffusion_steps", 200), "models_per_seed": settings.get("models_per_seed", 1),
            "msa": msa, "restraints": False}
    if comparison is None:
        result["engine"] = engine
    else:
        source = settings.get("msa_source", "cache")
        if source not in {"cache", "public_api", "local_mmseqs2"}:
            raise ValueError("MSA 방식은 cache, public_api, local_mmseqs2입니다. 자동 전환하지 않습니다.")
        consent = settings.get("allow_public_msa_server", False)
        if type(consent) is not bool or source == "public_api" and consent is not True:
            raise ValueError("이 복합체의 정확한 서열을 공용 MSA 서버로 전송하는 데 동의하세요.")
        result.update(comparison_engines=list(comparison), msa_source=source, allow_public_msa_server=consent)
    return result


def _write(path, value):
    write_json(path, to_native(value))


def _pdb(structure):
    out = io.StringIO(); writer = PDBIO(); writer.set_structure(structure); writer.save(out)
    return out.getvalue()


def reference_comparison(structure, entries, reference, settings):
    """Explicit original-number mapping; fit only the anchor chain."""
    if not reference:
        return {"available": False, "reason": "비교 구조가 제공되지 않았습니다."}, structure
    try:
        ref = pdb_io.load_structure(reference)
        anchor = settings.get("anchor_chain", "A")
        matches = {}
        for entry in entries:
            chain = pdb_io.get_chain(structure, entry["id"])
            original = pdb_io.get_chain(ref, entry.get("reference_chain", entry["id"]))
            lookup = {r.id[1]: r for r in pdb_io.iter_residues(original) if not r.id[2].strip()}
            pairs = []
            for r, number in zip(pdb_io.iter_residues(chain), entry["original_positions"]):
                native = lookup.get(number)
                if native is not None and native.resname != r.resname:
                    raise ValueError(f"체인 {entry['id']} 원본 번호 {number}: 참조와 입력 서열이 다릅니다.")
                if native is not None and native.resname == r.resname and "CA" in r and "CA" in native:
                    pairs.append((r["CA"], native["CA"]))
            matches[entry["id"]] = pairs
        pairs = matches[anchor]
        if len(pairs) < 3 or any(np.linalg.matrix_rank(np.asarray([p[k].coord for p in pairs])-np.mean([p[k].coord for p in pairs], axis=0)) < 2 for k in (0, 1)):
            raise ValueError("명확한 비공선 기준 체인 Cα 대응이 3개 미만입니다.")
        fit = Superimposer(); fit.set_atoms([p[1] for p in pairs], [p[0] for p in pairs]); fit.apply(structure.get_atoms())
        comparisons = []
        for entry in entries:
            matched = matches[entry["id"]]
            rmsd = float(np.sqrt(np.mean([np.sum((a.coord-b.coord)**2) for a,b in matched]))) if matched else None
            interest = set(settings.get("interest_positions", []))
            region = [(a,b) for a,b in matched if b.parent.id[1] in interest]
            comparisons.append({"chain": entry["id"], "matched_ca": len(matched), "ca_rmsd_angstrom": rmsd,
                                "interest_ca_rmsd_angstrom": float(np.sqrt(np.mean([np.sum((a.coord-b.coord)**2) for a,b in region]))) if region else None})
        return {"available": True, "content": _pdb(ref), "format": "pdb", "anchor_chain": anchor,
                "matched_ca": len(pairs), "rmsd": float(fit.rms), "chains": comparisons}, structure
    except (ValueError, KeyError, OSError) as exc:
        return {"available": False, "reason": str(exc)}, structure


def structure_data(pred, entries, cfg):
    structure = pdb_io.load_structure(pred.cif)
    if pdb_io.chain_ids(structure) != [p["id"] for p in entries]:
        raise ValueError("예측 체인 순서와 입력 대응이 다릅니다.")
    for entry in entries:
        if pdb_io.chain_sequence(pdb_io.get_chain(structure, entry["id"])) != entry["sequence"]:
            raise ValueError("예측 서열과 입력 서열이 다릅니다.")
    labels, _ = scorer.build_index_map(structure)
    pae = scorer.load_pae(pred)
    if pae is not None:
        scorer.validate_pae(pae, len(labels))
    confidence = scorer.load_confidence(pred)
    chai_pairs, chai_pair_reason = _chai_pair_scores(confidence, entries) if pred.source == "chai1" else ({}, None)
    pairs = []
    for a,b in itertools.combinations(entries, 2):
        metrics = scorer.score_complex(pred.cif, a["id"], b["id"], [], cfg, pred=pred, label=a["id"]+"–"+b["id"])
        pair_iptm = confidence.get("pair_chains_iptm", {})
        ai, bi = str(ord(a["id"])-65), str(ord(b["id"])-65)
        forward, reverse = pair_iptm.get(ai, {}).get(bi), pair_iptm.get(bi, {}).get(ai)
        if pred.source == "chai1":
            forward, reverse = chai_pairs.get((a["id"], b["id"])), chai_pairs.get((b["id"], a["id"]))
        if pred.source == "opendde":
            matrix = confidence.get("chain_pair_iptm")
            forward = matrix[int(ai)][int(bi)] if matrix is not None else None
            reverse = matrix[int(bi)][int(ai)] if matrix is not None else None
        pairs.append({"chain_a": a["id"], "chain_b": b["id"], "iptm_a_to_b": forward,
                      "iptm_b_to_a": reverse, "n_clashes": metrics.get("n_target_clashes"),
                      "native_pair_iptm_source": "chai1.per_chain_pair_iptm" if pred.source == "chai1" else "opendde.chain_pair_iptm" if pred.source == "opendde" else "pair_chains_iptm",
                      "native_pair_iptm_axes": ["query_chain", "key_chain"] if pred.source == "chai1" else ["chain_a", "chain_b"] if pred.source == "opendde" else None,
                      "native_pair_iptm_unavailable_reason": "OpenDDE chain_pair_iptm 미제공" if pred.source == "opendde" and matrix is None else chai_pair_reason,
                      **{k: metrics.get(k) for k in ("ipsae", "interface_plddt", "interface_pae")}})
    reference, structure = reference_comparison(structure, entries, cfg_get(cfg, "complex.reference_path"), cfg_get(cfg, "complex", {}))
    # Assign unique temporary numbers first to avoid collisions in PDBIO.
    for entry in entries:
        residues = list(pdb_io.iter_residues(pdb_io.get_chain(structure, entry["id"])))
        for i,r in enumerate(residues): r.id = (" ", 20000+i, " ")
        for r,n in zip(residues, entry["original_positions"]): r.id = (" ", n, " ")
    content = _pdb(structure)
    # Reparse final exported bytes: atom serials change around TER records.
    from Bio.PDB import PDBParser
    exported = PDBParser(QUIET=True).get_structure("exported", io.StringIO(content))
    chains, native_labels = [], []
    for entry in entries:
        chain = pdb_io.get_chain(exported, entry["id"])
        residues = [{"label": pdb_io.residue_label(r, chain.id), "number": r.id[1], "name": r.resname,
                     "atom_ids": {a.name: a.serial_number for a in r}, "ca": r["CA"].coord.tolist() if "CA" in r else None}
                    for r in pdb_io.iter_residues(chain)]
        native_labels.extend(r["label"] for r in residues)
        chains.append({"id": entry["id"], "name": entry["name"], "role": "target" if entry["id"] == "A" else "binder" if entry["id"] == "B" else "context", "residues": residues})
    contacts, clashes = [], []
    from .platform.structure_review import closest_atoms
    clash_cutoff = float(cfg_get(cfg, "score.clash_cutoff", 2.0))
    for a,b in itertools.combinations(entries, 2):
        for p in pdb_io.contact_pairs(pdb_io.get_chain(exported, a["id"]), pdb_io.get_chain(exported, b["id"]), cutoff=max(5.,clash_cutoff)):
            item = {"a": p["a"], "b": p["b"], "distance": float(p["dist"])}
            if p["dist"] <= 5:contacts.append(item)
            if p["dist"] < clash_cutoff:clashes.append({**item,**closest_atoms(p),"cutoff":clash_cutoff,"kind":"protein_pair"})
    if reference.get("available"):
        ref_struct = pdb_io.load_structure(cfg["complex"]["reference_path"])
        native_ref = set()
        for a,b in itertools.combinations(entries, 2):
            for p in pdb_io.contact_pairs(pdb_io.get_chain(ref_struct, a.get("reference_chain", a["id"])), pdb_io.get_chain(ref_struct, b.get("reference_chain", b["id"])), cutoff=5.):
                a_label, b_label = p["a"].split(":",1)[1], p["b"].split(":",1)[1]
                if a_label in {str(n) for n in a["original_positions"]} and b_label in {str(n) for n in b["original_positions"]}:
                    native_ref.add((a["id"]+":"+a_label, b["id"]+":"+b_label))
        recovered = native_ref & {(p["a"],p["b"]) for p in contacts}
        reference.update(reference_contacts_5A=len(native_ref), recovered_contacts_5A=len(recovered), fnat_5A=len(recovered)/len(native_ref) if native_ref else None)
    return to_native({"content": content, "format": "pdb", "chains": chains, "target_chain": "A", "binder_chain": "B",
                      "contacts": contacts, "clashes": clashes, "hotspots": [], "cdr": {"ranges": {}}, "reference": reference,
                      "pae": {"values": pae.tolist(), "labels": native_labels, "unit": "Å"} if pae is not None else None,
                      "pair_metrics": pairs, "global_iptm": confidence.get("iptm", confidence.get("protein_iptm")),
                      "engine_aggregate_score": confidence.get("aggregate_score"), "issues": []})


def _chai_pair_scores(confidence, entries):
    """Use native tensor axis receipts; absent/ambiguous mappings stay null."""
    axis = confidence.get("chain_axis")
    if not isinstance(axis, dict):
        return {}, confidence.get("chain_axis_unavailable", "Chai native chain-axis receipt is absent")
    ids = axis.get("chain_ids", [])
    if len(ids) != len(entries) or len(set(ids)) != len(ids) or set(ids) != {entry["id"] for entry in entries}:
        return {}, "Chai native chain axes do not match reviewed input chains"
    try:
        matrix = np.asarray(confidence["per_chain_pair_iptm"], dtype=float)
        if matrix.ndim == 3 and matrix.shape[0] == 1:
            matrix = matrix[0]
        if matrix.shape != (len(ids), len(ids)) or not np.isfinite(matrix).all() or np.any(matrix < 0) or np.any(matrix > 1):
            raise ValueError("Chai native pair matrix dimensions/values are invalid")
        return {(a, b): float(matrix[i, j]) for i, a in enumerate(ids) for j, b in enumerate(ids) if i != j}, None
    except (KeyError, ValueError, TypeError) as exc:
        return {}, str(exc)


def _resume_error(root, reason):
    """Record a refusal without replacing any input, observation or native file."""
    _write(root / "02_complex" / "resume_rejections" / (uuid4().hex + ".json"),
           {"reason": reason, "time_ns": time.time_ns(), "action": "preserved; submit a new run"})
    raise ValueError("복합체 재개를 중단했습니다: " + reason + ". 원본을 보존하고 새 분석을 제출하세요.")


def _read_resume_json(root, path):
    try:
        check_output(path, root)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError, TypeError):
        _resume_error(root, "재개 기록 형식이 손상되었습니다")


def _owned_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        _resume_error(root, "산출물의 상대 경로가 올바르지 않습니다")
    path = root / relative
    try:
        check_output(path, root)
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise OSError("missing or outside run")
    except (OSError, ValueError):
        _resume_error(root, "저장된 산출물이 없거나 실행 밖의 경로를 가리킵니다")
    return path


def _verify_artifacts(root, manifest):
    if not isinstance(manifest, dict) or not manifest:
        _resume_error(root, "산출물 해시 기록이 없습니다")
    for relative, digest in manifest.items():
        if provenance.file_digest(_owned_file(root, relative)) != digest:
            _resume_error(root, "저장된 산출물의 해시가 변경되었습니다")


def _request_identity(cfg, entries, settings):
    # Runtime controls and report presentation cannot change the requested
    # scientific conditions. The complete effective runtime is checked below.
    from .config import resolve_path
    external = {}
    for key in ("complex.reference_path", "score.ipsae.official_script"):
        path = resolve_path(cfg, cfg_get(cfg, key))
        if path:
            external[key] = provenance.file_digest(path)
    value = {"proteins": entries, "settings": settings, "external_input_sha256": external,
             "scientific_config": {key: value for key, value in cfg.items() if key not in {"run", "report"}}}
    return to_native(value)


def _run_identity(cfg, entries, settings, engines, monomers, requested):
    from .complex_comparison import condition_receipt, effective_config
    package = Path(__file__).resolve().parent
    code = {str(p.relative_to(package)): provenance.file_digest(p) for p in sorted(package.rglob("*.py"))}
    msa = {}
    for letter, entry in monomers.items():
        digest = provenance.file_digest(entry["a3m"])
        if digest != entry["sha256"]:
            raise ValueError("검토 후 복합체 MSA 파일이 변경되었습니다.")
        msa[letter] = digest
    applied = {engine: effective_config(cfg, settings, engine, settings["seeds"][0]) for engine in engines}
    value = {"schema_version": 1, "requested": requested, "msa_sha256": msa,
             "application_python_sha256": provenance.fingerprint(code),
             "engine_conditions": {engine: condition_receipt(local, settings, engine) for engine, local in applied.items()},
             "runtime": {engine: provenance.runtime_identity(local, engine) for engine, local in applied.items()}}
    value = to_native(value)
    return {"fingerprint": provenance.fingerprint(value), **value}


def _verify_group(root, record, identity):
    if not isinstance(record, dict):
        _resume_error(root, "예측 완료 영수증 형식이 손상되었습니다")
    receipt_path = _owned_file(root, record.get("path"))
    if provenance.file_digest(receipt_path) != record.get("sha256"):
        _resume_error(root, "예측 완료 영수증의 해시가 변경되었습니다")
    receipt = _read_resume_json(root, receipt_path)
    if receipt.get("identity_fingerprint") != identity["fingerprint"]:
        _resume_error(root, "예측 완료 영수증의 실행 조건이 다릅니다")
    _verify_artifacts(root, receipt.get("artifacts"))
    relative = receipt.get("prediction_directory")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        _resume_error(root, "예측 완료 디렉터리의 상대 경로가 올바르지 않습니다")
    directory = root / relative
    try:
        check_output(directory, root)
        if not directory.resolve().is_relative_to(root) or not directory.is_dir():
            raise OSError("prediction directory missing")
    except (OSError, ValueError):
        _resume_error(root, "완료된 예측 디렉터리가 없거나 실행 밖의 경로를 가리킵니다")
    return receipt


def _resume_observations(root, identity, expected):
    path = root / "complex_results.json"
    rows = _read_resume_json(root, path).get("rows") if path.exists() else []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _resume_error(root, "모델 관측 목록이 손상되었습니다")
    result = {}
    def verify(row):
        candidate = row.get("candidate_id")
        if candidate not in expected or row.get("identity_fingerprint") != identity["fingerprint"]:
            _resume_error(root, "관측 ID 또는 실행 identity가 현재 계획과 다릅니다")
        if (row.get("engine"), row.get("seed"), row.get("model")) != expected[candidate]:
            _resume_error(root, "관측의 엔진·seed·모델 대응이 다릅니다")
        observation = _owned_file(root, row.get("observation_file"))
        if _read_resume_json(root, observation) != row:
            _resume_error(root, "현재 관측과 보존한 attempt 관측이 다릅니다")
        if row.get("evaluated") is True and not row.get("error"):
            payload = {key: value for key, value in row.items() if key != "observation_sha256"}
            if row.get("observation_sha256") != provenance.fingerprint(payload):
                _resume_error(root, "성공 관측의 내용 해시가 변경되었습니다")
            _verify_artifacts(root, row.get("artifacts"))
            group = _verify_group(root, row.get("prediction_receipt", {}), identity)
            if group.get("group") != row["engine"] + "__" + f"seed_{row['seed']}":
                _resume_error(root, "성공 관측과 예측 영수증의 엔진·seed 대응이 다릅니다")
    for row in rows:
        candidate = row.get("candidate_id")
        if candidate in result:
            _resume_error(root, "관측 ID가 중복됩니다")
        verify(row)
        result[candidate] = row
    # An interrupt between the immutable observation write and active-ledger
    # publication must not cause a successful model to be recomputed. Failed
    # attempts remain inspectable, while a verified success always takes priority.
    history = sorted((root / "02_complex" / "attempts").glob("*/*/models/model_*/observation.json"),
                     key=lambda p: (p.stat().st_mtime_ns, str(p)))
    for path in history:
        row = _read_resume_json(root, path)
        verify(row)
        if row.get("observation_file") != str(path.relative_to(root)):
            _resume_error(root, "보존한 관측의 파일 대응이 다릅니다")
        candidate = row["candidate_id"]
        current = result.get(candidate, {})
        if candidate not in result or (row.get("evaluated") is True and not current.get("evaluated")):
            result[candidate] = row
    return result


def _save_observations(root, settings, entries, identity, rows, expected):
    ordered = [rows[key] for key in expected if key in rows]
    _write(root / "complex_results.json", {"schema_version": 3, "mode": "complex", "rows": ordered,
           "settings": settings, "proteins": entries, "identity_fingerprint": identity["fingerprint"],
           "resume_policy": "verified successful observations retained; incomplete native groups retried in new attempts"})
    return ordered


def run(cfg, root, state):
    root = Path(root).resolve()
    if cfg_get(cfg, "run.dry_run", False):
        raise ValueError("일반 복합체의 실제 예측은 dry-run에서 실행하지 않습니다. 플랫폼 입력 검토에서 조건을 확인하세요.")
    if cfg_get(cfg, "run.mock", False):
        raise ValueError("일반 복합체는 합성 좌표를 생성하지 않습니다. 저장된 예제 결과를 열거나 실제 분석을 실행하세요.")
    entries = proteins(cfg_get(cfg, "complex.proteins"))
    settings = validate_settings(cfg_get(cfg, "complex", {}))
    records = {p["id"]: p["sequence"] for p in entries}
    comparison = settings.get("comparison_engines")
    engines = comparison or [settings["engine"]]
    requested = _request_identity(cfg, entries, settings)
    identity_path, groups_path = root / "complex_resume_identity.json", root / "complex_inference_groups.json"
    previous = _read_resume_json(root, identity_path) if identity_path.exists() else None
    has_evidence = (root / "complex_results.json").exists() or groups_path.exists() or (root / "02_complex").exists()
    if previous is None and has_evidence:
        _resume_error(root, "기존 실행에는 검증 가능한 재개 identity가 없습니다")
    if previous:
        if not cfg_get(cfg, "run.resume", True):
            _resume_error(root, "전체 재실행에는 별도의 새 실행 디렉터리가 필요합니다")
        if previous.get("requested") != requested:
            _resume_error(root, "입력·엔진·분석 설정 또는 참조 파일이 변경되었습니다")
    total = len(settings["seeds"]) * settings["models_per_seed"] * len(engines)
    expected = {(engine + "__" if comparison else "") + f"seed_{seed}_model_{model}": (engine, seed, model)
                for engine in engines for seed in settings["seeds"] for model in range(settings["models_per_seed"])}
    # Publish the known plan before MSA preparation, whose provider may take time.
    if not previous:
        state.set_phase("prediction", {"completed_models": 0, "total_models": total, "failed_models": 0})
    monomers = {}
    if comparison:
        from .complex_comparison import configure, prepare
        cfg, monomers = prepare(configure(copy.deepcopy(cfg), settings), root, state, entries)
    try:
        identity = _run_identity(cfg, entries, settings, engines, monomers, requested)
    except (OSError, ValueError, TypeError, KeyError):
        _resume_error(root, "MSA·실행 환경·모델 파일의 identity 검증에 실패했습니다")
    if previous and previous != identity:
        _resume_error(root, "MSA·실제 엔진 조건·코드·실행 환경 또는 모델 지문이 변경되었습니다")
    if not previous:
        _write(identity_path, identity)
    rows = _resume_observations(root, identity, expected)
    groups = _read_resume_json(root, groups_path).get("groups") if groups_path.exists() else {}
    if not isinstance(groups, dict):
        _resume_error(root, "예측 완료 목록이 손상되었습니다")
    verified_groups = {key: _verify_group(root, record, identity) for key, record in groups.items()}
    planned_groups = {engine + "__" + f"seed_{seed}" for engine in engines for seed in settings["seeds"]}
    if any(key not in planned_groups or receipt.get("group") != key for key, receipt in verified_groups.items()):
        _resume_error(root, "예측 완료 목록의 엔진·seed 대응이 현재 계획과 다릅니다")
    state.set_phase("input", {"proteins": entries, "settings": settings, "identity_fingerprint": identity["fingerprint"]})
    state.set_phase("prediction", {"completed_models": len(rows), "total_models": total,
                    "failed_models": sum(bool(row.get("error")) for row in rows.values())})
    from .complex_comparison import condition_receipt, effective_config
    for engine in engines:
        for seed in settings["seeds"]:
            group = engine + "__" + f"seed_{seed}"
            model_ids = [(engine + "__" if comparison else "") + f"seed_{seed}_model_{model}"
                         for model in range(settings["models_per_seed"])]
            pending = [candidate for candidate in model_ids if not (rows.get(candidate, {}).get("evaluated") is True
                       and not rows[candidate].get("error"))]
            if not pending:
                print(f"[complex] {group}: 검증된 성공 {len(model_ids)}개 재사용", flush=True)
                continue
            local = effective_config(cfg, settings, engine, seed)
            conditions = condition_receipt(local, settings, engine)
            attempt = root / "02_complex" / "attempts" / group / uuid4().hex
            check_output(attempt, root)
            attempt.mkdir(parents=True)
            prediction_folder = attempt / "prediction"
            attempt_record = {"schema_version": 1, "group": group, "engine": engine, "seed": seed,
                "identity_fingerprint": identity["fingerprint"], "condition_receipt": conditions,
                "pending_models": pending, "started_ns": time.time_ns(), "status": "running"}
            _write(attempt / "attempt.json", attempt_record)
            started = time.time()
            prediction_error = None
            consumed_msa = {}
            group_receipt = verified_groups.get(group)
            try:
                if group_receipt is not None:
                    # Native inference finished before the interrupted/failed
                    # analysis. Re-evaluate only missing observations, without
                    # re-entering an engine wrapper or touching successful files.
                    directory = root / group_receipt["prediction_directory"]
                    consumed_msa = group_receipt["consumed_msa"]
                    attempt_record["native_inference_reused"] = groups[group]
                else:
                    if comparison:
                        from .complex_comparison import predict as compare_predict
                        directory, consumed_msa = compare_predict(local, prediction_folder, records, engine, monomers)
                    elif engine == "boltz2":
                        screen._predict_boltz2(local, prediction_folder, records, "empty", entries[-1]["id"], [], False)
                        directory = next((prediction_folder / "boltz_out").glob("boltz_results_*/predictions/*"))
                    elif engine == "opendde":
                        from .opendde import predict as opendde_predict
                        directory = opendde_predict(local, prediction_folder, records)
                    else:
                        screen._predict_chai1(local, prediction_folder, {"sequence": entries[0]["sequence"]}, records, entries[-1]["id"], [], False)
                        directory = prediction_folder / "chai_out"
                    directory = Path(directory)
                    check_output(directory, root)
                    if not directory.is_dir() or not directory.resolve().is_relative_to(root):
                        raise ValueError("예측 결과 디렉터리가 실행 내부에 없습니다.")
                    artifacts = {str(path.relative_to(root)): provenance.file_digest(path)
                                 for path in sorted(prediction_folder.rglob("*")) if path.is_file()}
                    if not artifacts:
                        raise ValueError("예측 결과 파일이 없습니다.")
                    group_receipt = {"schema_version": 1, "identity_fingerprint": identity["fingerprint"],
                        "group": group, "prediction_directory": str(directory.relative_to(root)),
                        "artifacts": artifacts, "consumed_msa": consumed_msa,
                        "condition_receipt": conditions, "native_inference_seconds": time.time() - started}
                    receipt_path = attempt / "inference_receipt.json"
                    _write(receipt_path, group_receipt)
                    groups[group] = {"path": str(receipt_path.relative_to(root)), "sha256": provenance.file_digest(receipt_path)}
                    _write(groups_path, {"schema_version": 1, "identity_fingerprint": identity["fingerprint"], "groups": groups})
                    verified_groups[group] = group_receipt
            except BaseException as exc:
                from .utils.shell import ExecutionCancelled
                if isinstance(exc, (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit)):
                    _write(attempt / "attempt.json", {**attempt_record, "status": "interrupted", "error": type(exc).__name__})
                    raise
                if not isinstance(exc, Exception):
                    raise
                prediction_error = exc
            for model, identifier in enumerate(model_ids):
                if identifier not in pending:
                    continue
                folder = attempt / "models" / f"model_{model}"
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    if prediction_error is not None:
                        raise prediction_error
                    pred = scorer.find_prediction_files(directory, model_index=model)
                    data = structure_data(pred, entries, local)
                    data["candidate_id"] = identifier
                    data["coordinate_source"] = {"raw_path": str(pred.cif.relative_to(root)), "raw_sha256": provenance.file_digest(pred.cif),
                        "display_transform": "original numbering; optional reference alignment", "raw_preserved": True}
                    data["raw_content"] = pred.cif.read_bytes().decode("utf-8")
                    data["raw_format"] = pred.cif.suffix.lstrip(".")
                    data["consumed_msa"] = consumed_msa
                    data["condition_receipt"] = conditions
                    _write(folder / "structure.json", data)
                    write_bytes(folder / "model.pdb", data["content"].encode("utf-8"), root=root)
                    pair = data["pair_metrics"][0]
                    row = {"candidate_id": identifier, "seed": seed, "effective_seed": seed, "model": model, "engine": engine,
                        "iptm": data["global_iptm"], "iptm_scope": "whole_complex", "engine_aggregate_score": data["engine_aggregate_score"],
                        "ipsae": pair.get("ipsae"), "ipsae_scope": "selected_chain_pair",
                        "ipsae_chain_a": pair["chain_a"], "ipsae_chain_b": pair["chain_b"],
                        "ipsae_aggregation": "max_of_directional_pair_ipsae", "ipsae_selection": "first_pair_in_reviewed_chain_order",
                        "n_clashes": sum(p.get("n_clashes") or 0 for p in data["pair_metrics"]), "n_clashes_scope": "sum_over_all_chain_pairs",
                        "elapsed_seconds": (time.time() - started) / len(pending),
                        "elapsed_scope": "analysis_only_native_reused" if "native_inference_reused" in attempt_record else "engine_seed_group_amortized",
                        "msa": settings["msa"], "consumed_msa": consumed_msa, "condition_receipt": conditions,
                        "evaluated": True, "error": None, "structure_file": str((folder / "structure.json").relative_to(root)),
                        "structure_available": True, "selected": False, "final_eligible": None, "exclusion_reasons": [], "pair_metrics": data["pair_metrics"],
                        "reference_comparison": {k: v for k, v in data["reference"].items() if k != "content"},
                        "identity_fingerprint": identity["fingerprint"], "prediction_receipt": groups[group], "attempt": str(attempt.relative_to(root))}
                    files = [folder / "structure.json", folder / "model.pdb", pred.cif]
                    files += [getattr(pred, key, None) for key in ("scores_npz", "confidence_json", "pae_npz")]
                    row["artifacts"] = {str(path.relative_to(root)): provenance.file_digest(path) for path in files if path is not None}
                except BaseException as exc:
                    from .utils.shell import ExecutionCancelled
                    if isinstance(exc, (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit)):
                        _write(attempt / "attempt.json", {**attempt_record, "status": "interrupted", "error": type(exc).__name__})
                        raise
                    if not isinstance(exc, Exception):
                        raise
                    reason = screen.prediction_failure_reason(prediction_folder) or f"{type(exc).__name__}: {exc}"
                    row = {"candidate_id": identifier, "seed": seed, "model": model, "engine": engine,
                           "evaluated": False, "error": reason, "iptm": None, "condition_receipt": conditions,
                           "identity_fingerprint": identity["fingerprint"], "attempt": str(attempt.relative_to(root))}
                    print(f"[complex] {identifier}: {row['error']}", flush=True)
                # Every attempt retains its own observation. The active ledger
                # is a derived view and never replaces a verified successful row.
                row["observation_file"] = str((folder / "observation.json").relative_to(root))
                row = to_native(row)
                if row.get("evaluated") is True:
                    row["observation_sha256"] = provenance.fingerprint(row)
                _write(folder / "observation.json", row)
                rows[identifier] = row
                ordered = _save_observations(root, settings, entries, identity, rows, expected)
                state.set_phase("prediction", {"completed_models": len(ordered), "total_models": total,
                                "failed_models": sum(bool(r.get("error")) for r in ordered)})
                print(f"[complex] {len(ordered)}/{total} 완료", flush=True)
            _write(attempt / "attempt.json", {**attempt_record, "status": "complete" if all(rows[key].get("evaluated") is True for key in model_ids) else "partial_failure",
                                            "finished_ns": time.time_ns(), "observations": model_ids})
    ordered = _save_observations(root, settings, entries, identity, rows, expected)
    write_csv(root / "models.csv", pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in ordered]), root=root)
    successes = sum(row.get("evaluated") is True for row in ordered)
    state.set_phase("final", {"total_models": total, "successful_models": successes})
    return 0 if successes else 1
