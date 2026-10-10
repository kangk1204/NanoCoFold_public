"""General protein complexes, using the same prediction and provenance adapters."""
from __future__ import annotations

import copy
import io
import itertools
import subprocess
import time

import numpy as np
import pandas as pd
from Bio.PDB import PDBIO, Superimposer

from . import screen, score_complex as scorer
from .config import cfg_get
from .platform.inputs import AA
from .utils import pdb_io
from .utils.misc import to_native
from .utils.atomic import write_json, write_csv


def proteins(values):
    if not isinstance(values, list) or not values:
        raise ValueError("단백질 목록이 필요합니다.")
    result = []
    for i, entry in enumerate(values):
        if not isinstance(entry, dict):
            raise ValueError("단백질 입력 형식 오류")
        sequence = "".join(str(entry.get("sequence", "")).split()).upper()
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
    engine = settings.get("engine", "boltz2")
    if engine not in {"boltz2", "chai1", "opendde"}:
        raise ValueError("엔진은 Boltz-2, Chai-1 또는 선택 OpenDDE Preview입니다.")
    seeds = settings.get("seeds", [42, 123, 2026])
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20 or len(set(seeds)) != len(seeds) or any(type(n) is not int or not 0 <= n < 2**31 for n in seeds):
        raise ValueError("seed는 서로 다른 0–2147483647 정수 1–20개입니다.")
    for key, default, lo, hi in (("recycles", 3, 1, 20), ("diffusion_steps", 200, 1, 1000), ("models_per_seed", 1, 1, 5)):
        v = settings.get(key, default)
        if type(v) is not int or not lo <= v <= hi:
            raise ValueError(f"{key}: {lo}–{hi} 정수가 필요합니다.")
    if settings.get("msa", "empty") != "empty" or settings.get("restraints", False) is not False:
        raise ValueError("현재 일반 복합체 모드는 MSA 없음·접촉 제약 없음을 지원합니다.")
    if engine == "chai1" and settings.get("models_per_seed", 1) != 1:
        raise ValueError("Chai-1의 현재 PAE 어댑터는 seed당 모델 1개를 지원합니다. seed를 추가하세요.")
    return {"engine": engine, "seeds": seeds, "recycles": settings.get("recycles", 3),
            "diffusion_steps": settings.get("diffusion_steps", 200), "models_per_seed": settings.get("models_per_seed", 1),
            "msa": "empty", "restraints": False}


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


def run(cfg, root, state):
    entries = proteins(cfg_get(cfg, "complex.proteins"))
    settings = validate_settings(cfg_get(cfg, "complex", {}))
    records = {p["id"]: p["sequence"] for p in entries}
    state.set_phase("input", {"proteins": entries, "settings": settings})
    rows = []; total = len(settings["seeds"])*settings["models_per_seed"]
    state.set_phase("prediction", {"completed_models": 0, "total_models": total, "failed_models": 0})
    for seed in settings["seeds"]:
        prediction_folder = root / "02_complex" / f"seed_{seed}"
        local = copy.deepcopy(cfg); local["run"]["seed"] = seed
        local["score"]["boltz2"] = {"seed": seed}
        local["score"]["boltz2_diffusion_samples"] = settings["models_per_seed"]
        local["score"]["boltz2_recycling_steps"] = settings["recycles"]
        local["score"]["boltz2_extra_args"] = ["--no_kernels", "--num_workers", "0", "--sampling_steps", str(settings["diffusion_steps"])]
        local["screen"].update(num_trunk_recycles=settings["recycles"], num_diffn_timesteps=settings["diffusion_steps"], num_diffn_samples=1, msa_mode="single", restraint={"enabled": False})
        started = time.time(); prediction_error = None
        try:
            if cfg_get(cfg, "run.mock", False):
                raise ValueError("일반 복합체는 합성 좌표를 생성하지 않습니다. 저장된 예제 결과를 열거나 실제 분석을 실행하세요.")
            if settings["engine"] == "boltz2":
                screen._predict_boltz2(local, prediction_folder, records, "empty", entries[-1]["id"], [], False)
                directory = next((prediction_folder / "boltz_out").glob("boltz_results_*/predictions/*"))
            elif settings["engine"] == "opendde":
                from .opendde import predict as opendde_predict
                local.setdefault("screen", {}).setdefault("opendde", {}).update(cycles=settings["recycles"], steps=settings["diffusion_steps"], samples=settings["models_per_seed"])
                directory = opendde_predict(local, prediction_folder, records)
            else:
                screen._predict_chai1(local, prediction_folder, {"sequence": entries[0]["sequence"]}, records, entries[-1]["id"], [], False)
                directory = prediction_folder / "chai_out"
        except Exception as exc:
            from .utils.shell import ExecutionCancelled
            if isinstance(exc, (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired)):raise
            prediction_error = exc
        for model in range(settings["models_per_seed"]):
            identifier = f"seed_{seed}_model_{model}"
            folder = root / "02_complex" / identifier; folder.mkdir(parents=True, exist_ok=True)
            try:
                if prediction_error is not None:raise prediction_error
                pred = scorer.find_prediction_files(directory, model_index=model)
                data = structure_data(pred, entries, local)
                data["candidate_id"] = identifier
                _write(folder / "structure.json", data)
                (folder / "model.pdb").write_text(data["content"])
                row = {"candidate_id": identifier, "seed": seed, "effective_seed": seed, "model": model, "engine": settings["engine"],
                       "iptm": data["global_iptm"], "engine_aggregate_score": data["engine_aggregate_score"],
                       "ipsae": data["pair_metrics"][0].get("ipsae"),
                       "n_clashes": sum(p.get("n_clashes") or 0 for p in data["pair_metrics"]),
                       "elapsed_seconds": (time.time()-started)/settings["models_per_seed"], "elapsed_scope": "seed_group_amortized", "evaluated": True, "error": None,
                       "structure_file": str((folder/"structure.json").relative_to(root)), "structure_available": True, "selected": False, "final_eligible": None, "exclusion_reasons": [], "pair_metrics": data["pair_metrics"],
                       "reference_comparison": {k:v for k,v in data["reference"].items() if k != "content"}}
            except Exception as exc:
                from .utils.shell import ExecutionCancelled
                if isinstance(exc, (ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired)):
                    raise
                reason = screen.prediction_failure_reason(prediction_folder) or f"{type(exc).__name__}: {exc}"
                row = {"candidate_id": identifier, "seed": seed, "model": model, "engine": settings["engine"], "evaluated": False, "error": reason, "iptm": None}
                print(f"[complex] {identifier}: {row['error']}", flush=True)
            rows.append(row)
            _write(root / "complex_results.json", {"schema_version": 2, "mode": "complex", "rows": rows, "settings": settings, "proteins": entries})
            state.set_phase("prediction", {"completed_models": len(rows), "total_models": total, "failed_models": sum(bool(r.get("error")) for r in rows)})
            print(f"[complex] {len(rows)}/{total} 완료", flush=True)
    write_csv(root / "models.csv", pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in rows]), root=root)
    state.set_phase("final", {"total_models": total, "successful_models": sum(r["evaluated"] for r in rows)})
    return 0 if any(r["evaluated"] for r in rows) else 1
