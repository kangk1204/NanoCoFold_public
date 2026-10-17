"""Full/cleaved target definitions and conservative paired-result aggregation.

All positions in this public interface are 1-based positions in the full sequence.
Confidence differences are descriptive, never a binding probability or a KD.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import pandas as pd

from .config import cfg_get, resolve_path
from . import score_complex, target_prep
from .utils import pdb_io, seq

STATES = ("cleaved", "full")
METRICS = ("ipsae", "iptm", "interface_plddt", "interface_pae", "hotspot_contact_frac", "n_clashes")
REQUIRED_METRICS = ("ipsae", "iptm", "hotspot_contact_frac", "n_clashes")


def comparison_conditions(cfg: dict, provenance: dict) -> str:
    """Identify the producer's declared prediction conditions, not observed metadata."""
    value = {"engine": cfg_get(cfg, "specificity.engine", "boltz2"), "restraint": "none",
             "context": cfg_get(cfg, "specificity.context"), "screen": cfg_get(cfg, "screen"),
             "score": cfg_get(cfg, "score"), "provenance": provenance}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _conditions_status(records: pd.DataFrame, expected: str | None) -> str:
    """A unanimous observed value cannot establish its own approved provenance."""
    valid_hash = lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    if not valid_hash(expected):
        return "comparison_conditions_unverified"
    values = records.get("comparison_conditions", pd.Series(dtype=object))
    if values.empty or not values.map(valid_hash).all():
        return "comparison_conditions_missing_or_invalid"
    if not values.eq(expected).all():
        return "comparison_conditions_mismatch"
    return "verified"


def observation_metrics_complete(record: dict) -> bool:
    """Undefined interface values are valid only for an observed zero-contact pose."""
    def finite(value):
        try:
            return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return False
    if not all(finite(record.get(key)) for key in REQUIRED_METRICS):
        return False
    if all(finite(record.get(key)) for key in ("interface_plddt", "interface_pae")):
        return True
    contacts = record.get("n_contacts")
    return finite(contacts) and float(contacts) == 0


def _position(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}: 1-based 정수가 필요합니다.")
    return value


def resolve_definition(cfg: dict) -> dict:
    """Validate a biological cleavage definition before creating any run artifacts."""
    spec = cfg_get(cfg, "specificity", {}) or {}
    for dotted in ("run.mock", "run.dry_run", "run.resume", "specificity.secondary_chai"):
        value = cfg_get(cfg, dotted, False)
        if not isinstance(value, bool):
            raise ValueError(f"{dotted} 에는 YAML true/false만 허용합니다.")
    if not cfg_get(cfg, "run.mock", False) and (cfg_get(cfg, "structure.provider") == "mock" or
            cfg_get(cfg, "mature.prefilter.mode") == "mock"):
        raise ValueError("실제 캠페인에서 mock 타깃/프리필터를 사용할 수 없습니다.")
    if cfg_get(cfg, "screen.msa_mode", "single") not in {"single", "local"}:
        raise ValueError("특이성 비교는 동일한 single 또는 local MSA 정책을 사용합니다.")
    reserved = {"--seed", "--use_msa_server", "--use-msa-server", "--constraint-path", "--msa-directory",
                "--out_dir", "--out-dir", "--fasta", "--recycling_steps", "--num-trunk-recycles",
                "--diffusion_samples", "--num-diffn-samples", "--num-diffn-timesteps", "--num_subsampled_msa",
                "--sampling_steps", "--checkpoint", "--model"}
    for dotted in ("screen.extra_args", "score.boltz2_extra_args"):
        if any(str(arg).split("=", 1)[0] in reserved for arg in (cfg_get(cfg, dotted, []) or [])):
            raise ValueError(f"{dotted} 에서 비교 조건/seed/모델을 우회할 수 없습니다. 전용 설정을 사용하세요.")
    for key in ("species", "isoform", "sequence_source", "context", "hotspot_rationale"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError(f"specificity.{key} 를 명시하세요.")
    if spec["context"] not in {"isolated", "complex"}:
        raise ValueError("specificity.context 는 isolated 또는 complex 입니다.")
    if bool(cfg_get(cfg, "target.aux_chains")) != (spec["context"] == "complex"):
        raise ValueError("complex 문맥에는 target.aux_chains 가 필요하며 isolated 문맥에는 사용할 수 없습니다.")
    # Structure alone cannot establish an isoform's complete sequence.
    if not cfg_get(cfg, "target.sequence") and not cfg_get(cfg, "target.fasta"):
        raise ValueError("전장 target.sequence 또는 단일 서열 target.fasta 가 필요합니다.")
    fasta = resolve_path(cfg, cfg_get(cfg, "target.fasta"))
    if fasta and len(seq.read_fasta(fasta, candidate_ids=False)) != 1:
        raise ValueError("전장 FASTA에는 정확히 한 서열이 필요합니다.")
    full = target_prep.resolve_target_sequence(cfg)
    cut = _position((spec.get("cleavage") or {}).get("after"), "specificity.cleavage.after")
    if not 1 <= cut < len(full):
        raise ValueError("절단 위치는 전장 서열 내부의 결합이어야 합니다 (1 <= after < length).")
    retain = (spec.get("cleavage") or {}).get("retain")
    if retain not in {"N", "C"}:
        raise ValueError("specificity.cleavage.retain 에 N 또는 C를 지정하세요.")
    cleaved = full[:cut] if retain == "N" else full[cut:]
    supplied = spec.get("cleaved_sequence")
    if supplied and seq.clean_sequence(supplied) != cleaved:
        raise ValueError("입력 절단형 서열이 전장 서열/절단 위치/조각과 일치하지 않습니다.")
    mode = spec.get("epitope_mode", "both")
    if mode not in {"terminal", "exposed", "both"}:
        raise ValueError("specificity.epitope_mode 는 terminal|exposed|both 입니다.")
    positions = spec.get("hotspots_full")
    if not isinstance(positions, list) or not positions:
        raise ValueError("specificity.hotspots_full 에 전장 기준 서열 위치 목록을 지정하세요.")
    positions = sorted(set(_position(p, "hotspots_full") for p in positions))
    start, end = (1, cut) if retain == "N" else (cut + 1, len(full))
    if any(not start <= p <= end for p in positions):
        raise ValueError("모든 결합 부위는 보존된 절단 조각 안에 있어야 합니다.")
    neo_full = cut if retain == "N" else cut + 1
    if mode in {"terminal", "both"} and neo_full not in positions:
        raise ValueError("말단 인식 평가에는 새 말단 잔기를 hotspots_full 에 포함하세요.")
    seeds = spec.get("seeds", [42, 43, 44])
    if not isinstance(seeds, list) or len(seeds) < 3 or len(seeds) != len(set(seeds)):
        raise ValueError("specificity.seeds 에 서로 다른 seed를 3개 이상 지정하세요.")
    for seed in seeds:
        if _position(seed, "seed") < 0:
            raise ValueError("seed 는 음수가 아니어야 합니다.")
    for key, default, upper in (("stage1_n", 48, 48), ("stage2_n", 32, 32), ("final_n", 24, 32)):
        value = _position(spec.get(key, default), f"specificity.{key}")
        if not 1 <= value <= upper:
            raise ValueError(f"specificity.{key}: 1..{upper} 범위가 필요합니다.")
    if spec.get("final_n", 24) > spec.get("stage2_n", 32) or spec.get("stage2_n", 32) > spec.get("stage1_n", 48):
        raise ValueError("final_n <= stage2_n <= stage1_n 이어야 합니다.")
    if not math.isfinite(float(spec.get("budget_hours", 48))) or float(spec.get("budget_hours", 48)) <= 0:
        raise ValueError("specificity.budget_hours 는 양의 유한수여야 합니다.")
    if spec.get("engine", "boltz2") not in {"boltz2", "chai1"}:
        raise ValueError("specificity.engine 는 boltz2|chai1 입니다.")
    if cfg_get(cfg, "rank.fallback_to_ineligible", False):
        raise ValueError("특이성 캠페인에서는 fallback_to_ineligible 을 사용할 수 없습니다.")
    return {
        "full_sequence": full, "cleaved_sequence": cleaved,
        "after": cut, "retain": retain, "start": start, "end": end,
        "neo_full_position": neo_full, "neo_cleaved_position": neo_full - start + 1,
        "hotspots_full": positions, "hotspots_cleaved": [p - start + 1 for p in positions],
        "epitope_mode": mode, "context": spec["context"], "species": spec["species"],
        "isoform": spec["isoform"], "sequence_source": spec["sequence_source"],
        "hotspot_rationale": spec["hotspot_rationale"], "seeds": seeds,
        "terminal_modeling_status": "unassessed" if mode != "exposed" else "not_applicable",
    }


def state_config(cfg: dict, definition: dict, state: str) -> dict:
    if state not in STATES:
        raise ValueError(state)
    result = copy.deepcopy(cfg)
    result["specificity"]["enabled"] = False  # Never recursively launch a campaign.
    result["target"].update(sequence=definition[f"{state}_sequence"], fasta=None,
                             hotspots=[f"seq{p}" for p in definition[f"hotspots_{state}"]])
    if state == "cleaved":
        result["target"]["pdb"] = cfg_get(cfg, "specificity.cleaved_pdb")
        result["target"]["chain"] = cfg_get(cfg, "specificity.cleaved_chain", cfg_get(cfg, "target.chain", "A"))
    result["truncate"]["enabled"] = False  # Do not create artificial termini in comparisons.
    if cfg_get(cfg, "run.mode") != "design" and not result["target"].get("pdb"):
        result["structure"]["provider"] = "none"
    return result


def residue_mapping(cfg: dict, definition: dict) -> pd.DataFrame:
    """Keep sequence numbering and original PDB insertion codes; reject mismatches."""
    structures = {}
    for state in STATES:
        scfg = state_config(cfg, definition, state)
        path = resolve_path(scfg, cfg_get(scfg, "target.pdb"))
        mapping = {}
        if path:
            if not path.is_file():
                raise FileNotFoundError(path)
            chain = str(cfg_get(scfg, "target.chain", "A"))
            _, mapping, _ = target_prep._structure_index_maps(path, chain, definition[f"{state}_sequence"])
            structure_chain = pdb_io.get_chain(pdb_io.load_structure(path), chain)
            residues = {pdb_io.residue_label(r, chain): r for r in structure_chain if r.id[0] == " "}
            # Verify mapped amino acids instead of trusting a permissive alignment.
            for pos, label in mapping.items():
                residue = residues.get(label)
                if residue is not None and seq.AA3TO1.get(residue.resname, "X") != definition[f"{state}_sequence"][pos - 1]:
                    raise ValueError(f"{state} 구조/서열 불일치: {label}")
            if any(p not in mapping for p in definition[f"hotspots_{state}"]):
                raise ValueError(f"{state} 구조에서 hotspot 잔기가 누락되었습니다.")
        structures[state] = mapping
    return pd.DataFrame([
        {"full_position": p, "aa": aa,
         "cleaved_position": p - definition["start"] + 1 if definition["start"] <= p <= definition["end"] else None,
         "full_pdb_label": structures["full"].get(p),
         "cleaved_pdb_label": structures["cleaved"].get(p - definition["start"] + 1),
         "is_hotspot": p in definition["hotspots_full"], "is_neo_terminus": p == definition["neo_full_position"]}
        for p, aa in enumerate(definition["full_sequence"], 1)
    ])


def aggregate(candidates: pd.DataFrame, records: pd.DataFrame, cfg: dict, seeds: list[int], *,
              expected_conditions: str | None = None) -> pd.DataFrame:
    """Require every pair and an independently approved producer condition hash.

    Without expected_conditions, historical observations remain readable but
    unverified; observed unanimity cannot grant eligibility. Low confidence is
    never interpreted as nonbinding.
    """
    rows = []
    expected = {(state, seed) for state in STATES for seed in seeds}
    engine = cfg_get(cfg, "specificity.engine", "boltz2")
    for _, candidate in candidates.iterrows():
        row = candidate.to_dict()
        subset = records[records.design_id.eq(row["design_id"])] if not records.empty else pd.DataFrame()
        subset = subset[subset.engine.eq(engine)] if not subset.empty else subset
        found = set(zip(subset.get("target_state", []), subset.get("seed", [])))
        complete = found == expected and len(subset) == len(expected)
        complete = complete and subset.status.eq("complete").all()
        conditions_status = _conditions_status(subset, expected_conditions)
        complete = complete and conditions_status == "verified" and subset.restraint.eq("none").all()
        sequence_hash = hashlib.sha256(str(row["sequence"]).encode()).hexdigest()
        for key, expected_value in (("sequence_sha256", sequence_hash), ("context", cfg_get(cfg, "specificity.context")),
                                     ("evidence_type", "mock" if cfg_get(cfg, "run.mock") else "real")):
            if key not in subset or not subset[key].eq(expected_value).all():
                complete = False
        complete = complete and all(observation_metrics_complete(r) for r in subset.to_dict("records"))
        row.update(specificity_complete=bool(complete), comparison_status="insufficient",
                   comparison_conditions_status=conditions_status,
                   terminal_modeling_status="unassessed" if cfg_get(cfg, "specificity.epitope_mode", "both") != "exposed" else "not_applicable",
                   evidence_type="mock" if cfg_get(cfg, "run.mock", False) else "real", review_status="pending")
        for state in STATES:
            observations = subset[subset.target_state.eq(state)] if not subset.empty else pd.DataFrame()
            for metric in METRICS:
                values = pd.to_numeric(observations.get(metric, pd.Series(dtype=float)), errors="coerce")
                row[f"{state}_{metric}"] = values.median() if len(values) else float("nan")
                row[f"{state}_{metric}_min"] = values.min() if len(values) else float("nan")
                row[f"{state}_{metric}_max"] = values.max() if len(values) else float("nan")
            if not observations.empty:
                row[f"{state}_cif_path"] = observations.iloc[0].get("cif_path", "")
        if complete:
            # Any confident full-form interface flags potential cross-binding.
            full_rows = subset[subset.target_state.eq("full")]
            concern = any(score_complex.pass_flags(r, cfg)["pass_ipsae"] and
                          score_complex.pass_flags(r, cfg)["pass_iptm"] for r in full_rows.to_dict("records"))
            row["comparison_status"] = "cross_binding_risk" if concern else "complete"
        if not subset.empty and "hotspot_contacts" in subset:
            contact_sets = [set(r) if isinstance(r, list) else set() for r in subset.loc[subset.target_state.eq("cleaved"), "hotspot_contacts"]]
            row["consistent_hotspot_positions"] = ";".join(map(str, sorted(set.intersection(*contact_sets)))) if contact_sets else ""
        else:
            row["consistent_hotspot_positions"] = ""
        row["delta_ipsae"] = row["cleaved_ipsae"] - row["full_ipsae"] if complete else float("nan")
        row["positive_quality_pass"] = bool(complete and all(
            all(score_complex.pass_flags(r, cfg).get(flag, False) for flag in
                ("pass_ipsae", "pass_iptm", "pass_plddt", "pass_hotspot", "pass_clash"))
            for r in subset[subset.target_state.eq("cleaved")].to_dict("records")))
        rows.append(row)
    columns = list(candidates.columns) + ["specificity_complete", "comparison_status", "positive_quality_pass"]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
