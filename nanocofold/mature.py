from __future__ import annotations

import json
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

from .config import cfg_get, cfg_seed, resolve_path
from .utils import seq as seq_utils, shell

AMINO_ACIDS = list("ACDEFGHIKLMNPQRSTVWY")
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"


def resolve_wt_sequence(cfg: dict) -> str:
    sequence = cfg_get(cfg, "mature.wt_sequence") or ""
    if sequence:
        return seq_utils.clean_sequence(sequence)
    fasta = resolve_path(cfg, cfg_get(cfg, "mature.wt_fasta"))
    if fasta and fasta.exists():
        records = seq_utils.read_fasta(fasta)
        if len(records) != 1:
            raise ValueError("WT FASTA에는 서열을 1개만 넣으세요.")
        if records:
            return seq_utils.clean_sequence(next(iter(records.values())))
    raise ValueError("mature.wt_sequence 또는 mature.wt_fasta 로 야생형 VHH 서열을 지정하세요.")


def validate_cdr_ranges(ranges: dict, sequence_length: int) -> dict[str, tuple[int, int]]:
    """CDR positions are source-sequence indices, never IMGT numbers."""
    if not isinstance(ranges, dict) or not ranges:
        raise ValueError("CDR 구간을 서열의 1-based 시작·끝 위치로 지정하세요.")
    normalized, occupied = {}, set()
    for name, bounds in ranges.items():
        if name not in {"CDR1", "CDR2", "CDR3"} or not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
            raise ValueError("CDR 구간은 CDR1/CDR2/CDR3의 [시작, 끝] 정수 목록이어야 합니다.")
        if any(type(value) is not int for value in bounds) or not 1 <= bounds[0] <= bounds[1] <= sequence_length:
            raise ValueError("CDR 구간은 WT 서열 범위 안의 1-based 정수 위치여야 합니다.")
        positions = set(range(bounds[0], bounds[1] + 1))
        if positions & occupied:
            raise ValueError("CDR 구간은 서로 겹칠 수 없습니다.")
        occupied.update(positions)
        normalized[name] = tuple(bounds)
    return normalized


def resolve_cdr_ranges(
    cfg: dict,
    wt_sequence: str,
    mock: bool = False,
    dry_run: bool = False,
    log_path: Path | None = None,
) -> tuple[dict[str, tuple[int, int]], list[str]]:
    """ANARCI(IMGT) 또는 config 지정값으로 CDR 범위(1-based inclusive)를 확정한다."""
    warnings: list[str] = []
    source = str(cfg_get(cfg, "mature.cdr.source", "auto"))
    configured = cfg_get(cfg, "mature.cdr.regions", {}) or {}
    def config_ranges():
        if not configured and mock:
            # Synthetic execution only: these positions are not real annotations.
            length = len(wt_sequence)
            return validate_cdr_ranges({"CDR1": [max(1, length // 4), max(1, length // 4)],
                                        "CDR2": [max(2, length // 2), max(2, length // 2)],
                                        "CDR3": [max(3, length * 3 // 4), max(3, length * 3 // 4)]}, length)
        return validate_cdr_ranges(configured, len(wt_sequence))
    if dry_run and source != "config":
        warnings.append("dry-run: ANARCI를 건너뛰고 config CDR 범위를 사용합니다.")
        source = "config"
    if source == "config" or mock:
        ranges = config_ranges()
        if not ranges:
            raise ValueError("mature.cdr.regions 가 비어 있습니다.")
        return ranges, warnings

    env = resolve_path(cfg, cfg_get(cfg, "mature.anarci.env"))
    script = SCRIPTS_DIR / "anarci_run.py"
    try:
        argv = [str(shell.env_python(env)), str(script), wt_sequence, str(cfg_get(cfg, "mature.cdr.scheme", "imgt"))]
        result = shell.run_in_env(env, argv, log_path=log_path, check=False, quiet=True)
    except shell.ToolError as exc:
        warnings.append("ANARCI 실행 환경이 없어 config CDR 범위로 대체합니다. envs/setup_antibody.sh 를 실행하세요.")
        ranges = config_ranges()
        if not ranges:
            raise ValueError("ANARCI 환경 없음 + mature.cdr.regions 도 없습니다.") from exc
        return ranges, warnings
    if result.returncode != 0:
        warnings.append("ANARCI 실행 실패 → config CDR 범위로 대체합니다.")
        ranges = config_ranges()
        if not ranges:
            raise ValueError("ANARCI 실패 후 사용할 mature.cdr.regions 가 없습니다.")
        return ranges, warnings

    payload = _parse_json_from_log(result)
    if payload is None or "cdr_ranges" not in payload:
        warnings.append("ANARCI 출력 파싱 실패 → config CDR 범위로 대체합니다.")
        ranges = config_ranges()
        return ranges, warnings

    if payload.get("scheme_supported") is False:
        warnings.append(f"ANARCI scheme={cfg_get(cfg, 'mature.cdr.scheme', 'imgt')} 는 IMGT CDR 경계와 호환되지 않습니다.")
    ranges = {name: (int(value[0]), int(value[1])) for name, value in payload["cdr_ranges"].items()}
    invalid = {name: value for name, value in ranges.items() if not (1 <= value[0] <= value[1] <= len(wt_sequence))}
    if invalid:
        warnings.append(f"ANARCI CDR 범위가 서열 길이를 벗어납니다: {invalid} → 해당 범위 제외")
        ranges = {name: value for name, value in ranges.items() if name not in invalid}
    missing = {"CDR1", "CDR2", "CDR3"} - set(ranges)
    if missing:
        warnings.append(f"ANARCI가 일부 CDR을 찾지 못했습니다: {sorted(missing)} (갭 처리·스킴 확인)")
    if not ranges:
        configured = cfg_get(cfg, "mature.cdr.regions", {}) or {}
        fallback = config_ranges()
        if fallback:
            warnings.append("유효한 ANARCI CDR이 없어 config CDR 범위를 사용합니다.")
            return fallback, warnings
    return validate_cdr_ranges(ranges, len(wt_sequence)), warnings


def _parse_json_from_log(result: shell.RunResult) -> dict | None:
    if result.log_path and result.log_path.exists():
        text = result.log_path.read_text(encoding="utf-8", errors="ignore")
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def resolve_cdr_ranges_batch(
    cfg: dict,
    sequences: dict[str, str],
    work_dir: Path,
    dry_run: bool = False,
) -> dict[str, dict]:
    """후보 서열 다수를 한 번의 ANARCI 실행으로 IMGT CDR 주석한다 (design/screen용 F10).

    반환: {candidate_id: {"CDR1": (start, end), ...}} — 실패/미지원 후보는 제외.
    """
    if not sequences:
        return {}
    env = resolve_path(cfg, cfg_get(cfg, "mature.anarci.env"))
    script = SCRIPTS_DIR / "anarci_run.py"
    work_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = work_dir / "anarci_batch.fasta"
    seq_utils.write_fasta({str(key): str(value) for key, value in sequences.items()}, fasta_path)
    if dry_run:
        return {}
    try:
        argv = [str(shell.env_python(env)), str(script), "--fasta", str(fasta_path),
                str(cfg_get(cfg, "mature.cdr.scheme", "imgt"))]
        result = shell.run_in_env(env, argv, log_path=work_dir / "anarci_batch.log", check=False, quiet=True)
    except shell.ToolError:
        return {}
    if result.returncode != 0:
        return {}
    payload = _parse_json_from_log(result)
    if payload is None or "results" not in payload:
        return {}
    annotated: dict[str, dict] = {}
    for candidate_id, entry in payload["results"].items():
        if "cdr_ranges" not in entry:
            continue
        annotated[candidate_id] = {name: (int(value[0]), int(value[1])) for name, value in entry["cdr_ranges"].items()}
    return annotated


def generate_dms_library(
    wt_sequence: str,
    cdr_ranges: dict[str, tuple[int, int]],
    exclude_cys: bool = True,
    protect_wt_cys: bool = True,
) -> pd.DataFrame:
    """CDR 영역 전수 단일 치환 라이브러리 (WT 포함).

    exclude_cys: 신규 Cys 도입 금지 (비정형 이황화 결합 방지)
    protect_wt_cys: 야생형 Cys 위치는 치환 대상에서 제외 (구조 이황화 결합 보존)
    """
    cdr_ranges = validate_cdr_ranges(cdr_ranges, len(wt_sequence))
    if not wt_sequence or any(aa not in AMINO_ACIDS for aa in wt_sequence):
        raise ValueError("DMS WT는 표준 아미노산 대문자 서열이어야 합니다.")
    rows: list[dict] = [{
        "variant_id": "WT",
        "region": "WT",
        "position": -1,
        "wt_aa": "-",
        "mut_aa": "-",
        "mutation": "WT",
        "sequence": wt_sequence,
    }]
    for region, (start, end) in cdr_ranges.items():
        for position in range(start, end + 1):
            if not 1 <= position <= len(wt_sequence):
                continue
            wt_aa = wt_sequence[position - 1]
            if protect_wt_cys and wt_aa == "C":
                continue
            for mut_aa in AMINO_ACIDS:
                if mut_aa == wt_aa:
                    continue
                if exclude_cys and mut_aa == "C":
                    continue
                sequence = wt_sequence[: position - 1] + mut_aa + wt_sequence[position:]
                rows.append({
                    "variant_id": f"{region}_{wt_aa}{position}{mut_aa}",
                    "region": region,
                    "position": position,
                    "wt_aa": wt_aa,
                    "mut_aa": mut_aa,
                    "mutation": f"{wt_aa}{position}{mut_aa}",
                    "sequence": sequence,
                })
    return pd.DataFrame(rows)


def resolve_cdr_annotations_batch(cfg, sequences, work_dir):
    """ANARCI heavy-domain/CDR validation with complete source-sequence coverage."""
    work_dir = Path(work_dir)
    ranges = resolve_cdr_ranges_batch(cfg, sequences, work_dir)
    payload = {}
    log = work_dir / "anarci_batch.log"
    if ranges and log.is_file():
        for line in reversed(log.read_text(errors="replace").splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get("results"), dict):
                    payload = value["results"]
                    break
            except ValueError:
                continue
    result = {}
    for identifier, sequence in sequences.items():
        entry = payload.get(identifier, {})
        if not isinstance(entry, dict):
            entry = {}
        numbering = entry.get("imgt_positions", [])
        reason = None
        if identifier not in ranges or entry.get("error"):
            reason = "anarci_numbering_unavailable"
        elif entry.get("chain_type") != "H":
            reason = "anarci_not_heavy_domain"
        elif (not isinstance(numbering, list) or any(not isinstance(p, dict) for p in numbering)
              or [p.get("index") for p in numbering] != list(range(1, len(sequence) + 1))
              or "".join(str(p.get("aa", "")) for p in numbering) != sequence):
            reason = "anarci_sequence_coverage_mismatch"
        elif set(ranges[identifier]) != {"CDR1", "CDR2", "CDR3"}:
            reason = "anarci_incomplete_cdrs"
        else:
            core = {p.get("imgt"): p.get("aa") for p in numbering if not str(p.get("insertion") or "").strip()}
            if core.get(23) != "C" or core.get(41) != "W" or core.get(104) != "C" or core.get(118) not in {"F", "W"}:
                reason = "anarci_heavy_core_mismatch"
        result[identifier] = {"valid": reason is None, "reason": reason,
                              "cdr_ranges": ranges.get(identifier, {}), "validation_kind": "anarci",
                              "anarci_validated": reason is None,
                              "chain_type": entry.get("chain_type")}
    return result


def generate_random_library(
    wt_sequence: str, cdr_ranges: dict[str, tuple[int, int]], *, size: int = 10000,
    min_mutations: int = 1, max_mutations: int = 5, seed: int = 42,
    exclude_cys: bool = True, protect_wt_cys: bool = True,
) -> pd.DataFrame:
    """Unique CDR-only random substitutions; size includes the WT control.

    Draw mutation count uniformly among non-exhausted requested counts, then
    positions without replacement and an alternative residue at each position.
    Duplicate draws retry within that count. A deterministic enumeration fallback
    after bounded retries prevents near-exhausted spaces from hanging.
    """
    if not isinstance(wt_sequence, str) or not wt_sequence or any(aa not in AMINO_ACIDS for aa in wt_sequence):
        raise ValueError("random 라이브러리 WT는 표준 아미노산의 대문자 서열이어야 합니다.")
    if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 100000:
        raise ValueError("random 라이브러리 size는 WT 포함 2–100000 사이의 정수여야 합니다.")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (min_mutations, max_mutations)) or not 1 <= min_mutations <= max_mutations <= 50:
        raise ValueError("random 변이 개수는 1 ≤ min_mutations ≤ max_mutations ≤ 50이어야 합니다.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("random seed는 0–4294967295 사이의 정수여야 합니다.")
    if not isinstance(exclude_cys, bool) or not isinstance(protect_wt_cys, bool):
        raise ValueError("random Cys 보호 설정은 true/false여야 합니다.")
    if not isinstance(cdr_ranges, dict) or not cdr_ranges:
        raise ValueError("random 라이브러리에는 검증된 CDR 구간이 필요합니다.")
    regions = {}
    for name, bounds in sorted(cdr_ranges.items()):
        if name not in {"CDR1", "CDR2", "CDR3"} or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError("random CDR 구간은 CDR1/CDR2/CDR3의 시작·끝 위치여야 합니다.")
        start, end = bounds
        if any(isinstance(v, bool) or not isinstance(v, int) for v in bounds) or not 1 <= start <= end <= len(wt_sequence):
            raise ValueError("random CDR 구간이 WT 서열 범위를 벗어났습니다.")
        for position in range(start, end + 1):
            if position in regions:
                raise ValueError("random CDR 구간이 서로 겹칩니다.")
            regions[position] = name
    positions = [p for p in sorted(regions) if not (protect_wt_cys and wt_sequence[p - 1] == "C")]
    if max_mutations > len(positions):
        raise ValueError(f"random max_mutations={max_mutations}가 변경 가능한 CDR 위치 {len(positions)}개보다 큽니다.")
    alternatives = {p: [aa for aa in AMINO_ACIDS if aa != wt_sequence[p - 1] and not (exclude_cys and aa == "C")] for p in positions}
    # Elementary symmetric polynomial gives the exact number of distinct
    # k-substitution sequences, including mixed 18/19-option positions.
    capacity = [1] + [0] * max_mutations
    for p in positions:
        for count in range(max_mutations, 0, -1):
            capacity[count] += capacity[count - 1] * len(alternatives[p])
    total_capacity = 1 + sum(capacity[min_mutations:max_mutations + 1])
    if size > total_capacity:
        raise ValueError(f"random 요청 {size}종이 중복 없는 가능한 공간 {total_capacity}종(WT 포함)을 초과합니다.")
    rng = np.random.default_rng(seed)
    seen = {count: set() for count in range(min_mutations, max_mutations + 1)}
    fallback_iterators = {}
    fallback_count = 0

    def enumerate_signatures(count):
        for selected in itertools.combinations(positions, count):
            for amino_acids in itertools.product(*(alternatives[p] for p in selected)):
                yield tuple(zip(selected, amino_acids))

    rows = [{"variant_id": "WT", "region": "WT", "position": -1, "wt_aa": "-", "mut_aa": "-",
             "mutation": "WT", "sequence": wt_sequence, "mutation_count": 0,
             "mutation_positions": "[]", "library_strategy": "random", "library_seed": seed}]
    while len(rows) < size:
        active = [count for count in seen if len(seen[count]) < capacity[count]]
        count = int(rng.choice(active))
        signature = None
        for _ in range(128):
            selected = sorted(int(p) for p in rng.choice(positions, count, replace=False))
            proposal = tuple((p, alternatives[p][int(rng.integers(len(alternatives[p])))]) for p in selected)
            if proposal not in seen[count]:
                signature = proposal
                break
        if signature is None:
            iterator = fallback_iterators.setdefault(count, enumerate_signatures(count))
            signature = next(candidate for candidate in iterator if candidate not in seen[count])
            fallback_count += 1
        seen[count].add(signature)
        sequence = list(wt_sequence)
        for position, aa in signature:
            sequence[position - 1] = aa
        changed = [position for position, _ in signature]
        wt_aas = [wt_sequence[position - 1] for position in changed]
        mutant_aas = [aa for _, aa in signature]
        rows.append({"variant_id": f"RND_{len(rows):06d}",
                     "region": ";".join(sorted({regions[p] for p in changed})),
                     "position": changed[0] if count == 1 else -1,
                     "wt_aa": ";".join(wt_aas), "mut_aa": ";".join(mutant_aas),
                     "mutation": ";".join(f"{wt_sequence[p - 1]}{p}{aa}" for p, aa in signature),
                     "sequence": "".join(sequence), "mutation_count": count,
                     "mutation_positions": json.dumps(changed), "library_strategy": "random", "library_seed": seed})
    frame = pd.DataFrame(rows)
    frame.attrs["library_generation"] = {"strategy": "random", "size_including_wt": size,
        "seed": seed, "min_mutations": min_mutations, "max_mutations": max_mutations,
        "mutable_cdr_positions": positions, "capacity_including_wt": total_capacity,
        "sampling": "uniform_nonexhausted_mutation_count_then_positions_and_alternative_aa",
        "duplicate_fallback_count": fallback_count}
    return frame


def generate_library(wt_sequence: str, cdr_ranges: dict, cfg: dict) -> pd.DataFrame:
    """Dispatch without changing the established DMS strategy or file columns."""
    options = dict(exclude_cys=bool(cfg_get(cfg, "mature.exclude.cys_substitution", True)),
                   protect_wt_cys=bool(cfg_get(cfg, "mature.exclude.wt_cys", True)))
    strategy = cfg_get(cfg, "mature.library.strategy", "dms")
    if strategy == "dms":
        return generate_dms_library(wt_sequence, cdr_ranges, **options)
    if strategy != "random":
        raise ValueError("mature.library.strategy는 dms/random 중 하나여야 합니다.")
    if cfg_get(cfg, "mature.prefilter.mode", "esm2") != "none":
        raise ValueError("다중 변이 random 라이브러리는 mature.prefilter.mode=none이 필요합니다.")
    return generate_random_library(wt_sequence, cdr_ranges,
        size=cfg_get(cfg, "mature.library.size", 10000),
        min_mutations=cfg_get(cfg, "mature.library.min_mutations", 1),
        max_mutations=cfg_get(cfg, "mature.library.max_mutations", 5),
        seed=cfg_seed(cfg, "mature.library.seed"), **options)


def run_prefilter(
    library: pd.DataFrame,
    wt_sequence: str,
    cfg: dict,
    phase_dir: Path,
    mock: bool = False,
    dry_run: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """ESM-2 ΔLLR (+ 선택적 AntiFold) 프리필터."""
    phase_dir.mkdir(parents=True, exist_ok=True)
    mode = "mock" if mock else str(cfg_get(cfg, "mature.prefilter.mode", "esm2"))
    info: dict = {"mode": mode, "warnings": []}
    if library.attrs.get("library_generation"):
        info["library_generation"] = library.attrs["library_generation"]
    if mode == "esm2" and (cfg_get(cfg, "mature.library.strategy", "dms") == "random"
                          or "mutation_count" in library and library["mutation_count"].gt(1).any()):
        raise ValueError("ESM 단일 변이 ΔLLR 프리필터는 다중 변이 라이브러리에 사용할 수 없습니다. mature.prefilter.mode=none을 설정하세요.")

    if dry_run:
        info.update({"total_variants": len(library), "selected": 0, "dry_run": True})
        info["warnings"].append("dry-run: 프리필터 점수와 후보 파일은 생성하지 않았습니다.")
        return library.iloc[0:0].copy(), info

    frame = library.copy()
    if mode == "mock" or dry_run:
        rng = np.random.default_rng(int(cfg_get(cfg, "run.seed", 42)))
        frame["esm_llr"] = rng.normal(0.0, 1.2, len(frame)).round(3)
        frame.loc[frame["variant_id"] == "WT", "esm_llr"] = 0.0
        frame["antifold"] = rng.normal(0.0, 0.8, len(frame)).round(3)
    elif mode == "none":
        frame["esm_llr"] = float("nan")
        frame["antifold"] = float("nan")
        info["warnings"].append("프리필터를 끈 실행: ESM/AntiFold는 미평가이며 점수로 선별하지 않았습니다.")
    elif mode == "esm2":
        esm_cfg = cfg_get(cfg, "mature.esm", {}) or {}
        env = resolve_path(cfg, esm_cfg.get("env"))
        script = SCRIPTS_DIR / "esm_llr.py"
        variants_csv = phase_dir / "variants_in.csv"
        frame[["variant_id", "position", "wt_aa", "mut_aa"]].to_csv(variants_csv, index=False)
        output_csv = phase_dir / "variants_esm.csv"
        argv = [
            str(shell.env_python(env)), str(script),
            "--variants", str(variants_csv),
            "--wt-sequence", wt_sequence,
            "--output", str(output_csv),
            "--model", str(esm_cfg.get("model", "esm2_t33_650M_UR50D")),
        ]
        from .diagnostics import _engine_extra_env
        shell.run_in_env(env, argv, extra_env=_engine_extra_env(cfg, "esm"),
                         log_path=phase_dir / "esm2.log", dry_run=dry_run)
        if output_csv.exists():
            scored = pd.read_csv(output_csv)
            frame = frame.drop(columns=[c for c in ("esm_llr",) if c in frame.columns]).merge(
                scored[["variant_id", "esm_llr"]], on="variant_id", how="left"
            )
        else:
            raise shell.ToolError(
                f"ESM 스코어링 출력이 없습니다: {output_csv}. "
                "후보가 조용히 사라지는 것을 막기 위해 중단합니다. envs/setup_esm.sh 설치와 "
                "mature.esm.env 경로를 확인하세요."
            )
        if frame["esm_llr"].isna().all():
            raise shell.ToolError("ESM ΔLLR 이 전부 NaN 입니다. esm_llr.py 로그를 확인하세요.")
        frame["antifold"] = _run_antifold(cfg, frame, wt_sequence, phase_dir, info["warnings"], dry_run)
    else:
        raise ValueError(f"지원하지 않는 prefilter mode: {mode}")

    threshold = float(cfg_get(cfg, "mature.prefilter.threshold", -2.0))
    threshold_mode = str(cfg_get(cfg, "mature.prefilter.threshold_mode", "absolute"))
    percentile = float(cfg_get(cfg, "mature.prefilter.percentile", 50.0))
    top_n = int(cfg_get(cfg, "mature.prefilter.top_n", 200))

    if threshold_mode == "percentile":
        values = pd.to_numeric(frame.loc[frame["variant_id"] != "WT", "esm_llr"], errors="coerce").dropna()
        cutoff = float(np.nanpercentile(values, max(0.0, 100.0 - percentile))) if len(values) else threshold
    else:
        cutoff = threshold
    frame["pass_llr"] = True if mode == "none" else (frame["esm_llr"] >= cutoff) | (frame["variant_id"] == "WT")

    threshold_applied = mode != "none" and bool(cfg_get(cfg, "mature.prefilter.enforce_threshold", True))
    passed = frame[frame["pass_llr"]].copy() if threshold_applied else frame.copy()
    if mode != "none":
        passed = passed.sort_values("esm_llr", ascending=False).head(top_n)
    if bool(cfg_get(cfg, "rank.include_wt_control", False)):
        wt_rows = frame[frame["variant_id"] == "WT"]
        if not wt_rows.empty and "WT" not in set(passed["variant_id"]):
            passed = pd.concat([wt_rows, passed], ignore_index=True)
    passed.to_csv(phase_dir / "prefiltered_candidates.csv", index=False)
    frame.to_csv(phase_dir / "esm2_scored_library.csv", index=False)

    info.update({
        "total_variants": len(frame),
        "passed_llr": int(frame["pass_llr"].sum()),
        "selected": len(passed),
        "threshold": threshold,
        "threshold_mode": threshold_mode,
        "cutoff_used": None if mode == "none" else round(cutoff, 3),
        "evaluated": mode != "none",
        "threshold_applied": threshold_applied,
        "evaluation_limit_applied": mode != "none",
        "prior_state": "not_evaluated" if mode == "none" else "evaluated",
        "percentile": percentile if threshold_mode == "percentile" else None,
    })
    if mode != "none" and not threshold_applied:
        info["warnings"].append("ESM/AntiFold 점수는 기록하며 ESM 컷으로 후보를 제외하지 않습니다. top_n 평가 한도는 적용됩니다.")
    return passed, info


def _run_antifold(
    cfg: dict,
    frame: pd.DataFrame,
    wt_sequence: str,
    phase_dir: Path,
    warnings: list[str],
    dry_run: bool,
) -> pd.Series:
    """AntiFold(BSD-3) 역폴딩 점수 (설치 패키지의 실제 API에 맞춘 연동, F05).

    - upstream CLI + explicit local-weight adapter (implicit downloads disabled)
    - 출력 CSV: pdb_posins/pdb_chain/pdb_res + 20 AA log-prob 컬럼
    - PDB 삽입코드(예: 111A)와 체인을 보존해 WT 서열 위치로 매핑한다. 결측은 NaN.
    """
    antifold_cfg = cfg_get(cfg, "mature.antifold", {}) or {}
    structure_path = resolve_path(cfg, cfg_get(cfg, "mature.wt_structure"))
    if not bool(antifold_cfg.get("enabled", False)):
        warnings.append("AntiFold 건너뜀 (mature.antifold.enabled=false).")
        return pd.Series(float("nan"), index=frame.index)
    if structure_path is None or not structure_path.exists():
        raise shell.ToolError("AntiFold를 켠 실행에는 mature.wt_structure가 필요합니다. 구조를 지정하거나 AntiFold를 끄세요.")

    try:
        chain_id, posins_to_seq, order_to_seq = validate_antifold_structure(
            structure_path, wt_sequence, antifold_cfg.get("antigen_chain"), antifold_cfg.get("nanobody_chain")
        )
    except (KeyboardInterrupt, shell.ExecutionCancelled, shell.ExecutionBudgetExceeded):
        raise
    except Exception as exc:  # noqa: BLE001
        raise shell.ToolError(f"AntiFold 필수 WT 구조 대응 실패: {exc}") from exc

    env = resolve_path(cfg, antifold_cfg.get("env"))
    if env is None:
        env = resolve_path(cfg, cfg_get(cfg, "mature.esm.env"))
    from .diagnostics import antifold_model_path, _sha256
    model_path = antifold_model_path(cfg, env)
    if not dry_run and (model_path is None or not model_path.is_file()):
        raise shell.ToolError("AntiFold 로컬 가중치가 없습니다. nanocofold setup --profile mature 실행 후 재시도하세요.")
    out_dir = phase_dir / "antifold"
    argv = [
        str(shell.env_python(env)) if not dry_run else "python",
        str(Path(__file__).resolve().parent / "scripts" / "antifold_local.py"),
        "--model-path", str(model_path or "models/antifold/model.pt"),
        "--pdb_file", str(structure_path),
        "--out_dir", str(out_dir),
        "--nanobody_chain", chain_id,
    ]
    if model_path and model_path.is_file():
        argv.extend(["--model-sha256", str(antifold_cfg.get("sha256") or _sha256(model_path))])
    antigen_chain = cfg_get(cfg, "mature.antifold.antigen_chain")
    if antigen_chain:
        argv += ["--antigen_chain", str(antigen_chain)]
    try:
        from .diagnostics import _engine_extra_env
        shell.run_in_env(env, argv, extra_env=_engine_extra_env(cfg, "antifold"),
                         log_path=phase_dir / "antifold.log", dry_run=dry_run)
    except shell.ToolError as exc:
        raise shell.ToolError(f"활성화된 AntiFold 실행 실패: {exc}") from exc

    scores = _parse_antifold_output(out_dir, posins_to_seq, order_to_seq, warnings)
    if scores is None:
        raise shell.ToolError("AntiFold 점수 파일이 없거나 손상되었습니다. antifold.log를 확인하세요.")

    values: list[float] = []
    for _, row in frame.iterrows():
        position = int(row["position"]) if row["position"] != -1 else 0
        if position <= 0:
            values.append(0.0 if row["variant_id"] == "WT" else float("nan"))
            continue
        logprob_mut = scores.get((position, str(row["mut_aa"])), float("nan"))
        logprob_wt = scores.get((position, str(row["wt_aa"])), float("nan"))
        if np.isnan(logprob_mut) or np.isnan(logprob_wt):
            values.append(float("nan"))
            continue
        values.append(float(logprob_mut - logprob_wt))
    if any(not np.isfinite(value) for value in values):
        raise shell.ToolError("AntiFold가 일부 변이 위치를 평가하지 못했습니다. WT 구조의 CDR 누락·잔기 대응·로그를 확인하세요.")
    return pd.Series(values, index=frame.index)


def validate_antifold_structure(path: Path, wt_sequence: str, antigen_chain=None, nanobody_chain=None):
    """The same WT correspondence gate is used before and during inference."""
    from .utils import pdb_io
    wt_sequence = seq_utils.clean_sequence(wt_sequence)
    structure = pdb_io.load_structure(path)
    warnings: list[str] = []
    chain, positions, order = _antifold_position_map(
        structure, wt_sequence, warnings, nanobody_chain=nanobody_chain, antigen_chain=antigen_chain
    )
    if not chain or not positions or warnings:
        raise ValueError("WT와 일치하는 VHH 체인·잔기 대응이 필요합니다. " + "; ".join(warnings))
    coverage = len(set(positions.values())) / max(1, len(wt_sequence))
    if coverage < .8:
        raise ValueError(f"WT 구조의 서열 대응 범위가 부족합니다: {coverage:.1%} (최소 80%).")
    if antigen_chain and (str(antigen_chain) == chain or str(antigen_chain) not in {c.id for c in next(structure.get_models())}):
        raise ValueError("AntiFold antigen_chain은 구조에 있는 별도 표적 체인이어야 합니다.")
    return chain, positions, order


def _antifold_position_map(
    structure, wt_sequence: str, warnings: list[str], *, nanobody_chain=None, antigen_chain=None
):
    """(chain_id, posins_to_seq, order_to_seq) 반환.

    posins_to_seq 키: (chain, "142A") 형태의 PDB 잔기 식별자.
    """
    from .utils import pdb_io

    model = next(iter(structure.get_models()))
    wt_sequence = seq_utils.clean_sequence(wt_sequence)
    if not wt_sequence:
        warnings.append("AntiFold: WT 서열이 비어 있습니다.")
        return "", {}, {}
    eligible = []
    issues = []
    for chain in model:
        if nanobody_chain is not None and chain.id != str(nanobody_chain):
            continue
        if antigen_chain is not None and chain.id == str(antigen_chain):
            continue
        sequence = pdb_io.chain_sequence(chain)
        if not sequence:
            continue
        # Coordinates may omit WT residues, but cannot introduce substitutions
        # or insertions. Earliest/latest exact subsequence embeddings are equal
        # iff every observed residue has one unique WT position. This avoids
        # scoring-dependent alignment guesses and enumerating repeated motifs.
        earliest, latest = [], []
        cursor = 0
        for amino_acid in sequence:
            cursor = wt_sequence.find(amino_acid, cursor)
            if cursor < 0:
                break
            earliest.append(cursor + 1)
            cursor += 1
        if len(earliest) != len(sequence):
            issues.append(f"체인 {chain.id}: WT와 다른 아미노산 또는 WT에 없는 좌표 잔기")
            continue
        cursor = len(wt_sequence)
        for amino_acid in reversed(sequence):
            cursor = wt_sequence.rfind(amino_acid, 0, cursor)
            latest.append(cursor + 1)
        if earliest != list(reversed(latest)):
            issues.append(f"체인 {chain.id}: 반복 WT 서열의 좌표 잔기 대응이 모호함")
            continue
        mapping = dict(enumerate(earliest))
        coverage = len(mapping) / len(wt_sequence)
        if coverage < .8:
            issues.append(f"체인 {chain.id}: WT 대응 범위 {coverage:.1%} (최소 80%)")
            continue
        eligible.append((coverage, chain, mapping))
    if not eligible:
        detail = "; ".join(issues) or f"선택한 체인 {nanobody_chain!r} 이 없거나 단백질 체인이 아님"
        warnings.append(f"AntiFold: WT와 정확히 대응하는 VHH 체인을 찾지 못했습니다. {detail}")
        return "", {}, {}
    best_coverage = max(item[0] for item in eligible)
    best = [item for item in eligible if item[0] == best_coverage]
    if len(best) != 1:
        warnings.append(
            f"AntiFold: WT 대응 체인이 모호합니다 ({[item[1].id for item in best]}). "
            "mature.antifold.nanobody_chain 을 명시하세요."
        )
        return "", {}, {}
    _, selected_chain, mapping = best[0]
    residues = list(pdb_io.iter_residues(selected_chain))
    posins_to_seq = {}
    for index, position in mapping.items():
        residue = residues[index]
        _, resseq, icode = residue.id
        posins_to_seq[(selected_chain.id, f"{int(resseq)}{str(icode).strip()}")] = position
    return selected_chain.id, posins_to_seq, {}


def _parse_antifold_output(
    out_dir: Path,
    posins_to_seq: dict,
    order_to_seq: dict,
    warnings: list[str],
) -> dict | None:
    csv_candidates = sorted(Path(out_dir).glob("*.csv"))
    if not csv_candidates:
        warnings.append("AntiFold 출력 CSV를 찾지 못했습니다 (로그 확인).")
        return None
    frame = pd.read_csv(csv_candidates[0])
    aa_columns = [aa for aa in AMINO_ACIDS if aa in frame.columns]
    if not aa_columns or "pdb_posins" not in frame.columns:
        warnings.append("AntiFold CSV 스키마가 예상과 다릅니다 (pdb_posins/AA 컬럼 필요).")
        return None
    logprobs: dict[tuple[int, str], float] = {}
    unmatched = 0
    scored_chains = {chain for chain, _ in posins_to_seq}
    for _, row in frame.iterrows():
        posins = str(row["pdb_posins"]).strip()
        chain = str(row.get("pdb_chain", "")).strip()
        # Upstream includes the antigen's rows in the same CSV. They are
        # expected context, not failed WT mappings or mutation scores.
        if chain not in scored_chains:
            continue
        seq_position = posins_to_seq.get((chain, posins))
        if seq_position is None:
            unmatched += 1
            continue
        for aa in aa_columns:
            logprobs[(int(seq_position), aa)] = float(row[aa])
    if unmatched:
        warnings.append(f"AntiFold: {unmatched}개 row가 구조/체인 매핑에 실패해 제외되었습니다(행 순서 대체 금지).")
    return logprobs


def mutation_summary_frame(selected: pd.DataFrame) -> pd.DataFrame:
    columns = ["variant_id", "region", "mutation", "esm_llr", "antifold", "sequence"]
    present = [c for c in columns if c in selected.columns]
    return selected[present].copy()
