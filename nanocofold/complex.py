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
from .result_schema import model_observation_record, structure_payload


def proteins(values, *, historical=False):
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
        reference_chains = entry.get("reference_chains")
        if reference_chains is not None:
            if (not isinstance(reference_chains, list) or len(reference_chains) != copies
                    or any(not isinstance(c, str) or not c or c.strip() != c for c in reference_chains)
                    or len(set(reference_chains)) != copies or entry.get("reference_chain") is not None):
                raise ValueError("reference_chains는 사본 수와 같은 길이의 중복 없는 체인 목록이며 reference_chain과 함께 지정할 수 없습니다.")
        elif copies > 1 and entry.get("reference_chain") is not None and not historical:
            raise ValueError("여러 사본에는 단일 reference_chain 대신 사본별 reference_chains를 지정하세요. 예전 결과는 report 명령으로 다시 열 수 있습니다. 재개·새 계산은 사본별 대응을 검토한 새 설정이 필요합니다.")
        for copy_index in range(copies):
            result.append({**entry, "id": chr(65+len(result)), "sequence": sequence[start-1:end],
                           "original_positions": positions[start-1:end], "start": start, "end": end, "copies": 1,
                           "source_protein_index": i, "copy_index": copy_index,
                           "name": str(entry.get("name") or f"protein_{i+1}")[:120]})
            if reference_chains is not None:
                result[-1]["reference_chain"] = reference_chains[copy_index]
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
    primary_pair = settings.get("primary_pair")
    if primary_pair is not None:
        if (not isinstance(primary_pair, list) or len(primary_pair) != 2
                or any(not isinstance(c, str) or len(c) != 1 or not 'A' <= c <= 'Y' for c in primary_pair)
                or primary_pair[0] == primary_pair[1]):
            raise ValueError("primary_pair는 서로 다른 분석 체인 ID 2개의 목록이어야 합니다.")
        result["primary_pair"] = list(primary_pair)
    symmetry = settings.get('reference_symmetry', True)
    if type(symmetry) is not bool:
        raise ValueError('reference_symmetry는 true/false여야 합니다.')
    result['reference_symmetry'] = symmetry
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


def validate_analysis_chains(entries, settings, *, historical=False):
    """Check selections against canonical analysis IDs after copy expansion."""
    chain_ids = {entry["id"] for entry in entries}
    if settings.get("primary_pair") and not set(settings["primary_pair"]) <= chain_ids:
        raise ValueError("primary_pair가 실제 분석 체인에 없습니다.")
    anchor = settings.get("anchor_chain", "A")
    if not historical and (not isinstance(anchor, str) or anchor not in chain_ids):
        raise ValueError("anchor_chain은 실제 분석 체인 ID여야 합니다.")


def pair_summary(pair_metrics, settings):
    """Keep the historical first-pair default and label assessed extrema."""
    requested = settings.get("primary_pair")
    chosen = next((p for p in pair_metrics if requested is not None
                   and {p['chain_a'], p['chain_b']} == set(requested)), None) if requested else pair_metrics[0]
    if chosen is None:
        raise ValueError("primary_pair가 실제 분석 체인 쌍에 없습니다.")
    finite = [float(p['ipsae']) for p in pair_metrics if p.get('ipsae') is not None
              and np.isfinite(float(p['ipsae']))]
    other_contacts = [p for p in pair_metrics if p is not chosen and p.get("n_contacts", 0) is not None
                      and p.get("n_contacts", 0) > 0]
    warning = ("대표 체인쌍에 접촉이 없지만 다른 체인쌍에는 접촉이 있습니다. complex.primary_pair를 확인하세요."
               if chosen.get("n_contacts") == 0 and other_contacts else None)
    return {"ipsae": chosen.get("ipsae"), "primary_pair_warning": warning, "interface_plddt": chosen.get("interface_plddt"),
            "interface_pae": chosen.get("interface_pae"), "ipsae_scope": "selected_chain_pair",
            "ipsae_chain_a": chosen["chain_a"], "ipsae_chain_b": chosen["chain_b"],
            "ipsae_aggregation": "max_of_directional_pair_ipsae",
            "ipsae_selection": "explicit_primary_pair" if requested else "first_pair_in_reviewed_chain_order",
            "primary_pair": [chosen['chain_a'], chosen['chain_b']],
            "max_pair_ipsae": max(finite) if finite else None,
            "min_pair_ipsae": min(finite) if finite else None,
            "pair_ipsae_assessed_count": len(finite), "pair_ipsae_total_count": len(pair_metrics),
            "pair_ipsae_all_assessed": len(finite) == len(pair_metrics),
            "pair_ipsae_extrema_scope": "finite_recorded_chain_pairs; missing_pairs_excluded"}


def _write(path, value):
    write_json(path, to_native(value))


def _pdb(structure):
    out = io.StringIO(); writer = PDBIO(); writer.set_structure(structure); writer.save(out)
    return out.getvalue()


def reference_comparison(structure, entries, reference, settings):
    """Preserve anchor-fit fields and search exact sequence-equivalent mappings."""
    if not reference:
        return {"available": False, "reason": "비교 구조가 제공되지 않았습니다."}, structure
    try:
        import math
        ref = pdb_io.load_structure(reference)
        anchor = settings.get("anchor_chain", "A")
        initial = {e["id"]: e.get("reference_chain", e["id"]) for e in entries}
        if len(set(initial.values())) != len(entries):
            raise ValueError("참조 체인 대응은 사본별로 중복 없이 지정해야 합니다.")
        groups = {}
        for entry in entries:
            groups.setdefault((entry["sequence"], tuple(entry["original_positions"])), []).append(entry["id"])
        symmetric = settings.get("reference_symmetry", True)
        if type(symmetric) is not bool:
            raise ValueError("reference_symmetry는 true/false여야 합니다.")
        required = math.prod(math.factorial(len(g)) for g in groups.values()) if symmetric else 1
        if required > 4096:
            return {"available": False, "reason": "동일 서열 사본의 정확한 순열 탐색이4096개 한도를 넘습니다. 사본별 reference_chains를 확인하고 고정 대응 비교에는 reference_symmetry=false를 명시하세요.",
                    "symmetry_search_complete": False, "symmetry_search_status": "exact_search_budget_exceeded",
                    "permutations_required": str(required), "permutation_budget": 4096}, structure
        predicted = {e["id"]: list(pdb_io.iter_residues(pdb_io.get_chain(structure, e["id"]))) for e in entries}
        references = {c: {r.id[1]: r for r in pdb_io.iter_residues(pdb_io.get_chain(ref, c)) if not r.id[2].strip()} for c in initial.values()}
        def matches_for(mapping):
            result = {}
            for entry in entries:
                pairs = []
                for residue, number in zip(predicted[entry["id"]], entry["original_positions"]):
                    native = references[mapping[entry["id"]]].get(number)
                    if native is not None and native.resname != residue.resname:
                        raise ValueError(f"체인 {entry['id']} 원본 번호 {number}: 참조와 입력 서열이 다릅니다.")
                    if native is not None and "CA" in residue and "CA" in native:
                        pairs.append((residue["CA"], native["CA"]))
                result[entry["id"]] = pairs
            return result
        alternatives = [list(itertools.permutations([initial[c] for c in group])) if symmetric else [tuple(initial[c] for c in group)] for group in groups.values()]
        best = None; evaluated = 0
        for choice in itertools.product(*alternatives):
            mapping = {chain: reference_id for group, assignment in zip(groups.values(), choice) for chain, reference_id in zip(group, assignment)}
            matches = matches_for(mapping)
            anchor_pairs = matches.get(anchor, [])
            if len(anchor_pairs) < 3 or any(np.linalg.matrix_rank(np.asarray([p[k].coord for p in anchor_pairs])-np.mean([p[k].coord for p in anchor_pairs], axis=0)) < 2 for k in (0, 1)):
                continue
            all_pairs = [pair for group in matches.values() for pair in group]
            fit_all = Superimposer(); fit_all.set_atoms([p[1] for p in all_pairs], [p[0] for p in all_pairs])
            key = (-len(all_pairs), float(fit_all.rms), tuple(mapping[e["id"]] for e in entries))
            evaluated += 1
            if best is None or key < best[0]: best = (key, mapping, matches)
        if best is None:
            raise ValueError("명확한 비공선 기준 체인 Cα 대응이3개 미만입니다.")
        _, mapping, matches = best; pairs = matches[anchor]
        fit = Superimposer(); fit.set_atoms([p[1] for p in pairs], [p[0] for p in pairs]); fit.apply(structure.get_atoms())
        comparisons = []
        all_distances = []
        for entry in entries:
            matched = matches[entry["id"]]
            distances = [float(np.sum((a.coord-b.coord)**2)) for a,b in matched];all_distances.extend(distances)
            interest = set(settings.get("interest_positions", []))
            region = [(a,b) for a,b in matched if b.parent.id[1] in interest]
            comparisons.append({"chain": entry["id"], "reference_chain": mapping[entry["id"]], "matched_ca": len(matched),
                "ca_rmsd_angstrom": float(np.sqrt(np.mean(distances))) if distances else None,
                "interest_ca_rmsd_angstrom": float(np.sqrt(np.mean([np.sum((a.coord-b.coord)**2) for a,b in region]))) if region else None})
        return {"available": True, "content": _pdb(ref), "format": "pdb", "anchor_chain": anchor,
                "reference_anchor_chain": mapping[anchor],
                "matched_ca": len(pairs), "rmsd": float(fit.rms), "rmsd_scope": "anchor_chain_CA",
                "display_alignment_scope": "anchor_chain_CA_with_selected_sequence_equivalent_mapping",
                "all_chain_ca_rmsd_angstrom": best[0][1], "all_chain_ca_rmsd_scope": "all_matched_CA_single_rigid_body_fit",
                "complex_rmsd": best[0][1], "complex_rmsd_scope": "all_matched_CA_single_rigid_body_fit",
                "all_chain_matched_ca": -best[0][0],
                "anchor_aligned_all_chain_ca_rmsd_angstrom": float(np.sqrt(np.mean(all_distances))),
                "chain_permutation_map": mapping, "symmetry_search_complete": bool(symmetric),
                "symmetry_search_status": "exact_sequence_equivalent_search" if symmetric else "explicit_fixed_mapping",
                "symmetry_candidates_evaluated": evaluated, "permutation_budget": 4096,
                "mapping_objective": "maximum_matched_CA_then_minimum_all_chain_CA_RMSD_then_lexical_map", "chains": comparisons}, structure
    except (ValueError, KeyError, OSError) as exc:
        from .platform.errors import public_error
        return {"available": False, "reason": public_error(exc, stage="input")}, structure


def structure_data(pred, entries, cfg):
    structure = pdb_io.load_structure(pred.cif)
    if pdb_io.chain_ids(structure) != [p["id"] for p in entries]:
        raise ValueError("예측 체인 순서와 입력 대응이 다릅니다.")
    for entry in entries:
        if pdb_io.chain_sequence(pdb_io.get_chain(structure, entry["id"])) != entry["sequence"]:
            raise ValueError("예측 서열과 입력 서열이 다릅니다.")
    context = scorer.prepare_scoring_context(pred, structure=structure)
    pae, confidence = context.pae, context.confidence
    chai_pairs, chai_pair_reason = _chai_pair_scores(confidence, entries) if pred.source == "chai1" else ({}, None)
    pairs = []
    for a,b in itertools.combinations(entries, 2):
        metrics = scorer.score_complex(pred.cif, a["id"], b["id"], [], cfg, pred=pred,
                                       label=a["id"]+"–"+b["id"], context=context)
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
                      **{k: metrics.get(k) for k in ("ipsae", "interface_plddt", "interface_pae", "n_contacts",
                          "interface_assessment_status", "buried_sasa", "buried_sasa_status")}})
    primary = pair_summary(pairs, cfg_get(cfg, 'complex', {}))
    selected_target, selected_binder = primary['primary_pair']
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
        chains.append({"id": entry["id"], "name": entry["name"], "role": "target" if entry["id"] == selected_target else "binder" if entry["id"] == selected_binder else "context", "residues": residues})
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
            for p in pdb_io.contact_pairs(pdb_io.get_chain(ref_struct, reference["chain_permutation_map"][a["id"]]), pdb_io.get_chain(ref_struct, reference["chain_permutation_map"][b["id"]]), cutoff=5.):
                a_label, b_label = p["a"].split(":",1)[1], p["b"].split(":",1)[1]
                if a_label in {str(n) for n in a["original_positions"]} and b_label in {str(n) for n in b["original_positions"]}:
                    native_ref.add((a["id"]+":"+a_label, b["id"]+":"+b_label))
        recovered = native_ref & {(p["a"],p["b"]) for p in contacts}
        reference.update(reference_contacts_5A=len(native_ref), recovered_contacts_5A=len(recovered), fnat_5A=len(recovered)/len(native_ref) if native_ref else None)
    return to_native({"content": content, "format": "pdb", "chains": chains,
                      "target_chain": selected_target, "binder_chain": selected_binder, **primary,
                      "contacts": contacts, "clashes": clashes, "hotspots": [], "cdr": {"ranges": {}}, "reference": reference,
                      "pae": {"values": pae.tolist(), "labels": native_labels, "unit": "Å"} if pae is not None else None,
                      "pair_metrics": pairs, "global_iptm": confidence.get("iptm", confidence.get("protein_iptm")),
                      "engine_aggregate_score": confidence.get("aggregate_score"), "issues": [primary["primary_pair_warning"]] if primary["primary_pair_warning"] else []})


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
    validate_analysis_chains(entries, cfg_get(cfg, "complex", {}))
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
                    row = {"candidate_id": identifier, "seed": seed, "effective_seed": seed, "model": model, "engine": engine,
                        "iptm": data["global_iptm"], "iptm_scope": "whole_complex", "engine_aggregate_score": data["engine_aggregate_score"],
                        **pair_summary(data["pair_metrics"], settings),
                        "n_clashes": sum(p.get("n_clashes") or 0 for p in data["pair_metrics"]), "n_clashes_scope": "sum_over_all_chain_pairs",
                        "elapsed_seconds": (time.time() - started) / len(pending),
                        "elapsed_scope": "analysis_only_native_reused" if "native_inference_reused" in attempt_record else "engine_seed_group_amortized",
                        "msa": settings["msa"], "consumed_msa": consumed_msa, "condition_receipt": conditions,
                        "evaluated": True, "error": None, "structure_file": str((folder / "structure.json").relative_to(root)),
                        "structure_available": True, "selected": False, "final_eligible": None, "exclusion_reasons": [], "pair_metrics": data["pair_metrics"],
                        "reference_comparison": {k: v for k, v in data["reference"].items() if k != "content"},
                        "identity_fingerprint": identity["fingerprint"], "prediction_receipt": groups[group], "attempt": str(attempt.relative_to(root))}
                    row["coordinate_source"] = data["coordinate_source"]
                    row = model_observation_record(row, mode="complex")
                    data.update(pair_summary(data['pair_metrics'], settings))
                    data = structure_payload(data, observation=row, mode="complex")
                    _write(folder / "structure.json", data)
                    write_bytes(folder / "model.pdb", data["content"].encode("utf-8"), root=root)
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
                row = model_observation_record(row, mode="complex")
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
