"""Read-only, within-run duration estimates; never mix hardware/engine conditions."""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
import time
from pathlib import Path

import yaml


class RuntimeEstimator:
    def __init__(self):
        self.cache = {}

    def _seconds(self, path):
        try:
            stat = path.stat()
            key = str(path)
            identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if self.cache.get(key, (None,))[0] != identity:
                with path.open("rb") as stream:
                    stream.seek(max(0, stat.st_size - 1024))
                    tail = stream.read().decode("utf-8", errors="replace")
                matches = re.findall(r"# exit=0 elapsed=([\d.]+)s", tail)
                value = float(matches[-1]) if matches else None
                self.cache[key] = (identity, value if value and math.isfinite(value) else None)
            return self.cache[key][1]
        except (OSError, ValueError):
            return None

    def estimate(self, job, *, now=None):
        result = {"scope": "complex_prediction", "remaining_seconds": None, "lower_seconds": None,
                  "upper_seconds": None, "completed": 0, "total": None, "sample_count": 0,
                  "basis": "within_run_successful_tool_logs", "includes_postprocessing": False,
                  "note": "표적 준비·후보 생성·후처리·대기 시간은 포함하지 않습니다."}
        if job.get("state") == "complete":
            return {**result, "remaining_seconds": 0, "lower_seconds": 0, "upper_seconds": 0, "status": "complete"}
        if job.get("state") != "running" or job.get("kind") != "analysis" or job.get("mock"):
            return {**result, "status": "unavailable"}
        root = Path(job["run_dir"])
        try:
            comparison_path = root / "02_comparison/comparison_status.json"
            if comparison_path.is_file():
                return self._comparison(json.loads(comparison_path.read_text()), result, now)
            if job.get("mode") == "complex":
                cfg = yaml.safe_load(Path(job["config_path"]).read_text())
                settings = cfg.get("complex", {})
                total = len(settings.get("seeds", [42,123,2026])) * settings.get("models_per_seed", 1)
                values = json.loads((root/"complex_results.json").read_text()).get("rows", []) if (root/"complex_results.json").exists() else []
                durations = [r["elapsed_seconds"] for r in values if r.get("evaluated") and r.get("elapsed_seconds")]
                result.update(total=total, completed=len(values), sample_count=len(durations), engine=settings.get("engine", "boltz2"))
                if not durations:
                    return {**result, "status": "awaiting_first_sample"}
                rate = statistics.median(durations); remaining = max(0,total-len(values))
                return {**result, "status": "estimated", "remaining_seconds": round(rate*remaining),
                        "lower_seconds": round(rate*.7*remaining), "upper_seconds": round(rate*1.3*remaining),
                        "seconds_per_candidate": round(rate,2), "range_kind": "heuristic_budget_range_not_confidence_interval"}
            plan = None
            plans = [p for p in root.glob("02*/prediction_plan.json") if not p.parent.is_symlink() and not p.is_symlink()]
            if plans:
                source = max(plans, key=lambda p: p.stat().st_mtime_ns)
                plan = json.loads(source.read_text())
                ids, engine = set(plan["candidate_ids"]), plan["engine"]
                progress = json.loads((source.parent / "prediction_progress.json").read_text())
                matches = (progress.get("fingerprint") == plan.get("fingerprint") if plan.get("compact") else progress.get("attempt_id") == plan.get("attempt_id"))
                approved = set(progress["completed"]) if matches else set()
            else:
                cfg = yaml.safe_load(Path(job["config_path"]).read_text())
                source = root / "02_screen/candidates.csv"
                if not source.exists():
                    return {**result, "status": "awaiting_candidate_pool"}
                with source.open(encoding="utf-8-sig", newline="") as stream:
                    ids = {r.get("design_id") or r.get("variant_id") for r in csv.DictReader(stream)}
                ids.discard(None)
                engine = cfg.get("screen", {}).get("engine", "chai1")
                approved = ids
            if engine == "colabfold":
                if plan is None:
                    # The candidate pool can be published before its verified
                    # batch plan. ETA must wait for the attempt identity.
                    return {**result, "status": "awaiting_prediction_plan"}
                if plan.get("compact"):
                    compact = json.loads((source.parent / "compact_progress.json").read_text())
                    batches = compact.get("batches", []) if compact.get("fingerprint") == plan.get("fingerprint") else []
                    rates = [b["wall_seconds"] / b["completed"] for b in batches if b.get("completed", 0) > 0 and b["completed"] == b.get("attempted") and _valid_duration(b.get("wall_seconds"))]
                    result.update(total=len(ids), completed=len(ids & approved), sample_count=len(rates), engine=engine, stage=source.parent.name)
                    if not rates:
                        return {**result, "status": "awaiting_first_batch"}
                    rate = statistics.median(rates[-8:])
                    seconds = rate * max(0, len(ids) - result["completed"])
                    calibration = root / "03_score/cascade_calibration.json"
                    if calibration.is_file():
                        control = json.loads(calibration.read_text())
                        cfg = yaml.safe_load(Path(job["config_path"]).read_text())
                        count = min(cfg.get("screen", {}).get("cascade", {}).get("top_n", 99), len(ids) - int("WT" in ids))
                        if control.get("valid") and not control.get("mock") and _valid_duration(control.get("seconds_per_candidate")):
                            seconds += count * control["seconds_per_candidate"]
                            result.update(includes_second_stage=True, note="ColabFold 완료 배치와 같은 설정의 WT Boltz-2 실측으로 두 단계 잔여 시간을 추정합니다. 결과 저장·전송과 GPU 대기는 제외합니다.")
                    return {**result, "status": "estimated", "seconds_per_candidate": round(rate, 2),
                            "remaining_seconds": round(seconds), "lower_seconds": round(seconds * .7), "upper_seconds": round(seconds * 1.4),
                            "estimated_finish": (time.time() if now is None else now) + seconds,
                            "range_kind": "heuristic_budget_range_not_confidence_interval"}
                completed = 0
                for identifier in ids & approved:
                    marker = source.parent / identifier / "colabfold_complete.json"
                    if marker.is_file() and not marker.is_symlink() and json.loads(marker.read_text()).get("status") == "complete":
                        completed += 1
                durations = []
                for path in (source.parent / "_batches").glob(str(plan["attempt_id"]) + "*/timing.json"):
                    if path.is_symlink():
                        continue
                    batch = json.loads(path.read_text())
                    seconds = batch.get("wall_seconds")
                    batch_ids = batch.get("candidate_ids", [])
                    if batch.get("returncode") == 0 and batch_ids and set(batch_ids) <= approved and isinstance(seconds, (int, float)) and seconds > 0 and math.isfinite(seconds):
                        durations.append(seconds / len(batch_ids))
                result.update(total=len(ids), completed=completed, sample_count=len(durations), engine=engine, stage=source.parent.name,
                              note="현재 ColabFold 단계의 완료 배치 실측입니다. 후속 Boltz-2·후처리·대기 시간은 포함하지 않습니다.")
                if not durations:
                    return {**result, "status": "awaiting_first_batch"}
                rate = statistics.median(durations[-10:])
                seconds = rate * max(0, len(ids) - completed)
                return {**result, "status": "estimated", "seconds_per_candidate": round(rate, 2),
                        "remaining_seconds": round(seconds), "lower_seconds": round(seconds * .5), "upper_seconds": round(seconds * 1.5),
                        "range_kind": "heuristic_batch_budget_range_not_confidence_interval"}
            if engine not in {"chai1", "boltz2"}:
                return {**result, "status": "unavailable"}
            filename = "boltz2.log" if engine == "boltz2" else "chai1.log"
            # One direct child per candidate. Never follow arbitrary paths from CSV.
            durations = []
            completed = 0
            for folder in source.parent.iterdir():
                if folder.name in ids & approved and folder.is_dir() and not folder.is_symlink():
                    marker = folder / ("boltz_complete.json" if engine == "boltz2" else "prediction_complete.json")
                    if marker.is_symlink() or not marker.is_file():
                        continue
                    try:
                        if json.loads(marker.read_text()).get("status") != "complete":
                            continue
                    except (ValueError, AttributeError):
                        continue
                    completed += 1
                    logs = [folder / filename]
                    if engine == "chai1":
                        logs.append(folder / "chai1_retry.log")
                    values = [(p.stat().st_mtime_ns, self._seconds(p)) for p in logs if p.is_file() and not p.is_symlink()]
                    successful = [v for v in values if v[1] is not None]
                    if successful:
                        # A successful retry is the measured sample; failures do
                        # not become zero-duration samples.
                        durations.append(max(successful))
            durations.sort()
            result.update(total=len(ids), completed=completed, sample_count=len(durations), engine=engine, stage=source.parent.name)
            if not durations:
                return {**result, "status": "awaiting_first_sample"}
            recent = [v[1] for v in durations[-10:]]
            remaining = max(0, len(ids) - completed)
            rate = statistics.median(recent)
            spread = max(rate * .30, statistics.pstdev(recent) * 2)
            seconds = rate * remaining
            return {**result, "status": "estimated", "seconds_per_candidate": round(rate, 2),
                    "remaining_seconds": round(seconds), "lower_seconds": round(max(0, rate - spread) * remaining),
                    "upper_seconds": round((rate + spread) * remaining),
                    "estimated_finish": (time.time() if now is None else now) + seconds,
                    "range_kind": "heuristic_budget_range_not_confidence_interval"}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, yaml.YAMLError):
            return {**result, "status": "unavailable"}

    @staticmethod
    def _comparison(snapshot, base, now):
        engines, rows = snapshot["engines"], snapshot["rows"]
        per_engine = snapshot["input_candidates"]
        if (not isinstance(engines, list) or not engines or len(set(engines)) != len(engines)
                or type(per_engine) is not int or per_engine < 0 or not isinstance(rows, list)
                or snapshot["expected_rows"] != per_engine * len(engines)):
            raise ValueError("Invalid comparison progress")
        groups = {engine: [row for row in rows if row["engine"] == engine] for engine in engines}
        if any(len(group) > per_engine for group in groups.values()):
            raise ValueError("Comparison count exceeds submitted pool")
        active = next((engine for engine in engines if len(groups[engine]) < per_engine), None)
        result = {**base, "scope": "engine_comparison", "completed": len(rows),
                  "total": snapshot["expected_rows"], "basis": "within_run_successful_same_engine_observations",
                  "unestimated_engines": [], "note": "각 엔진의 완료 관측만 사용합니다. MSA 검색·후처리·대기 시간은 제외합니다."}
        pending, seconds, samples = [], 0., 0
        for engine in engines:
            group = groups[engine]
            remaining = per_engine - len(group)
            durations = [row["elapsed_seconds"] for row in group if row.get("status") == "complete"
                         and _valid_duration(row.get("elapsed_seconds"))][-10:]
            samples += len(durations)
            if remaining and not durations:
                pending.append(engine)
            elif remaining:
                seconds += statistics.median(durations) * remaining
        result.update(sample_count=samples, engine=active, unestimated_engines=pending)
        if not active:
            return {**result, "status": "complete", "remaining_seconds": 0,
                    "lower_seconds": 0, "upper_seconds": 0}
        if pending:
            # Never extrapolate a ColabFold duration to a Boltz/Chai job.
            group = groups[active]
            durations = [row["elapsed_seconds"] for row in group if row.get("status") == "complete"
                         and _valid_duration(row.get("elapsed_seconds"))][-10:]
            result["current_engine_remaining_seconds"] = (
                round(statistics.median(durations) * (per_engine - len(group))) if durations else None)
            return {**result, "status": "awaiting_engine_samples"}
        return {**result, "status": "estimated", "remaining_seconds": round(seconds),
                "lower_seconds": round(seconds * .7), "upper_seconds": round(seconds * 1.4),
                "estimated_finish": (time.time() if now is None else now) + seconds,
                "range_kind": "heuristic_budget_range_not_confidence_interval"}


def terminal_eta(job):
    """Do not display a stale running estimate after cancellation or completion."""
    if isinstance(job.get("eta"), dict) and job.get("state") != "running":
        value = 0 if job.get("state") == "complete" else None
        job["eta"] = {**job["eta"], "remaining_seconds": value, "lower_seconds": value,
                      "upper_seconds": value, "status": job.get("state")}
    return job


def _valid_duration(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0
