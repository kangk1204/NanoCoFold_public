"""Exploratory, sequence-verified pose agreement; never a binding/KD score.

The object compared is the *same exact candidate and target sequence* within
one case. A proper rigid fit uses matched target Cα atoms only. Candidate Cα
RMSD is then measured in that target frame without fitting the candidate.
Contacts are target sequence positions with a protein heavy atom near the
candidate. Missing data are not zeros, and empty/empty contacts are not evidence
of agreement. Native VHH coordinates are neither inputs nor success gates.

Qualified models enter clusters before unqualified observations, ordered by
geometry rather than engine/model names. Each enters the first cluster with
passing pairs to *every* member. This complete-link partition maintains
the all-pairs invariant; A~B and B~C cannot imply A~C. It is not a maximum-clique
search or a calibrated probabilistic consensus. Geometry and quality-supported
distinct-engine counts remain separate, and confidence scores are never summed.
Policy v3 distinguishes multi-engine support from a single-engine promising
observation. Lack of agreement and generation-stage inconsistency are diagnostic
uncertainty, never biological non-binding evidence. Generation consistency does
not gate these classes or their review ranks.
For m models with a target and binder of t/b residues, pair alignment costs
O(m²(t+b)), complete-link partitioning O(m²) time/space plus O(m(t+b)) retained
coordinates. Heavy-atom contact queries use one spatial index per structure;
their worst case is O(A_target*A_binder), not an assumed constant query cost.
No inference or external commands are run.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from Bio.PDB import MMCIFParser, NeighborSearch, PDBParser
from Bio.PDB.PDBExceptions import PDBConstructionException, PDBIOException

from .binding_site_assessment import hotspot_assessment
from .utils import shell
from .utils.seq import AA1, AA1TO3, AA3TO1

SCHEMA_VERSION = 2
POLICY_VERSION = "pose_assessment_v3"
DEFAULTS = {
    "enabled": True,
    "contact_distance_A": 5.0,
    "max_target_rmsd_A": 3.0,
    "min_ca_coverage": 0.9,
    "max_binder_rmsd_A": 5.0,
    "min_contact_jaccard": 0.5,
    "min_supporting_engines": 2,
    "max_design_binder_rmsd_A": 5.0,
}
_INTERRUPTIONS = (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired)
_INPUT_ERRORS = (ValueError, KeyError, IndexError, ZeroDivisionError, OSError,
                 UnicodeError, PDBConstructionException, PDBIOException, np.linalg.LinAlgError)


def settings(cfg: dict | None = None) -> dict:
    """Return a frozen, hashed policy; thresholds are exploratory, not calibrated."""
    if cfg is not None and not isinstance(cfg, dict):
        raise ValueError("Pose assessment config must be an object")
    rank = (cfg or {}).get("rank", {})
    if not isinstance(rank, dict):
        raise ValueError("rank must be an object")
    options = rank.get("pose_assessment", {})
    if not isinstance(options, dict):
        raise ValueError("rank.pose_assessment must be an object")
    policy = {**DEFAULTS, **{k: options[k] for k in DEFAULTS if k in options}}
    if type(policy["enabled"]) is not bool:
        raise ValueError("rank.pose_assessment.enabled must be boolean")
    for key in ("contact_distance_A", "max_target_rmsd_A", "max_binder_rmsd_A", "max_design_binder_rmsd_A"):
        value = policy[key]
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"rank.pose_assessment.{key} must be finite and positive")
        policy[key] = float(value)
    for key in ("min_ca_coverage", "min_contact_jaccard"):
        value = policy[key]
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"rank.pose_assessment.{key} must be between zero and one")
        if key == "min_ca_coverage" and value == 0:
            raise ValueError("rank.pose_assessment.min_ca_coverage must be positive")
        policy[key] = float(value)
    if type(policy["min_supporting_engines"]) is not int or policy["min_supporting_engines"] < 2:
        raise ValueError("rank.pose_assessment.min_supporting_engines must be an integer >= 2")
    policy.update(version=POLICY_VERSION, calibrated=False,
                  grouping="case_and_exact_target_candidate_sequences",
                  alignment="proper_target_CA_rigid_fit_no_binder_fit",
                  clustering="qualified_first_geometry_ordered_greedy_complete_link",
                  score_aggregation="none", native_reference_gate=False,
                  generation_consistency="diagnostic_only",
                  single_engine_promising="verified_geometry_and_recorded_quality_and_requested_site",
                  multi_engine_supported="same_complete_link_pose_cluster_with_required_distinct_qualified_engines")
    policy["sha256"] = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return policy


def _checkpoint() -> None:
    shell.remaining_budget()
    # CPU post-processing honors the same cooperative scope as subprocess work.
    cancellation = shell._CANCELLATION.get()
    if cancellation and cancellation():
        raise shell.ExecutionCancelled("Pose assessment cancelled")


def _text_id(row: dict, keys: tuple[str, ...]) -> str | None:
    return next((row[k] for k in keys if isinstance(row.get(k), str) and row[k].strip()), None)


def _sequence(value, label: str) -> str:
    # The caller supplies canonical sequences; do not repair/truncate/infer them.
    if not isinstance(value, str) or not value or any(c not in AA1 for c in value):
        raise ValueError(f"Missing or noncanonical exact {label} sequence")
    return value


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source(row: dict, *, design: bool = False) -> dict:
    prefix = "design_" if design else ""
    content = row.get(prefix + "structure_content")
    path = _text_id(row, (prefix + "structure_path",) if design else ("structure_path", "cif_path"))
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Missing coordinate content")
        data = content.encode("utf-8")
    elif path:
        data = Path(path).read_bytes()
        content = data.decode("utf-8")
    else:
        raise ValueError("No preserved generation coordinates" if design else "No stored predicted coordinates")
    format_name = row.get(prefix + "structure_format")
    if format_name is None:
        format_name = Path(path).suffix.lstrip(".") if path else ("cif" if content.lstrip().startswith("data_") else "pdb")
    if not isinstance(format_name, str):
        raise ValueError("Coordinate format must be a string")
    format_name = format_name.lower()
    if format_name in {"mmcif", "cif"}:
        format_name = "cif"
    elif format_name in {"ent", "pdb"}:
        format_name = "pdb"
    else:
        raise ValueError("Unsupported coordinate format")
    digest = hashlib.sha256(data).hexdigest()
    expected = row.get(prefix + "structure_sha256")
    if expected is not None and expected != digest:
        raise ValueError("Coordinate source hash differs from recorded identity")
    # Runtime paths are only for opening files. Portable callers may supply the
    # verified run-relative path; standalone assessment discloses only a name.
    receipt_path = row.get(prefix + "structure_receipt_path")
    if receipt_path is None and path:
        receipt_path = Path(path).name if Path(path).is_absolute() else path
    return {"content": content, "format": format_name, "sha256": digest,
            "path": receipt_path, "size": len(data)}


def _chain(model, chain_id: str, sequence: str) -> dict:
    if not isinstance(chain_id, str) or chain_id not in model:
        raise ValueError("Declared chain missing from coordinates")
    residues = [r for r in model[chain_id] if r.id[0] == " "]
    if any(r.is_disordered() for r in residues):
        raise ValueError("Ambiguous alternate residue identity")
    if any(r.resname not in AA1TO3.values() for r in residues):
        raise ValueError("Noncanonical coordinate residue identity")
    if "".join(AA3TO1[r.resname] for r in residues) != sequence:
        raise ValueError("Coordinate chain differs from exact input sequence")
    ca, atoms, observed = {}, [], set()
    for position, residue in enumerate(residues, 1):
        _checkpoint()
        heavy = []
        for atom in residue:
            if atom.is_disordered():
                raise ValueError("Ambiguous alternate atom coordinates")
            xyz = np.asarray(atom.coord, dtype=np.float64)
            if xyz.shape != (3,) or not np.isfinite(xyz).all():
                raise ValueError("Nonfinite or malformed coordinates")
            element = str(atom.element or "").strip().upper()
            if element not in {"H", "D"}:
                heavy.append(atom)
        if not heavy:
            raise ValueError("Residue has no observed protein heavy atom")
        observed.add(position)
        atoms.extend(heavy)
        if "CA" in residue:
            ca[position] = np.asarray(residue["CA"].coord, dtype=np.float64)
    return {"ca": ca, "atoms": atoms, "residues": residues, "observed": observed,
            "ca_coverage": len(ca) / len(sequence)}


def _geometry(row: dict, policy: dict, *, design: bool = False, source: dict | None = None) -> dict:
    source = source if source is not None else _source(row, design=design)
    parser = MMCIFParser(QUIET=True) if source["format"] == "cif" else PDBParser(QUIET=True, PERMISSIVE=False)
    structure = parser.get_structure("pose", io.StringIO(source["content"]))
    if len(structure) != 1:
        raise ValueError("Exactly one coordinate model is required")
    model = structure[0]
    # Coordinates outside the chosen chains also must not contain nonfinite data.
    if any(not np.isfinite(atom.coord).all() for atom in model.get_atoms()):
        raise ValueError("Nonfinite coordinates")
    prefix = "design_" if design else ""
    target_chain = row.get(prefix + "target_chain", "A")
    binder_chain = row.get(prefix + "binder_chain", "B")
    if target_chain == binder_chain:
        raise ValueError("Target and candidate chain roles are ambiguous")
    target = _chain(model, target_chain, row["target_sequence"])
    binder = _chain(model, binder_chain, row["sequence"])
    if min(target["ca_coverage"], binder["ca_coverage"]) < policy["min_ca_coverage"]:
        raise ValueError("Cα coordinate coverage is below the frozen minimum")
    # Check intrinsic alignment rank even for singleton models.
    target_ca = np.asarray(list(target["ca"].values()), dtype=np.float64)
    _fit(target_ca, target_ca)
    neighbors = NeighborSearch(binder["atoms"])
    contacts = set()
    for position, residue in enumerate(target["residues"], 1):
        _checkpoint()
        heavy = [a for a in residue if str(a.element or "").strip().upper() not in {"H", "D"}]
        if any(neighbors.search(atom.coord, policy["contact_distance_A"]) for atom in heavy):
            contacts.add(position)
    receipt = {k: source[k] for k in ("sha256", "format", "path", "size")}
    receipt.update(target_chain=target_chain, binder_chain=binder_chain,
                   target_ca_coverage=target["ca_coverage"], binder_ca_coverage=binder["ca_coverage"],
                   contact_positions=sorted(contacts))
    # Atom/residue parent links retain entire Biopython structures. Comparisons
    # need only copied C-alpha coordinates, coverage, counts and contact sets.
    compact = lambda chain: {"ca": chain["ca"], "ca_coverage": chain["ca_coverage"],
                             "sequence_length": len(chain["residues"])}
    return {"target": compact(target), "binder": compact(binder),
            "contacts": contacts, "source": receipt}


def _fit(moving, fixed):
    """Proper row-vector transform; a reflection may never improve a pose fit."""
    moving, fixed = np.asarray(moving, dtype=np.float64), np.asarray(fixed, dtype=np.float64)
    if moving.shape != fixed.shape or moving.ndim != 2 or moving.shape[1:] != (3,) or len(moving) < 3:
        raise ValueError("At least three corresponding target Cα atoms are required")
    if not np.isfinite(moving).all() or not np.isfinite(fixed).all():
        raise ValueError("Nonfinite target Cα coordinates")
    a, b = moving.mean(axis=0), fixed.mean(axis=0)
    if min(np.linalg.matrix_rank(moving - a), np.linalg.matrix_rank(fixed - b)) < 2:
        raise ValueError("Target Cα alignment is collinear or degenerate")
    u, _, vt = np.linalg.svd((moving - a).T @ (fixed - b))
    correction = np.diag([1., 1., 1. if np.linalg.det(u @ vt) > 0 else -1.])
    rotation = u @ correction @ vt
    translation = b - a @ rotation
    rmsd = float(np.sqrt(np.mean(np.sum((moving @ rotation + translation - fixed) ** 2, axis=1))))
    return rotation, translation, rmsd


def _compare(left: dict, right: dict, policy: dict, *, design: bool = False) -> dict:
    result = {"status": "not_assessed", "reason": None, "target_ca_coverage": None,
              "binder_ca_coverage": None, "target_rmsd_A": None, "binder_rmsd_A": None,
              "contact_jaccard": None, "similar_pose": None}
    target_positions = sorted(left["target"]["ca"].keys() & right["target"]["ca"].keys())
    binder_positions = sorted(left["binder"]["ca"].keys() & right["binder"]["ca"].keys())
    target_coverage = len(target_positions) / left["target"]["sequence_length"]
    binder_coverage = len(binder_positions) / left["binder"]["sequence_length"]
    result.update(target_ca_coverage=target_coverage, binder_ca_coverage=binder_coverage,
                  matched_target_ca=len(target_positions), matched_binder_ca=len(binder_positions))
    if min(target_coverage, binder_coverage) < policy["min_ca_coverage"] or not binder_positions:
        result["reason"] = "Pairwise corresponding Cα coverage is below the frozen minimum"
        return result
    try:
        rotation, translation, target_rmsd = _fit([left["target"]["ca"][p] for p in target_positions],
                                                 [right["target"]["ca"][p] for p in target_positions])
    except _INTERRUPTIONS:
        raise
    except (ValueError, np.linalg.LinAlgError) as exc:
        result["reason"] = str(exc)
        return result
    result["target_rmsd_A"] = target_rmsd
    if target_rmsd > policy["max_target_rmsd_A"]:
        result["reason"] = "Target alignment RMSD exceeds the frozen maximum"
        return result
    moving = np.asarray([left["binder"]["ca"][p] for p in binder_positions])
    fixed = np.asarray([right["binder"]["ca"][p] for p in binder_positions])
    binder_rmsd = float(np.sqrt(np.mean(np.sum((moving @ rotation + translation - fixed) ** 2, axis=1))))
    a, b = left["contacts"], right["contacts"]
    result.update(binder_rmsd_A=binder_rmsd, left_contact_positions=sorted(a), right_contact_positions=sorted(b),
                  shared_contact_positions=sorted(a & b), rotation=rotation.tolist(), translation=translation.tolist())
    if not a or not b:
        result["reason"] = "Empty contact footprint is not evidence of pose agreement"
        return result
    jaccard = len(a & b) / len(a | b)
    maximum = policy["max_design_binder_rmsd_A"] if design else policy["max_binder_rmsd_A"]
    result.update(status="assessed", contact_jaccard=jaccard,
                  similar_pose=binder_rmsd <= maximum and jaccard >= policy["min_contact_jaccard"])
    return result


def _site(row: dict) -> dict:
    site = hotspot_assessment(row)
    raw = row.get("final_eligible")
    confidence = bool(raw) if isinstance(raw, (bool, np.bool_)) else None
    satisfied = True if site["status"] == "threshold_met" else (
        False if site["status"] in {"no_contact", "threshold_unmet"} else None)
    site_ok = True if site["status"] == "not_requested" else satisfied
    eligible = False if confidence is False or site_ok is False else (True if confidence is True and site_ok is True else None)
    return {**site, "satisfied": satisfied, "selection_eligible": eligible,
            "confidence_gate_eligible": confidence}


def _identity(row: dict, index: int) -> dict:
    model_id = _text_id(row, ("model_id", "id", "design_id"))
    candidate_id = _text_id(row, ("candidate_id", "source_sequence_id", "design_id"))
    target = _sequence(row.get("target_sequence"), "target")
    binder = _sequence(row.get("sequence"), "candidate")
    engine = _text_id(row, ("predictor_engine", "engine"))
    if not model_id or not engine:
        raise ValueError("Missing exact model or predictor engine identity")
    normalized_engine = "".join(c for c in engine.lower() if c.isalnum())
    aliases = {"boltz2": "boltz2", "chai1": "chai1", "colabfold": "colabfold",
               "opendde": "opendde", "openddepreview": "opendde"}
    if normalized_engine not in aliases:
        raise ValueError("Unknown predictor engine family cannot count as independent support")
    engine = aliases[normalized_engine]
    case_id = row.get("case_id")
    if case_id is not None and not isinstance(case_id, str):
        raise ValueError("Ambiguous case identity")
    target_sha, sequence_sha = _digest(target), _digest(binder)
    for field, digest in (("target_sequence_sha256", target_sha), ("sequence_sha256", sequence_sha)):
        if field in row and row[field] is not None and row[field] != digest:
            raise ValueError("Recorded exact sequence hash differs")
    grouping = {"case_id": case_id, "target_sequence_sha256": target_sha, "sequence_sha256": sequence_sha}
    return {"model_id": model_id, "candidate_id": candidate_id or sequence_sha, "engine": engine,
            "input_index": index, **grouping,
            "candidate_key": _digest(json.dumps(grouping, sort_keys=True, separators=(",", ":")))}


def _design(row: dict, predicted: dict, policy: dict) -> dict:
    result = {"status": "not_assessed", "reason": "No preserved generation coordinates",
              "binder_rmsd_A": None, "contact_jaccard": None,
              "reference_stage": row.get("design_reference_stage"),
              "reference_source": row.get("design_reference_source"),
              "interpretation": "Generation-stage coordinates versus independent prediction for the same sequence; not native pose accuracy"}
    has_source = row.get("design_structure_path") is not None or row.get("design_structure_content") is not None
    if not has_source:
        if row.get("design_expected") is False or row.get("design_origin") in {"submitted", "provided", "existing", "user"}:
            result.update(status="not_applicable", reason="No generation structure expected for a supplied candidate")
        return result
    try:
        source = _source(row, design=True)
        result["source"] = {k: source[k] for k in ("sha256", "format", "path", "size")}
        generation = _geometry(row, policy, design=True, source=source)
        comparison = _compare(generation, predicted, policy, design=True)
        result.update(comparison, source=generation["source"],
                      predicted_source_sha256=predicted["source"]["sha256"])
        if comparison["status"] == "assessed":
            result["status"] = "consistent" if comparison["similar_pose"] else "inconsistent"
    except _INTERRUPTIONS:
        raise
    except _INPUT_ERRORS as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    return result


def _qualified(annotation: dict) -> bool:
    """A positive observation needs known quality, site, roles and interface."""
    assessment = annotation["pose_assessment"]
    return (annotation["site_selection_eligible"] is True and assessment["status"] == "assessed"
            and bool(assessment.get("source", {}).get("contact_positions")))


def _geometry_order(geometry: dict) -> tuple:
    """Rigid-transform invariant ordering that contains no engine/model names.

    Canonical target-frame C-alpha coordinates distinguish poses without a
    target-by-candidate distance matrix. Round only this tie-break key,
    never a measured diagnostic or threshold comparison. Equal geometry can
    safely use identity as a final tie-break because compatibility is identical.
    """
    target = np.asarray([geometry["target"]["ca"][p] for p in sorted(geometry["target"]["ca"])])
    binder = np.asarray([geometry["binder"]["ca"][p] for p in sorted(geometry["binder"]["ca"])])
    centre = target.mean(axis=0)
    vectors = target - centre
    first = vectors[np.argmax(np.sum(vectors ** 2, axis=1))]
    x_axis = first / np.linalg.norm(first)
    perpendicular = vectors - (vectors @ x_axis)[:, None] * x_axis
    second = perpendicular[np.argmax(np.sum(perpendicular ** 2, axis=1))]
    y_axis = second / np.linalg.norm(second)
    basis = np.column_stack((x_axis, y_axis, np.cross(x_axis, y_axis)))
    canonical = np.concatenate(((target - centre) @ basis, (binder - centre) @ basis))
    return (tuple(sorted(geometry["contacts"])), tuple(np.round(canonical.ravel(), 8)))


def _priority(annotation: dict) -> str:
    site = annotation["pose_assessment"]["site"]
    if annotation["pose_consensus_status"] == "disabled":
        return "disabled"
    if site["status"] in {"no_contact", "threshold_unmet"}:
        return "site_unmet"
    if site["status"] not in {"not_requested", "threshold_met"}:
        return "site_not_assessed"
    if site["confidence_gate_eligible"] is False:
        return "confidence_gate_unmet"
    if site["confidence_gate_eligible"] is None:
        return "confidence_not_assessed"
    if annotation["pose_assessment"]["status"] != "assessed" or not annotation["pose_assessment"].get("source", {}).get("contact_positions"):
        return "geometry_not_assessed"
    if annotation["pose_consensus_status"] == "multi_engine_supported":
        return "multi_engine_supported"
    # A good model remains a plausible observation when other engines choose
    # different poses or its generation-stage reference is absent/inconsistent.
    return "single_engine_promising"


def assess_models(rows: list[dict], cfg: dict | None = None) -> dict:
    """Assess existing observations without mutating their scores or gate records.

    Per-model annotations have the same input order, including failed/missing
    observations. Only models with verified sequences and coordinate roles enter
    pair comparisons. Candidate grouping requires exact sequence hashes and the
    case boundary. Duplicate model IDs and conflicting candidate IDs fail closed.
    """
    policy = settings(cfg)
    models, geometry, groups = [], {}, {}
    for index, row in enumerate(rows):
        _checkpoint()
        site = _site(row)
        annotation = {"model_id": _text_id(row, ("model_id", "id", "design_id")), "input_index": index,
                      "site_satisfied": site["satisfied"], "site_selection_eligible": site["selection_eligible"],
                      "pose_cluster_id": None, "pose_supporting_engines": None,
                      "pose_quality_supporting_engines": None, "pose_consensus_status": "not_assessed",
                      "design_consistency_status": "not_assessed", "design_binder_rmsd_A": None,
                      "review_rank": None, "review_rank_scope": None,
                      "pose_assessment": {"status": "not_assessed", "reason": None, "site": site,
                                          "policy_version": policy["version"], "policy_sha256": policy["sha256"]}}
        models.append(annotation)
        if not policy["enabled"]:
            annotation.update(pose_consensus_status="disabled", design_consistency_status="disabled")
            annotation["pose_assessment"].update(status="disabled", reason="Pose assessment disabled by frozen policy")
            continue
        try:
            identity = _identity(row, index)
            annotation["pose_assessment"]["identity"] = identity
            groups.setdefault(identity["candidate_key"], []).append(index)
            evaluated = row.get("final_evaluated")
            if (isinstance(evaluated, (bool, np.bool_)) and not bool(evaluated)) or row.get("prediction_status") not in (None, "complete", "completed", "predicted", "success", "succeeded"):
                raise ValueError("Prediction observation is failed, incomplete or interrupted")
            source = _source(row)
            annotation["pose_assessment"]["source"] = {k: source[k] for k in ("sha256", "format", "path", "size")}
            observed = _geometry(row, policy, source=source)
            geometry[index] = observed
            design = _design(row, observed, policy)
            annotation.update(design_consistency_status=design["status"], design_binder_rmsd_A=design["binder_rmsd_A"])
            annotation["pose_assessment"].update(status="assessed", source=observed["source"],
                                               design_consistency=design)
        except _INTERRUPTIONS:
            raise
        except _INPUT_ERRORS as exc:
            annotation["pose_assessment"]["reason"] = f"{type(exc).__name__}: {exc}"
    # Validate identifiers globally before any comparison; never silently alias.
    model_ids, candidate_ids = {}, {}
    for index, model in enumerate(models):
        identity = model["pose_assessment"].get("identity")
        if model["model_id"]:
            model_ids.setdefault(model["model_id"], []).append(index)
        if identity:
            candidate_ids.setdefault((identity["case_id"], identity["candidate_id"]), []).append(index)
    ambiguous = {i for indices in model_ids.values() if len(indices) > 1 for i in indices}
    for indices in candidate_ids.values():
        keys = {models[i]["pose_assessment"]["identity"]["candidate_key"] for i in indices}
        if len(keys) > 1:
            ambiguous.update(indices)
    for index in ambiguous:
        models[index]["pose_assessment"].update(status="not_assessed", reason="Ambiguous duplicate model or conflicting candidate identity")
        models[index].update(design_consistency_status="not_assessed", design_binder_rmsd_A=None)
        geometry.pop(index, None)
    pairs, candidates = [], []
    for key, indices in sorted(groups.items()):
        valid = sorted((i for i in indices if i in geometry), key=lambda i: models[i]["model_id"])
        compatible = {}
        for offset, left in enumerate(valid):
            for right in valid[offset + 1:]:
                _checkpoint()
                pair = _compare(geometry[left], geometry[right], policy)
                pair.update(left_model_id=models[left]["model_id"], right_model_id=models[right]["model_id"],
                            candidate_key=key, left_source_sha256=geometry[left]["source"]["sha256"],
                            right_source_sha256=geometry[right]["source"]["sha256"],
                            sequence_sha256=models[left]["pose_assessment"]["identity"]["sequence_sha256"],
                            target_sequence_sha256=models[left]["pose_assessment"]["identity"]["target_sequence_sha256"])
                pairs.append(pair)
                compatible[frozenset((left, right))] = pair["similar_pose"] is True
        clusters = []
        # An unqualified observation must never separate two qualifying
        # engines. Geometry ordering also removes the old alphabetical-engine
        # dependence while retaining the documented greedy all-pairs policy.
        cluster_order = sorted(valid, key=lambda i: (not _qualified(models[i]),
                                                    _geometry_order(geometry[i]), models[i]["model_id"]))
        for index in cluster_order:
            cluster = next((c for c in clusters if all(compatible[frozenset((index, j))] for j in c)), None)
            if cluster is None:
                clusters.append([index])
            else:
                cluster.append(index)
        all_engines = {models[i]["pose_assessment"]["identity"]["engine"] for i in valid}
        summaries = []
        for cluster in clusters:
            members = sorted(models[i]["model_id"] for i in cluster)
            cluster_id = _digest(json.dumps({"candidate_key": key, "models": members}, sort_keys=True))
            engines = sorted({models[i]["pose_assessment"]["identity"]["engine"] for i in cluster})
            quality_engines = sorted({models[i]["pose_assessment"]["identity"]["engine"] for i in cluster
                                      if _qualified(models[i])})
            design_engines = sorted({models[i]["pose_assessment"]["identity"]["engine"] for i in cluster
                                     if _qualified(models[i])
                                     and models[i]["design_consistency_status"] in {"consistent", "not_applicable"}})
            if len(quality_engines) >= policy["min_supporting_engines"]:
                status = "multi_engine_supported"
            elif quality_engines:
                status = "single_engine_promising"
            elif len(engines) >= policy["min_supporting_engines"]:
                status = "quality_support_insufficient"
            else:
                status = "single_engine" if len(all_engines) <= 1 else "discordant"
            summary = {"cluster_id": cluster_id, "model_ids": members, "engines": engines,
                       "supporting_engines": len(engines), "quality_supporting_engines": len(quality_engines),
                       "quality_engines": quality_engines, "design_consistent_qualified_engines": len(design_engines),
                       "design_consistent_engines": design_engines, "model_count": len(cluster), "status": status}
            summaries.append(summary)
            for index in cluster:
                # A candidate cluster can have good observations while this
                # particular model failed its recorded quality or site check.
                model_status = status if _qualified(models[index]) else (
                    "quality_support_insufficient" if geometry[index]["contacts"] else "not_assessed")
                models[index].update(pose_cluster_id=cluster_id, pose_supporting_engines=len(engines),
                                     pose_quality_supporting_engines=len(quality_engines), pose_consensus_status=model_status)
                models[index]["pose_assessment"].update(cluster=summary)
        identities = [models[i]["pose_assessment"]["identity"] for i in indices]
        candidates.append({"candidate_key": key, "case_id": identities[0]["case_id"],
                           "candidate_ids": sorted({v["candidate_id"] for v in identities}),
                           "sequence_sha256": identities[0]["sequence_sha256"],
                           "target_sequence_sha256": identities[0]["target_sequence_sha256"],
                           "model_ids": [models[i]["model_id"] for i in indices],
                           "valid_model_count": len(valid), "engine_count": len(all_engines), "clusters": summaries,
                           "pose_supporting_engines": max((c["supporting_engines"] for c in summaries), default=0),
                           "pose_quality_supporting_engines": max((c["quality_supporting_engines"] for c in summaries), default=0),
                           "status": "multi_engine_supported" if any(c["status"] == "multi_engine_supported" for c in summaries) else (
                               "single_engine_promising" if any(c["status"] == "single_engine_promising" for c in summaries) else "not_assessed")})
    for model in models:
        model["review_priority"] = _priority(model)
        model["pose_assessment"]["review_priority"] = model["review_priority"]
    tiers = ["multi_engine_supported", "single_engine_promising",
             "confidence_gate_unmet", "site_unmet", "site_not_assessed", "confidence_not_assessed", "geometry_not_assessed"]
    ranking_rule = {"version": "pose_review_rank_v3", "calibrated": False,
                    "scope": "case_id_and_exact_target_sequence_sha256", "ties": "dense_equal_features",
                    "priority_tiers": tiers,
                    "features": ["priority_tier_ascending", "quality_supporting_engines_descending",
                                 "supporting_engines_descending"],
                    "generation_consistency": "diagnostic_only", "score_aggregation": "none",
                    "candidate_tier": "best_observed_model_tier_site_satisfied_before_site_unmet",
                    "interpretation": "Rank one can remain held; single-engine promising is not rejected for lack of agreement, and neither class proves biological binding"}
    ranked_scopes = {}
    for candidate in candidates:
        indices = groups[candidate["candidate_key"]]
        member_models = [models[i] for i in indices]
        best_cluster = max(candidate["clusters"], key=lambda c: (c["quality_supporting_engines"], c["supporting_engines"]), default=None)
        priority_set = {m["review_priority"] for m in member_models}
        # Distinct qualified engines must share one all-pairs-compatible pose;
        # support may never be added across separate candidate pose clusters.
        fully_supported = any(c["quality_supporting_engines"] >= policy["min_supporting_engines"]
                              for c in candidate["clusters"])
        priority = "multi_engine_supported" if fully_supported else next((p for p in tiers[1:] if p in priority_set), "geometry_not_assessed")
        scope = _digest(json.dumps({"case_id": candidate["case_id"],
                                   "target_sequence_sha256": candidate["target_sequence_sha256"]}, sort_keys=True))
        candidate.update(status=priority, review_priority=priority, review_priority_reason=priority,
                         review_rank=None, review_rank_scope=scope,
                         pose_supporting_engines=best_cluster["supporting_engines"] if best_cluster else 0,
                         pose_quality_supporting_engines=best_cluster["quality_supporting_engines"] if best_cluster else 0,
                         design_consistent_qualified_engines=best_cluster["design_consistent_qualified_engines"] if best_cluster else 0)
        if any(i in ambiguous for i in indices):
            candidate["status"] = candidate["review_priority"] = candidate["review_priority_reason"] = "identity_not_assessed"
            continue
        feature = (tiers.index(priority), -(best_cluster["quality_supporting_engines"] if best_cluster else 0),
                   -(best_cluster["supporting_engines"] if best_cluster else 0))
        ranked_scopes.setdefault(scope, []).append((feature, candidate))
    for entries in ranked_scopes.values():
        ranks = {feature: rank for rank, feature in enumerate(sorted({feature for feature, _ in entries}), 1)}
        for feature, candidate in entries:
            candidate["review_rank"] = ranks[feature]
            for index in groups[candidate["candidate_key"]]:
                models[index].update(review_rank=candidate["review_rank"], review_rank_scope=candidate["review_rank_scope"],
                                     review_priority_reason=candidate["review_priority_reason"],
                                     candidate_review_priority=candidate["review_priority"])
    for model in models:
        model.setdefault("review_priority_reason", model["review_priority"])
    return {"schema_version": SCHEMA_VERSION, "policy": policy, "models": models,
            "candidates": candidates, "pairs": pairs,
            "ranking_rule": ranking_rule,
            "interpretation": "Exploratory review priority; single-engine promising is retained despite absent agreement. Correlated engine agreement is not independent replication, binding or KD. Pose disagreement or generation inconsistency does not establish biological non-binding."}
