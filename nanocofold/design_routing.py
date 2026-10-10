"""Design engine dispatch with separate scores and lossless generation records."""
from __future__ import annotations

import copy
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

from .config import cfg_get
from . import design_rfantibody, design_boltzgen
from .utils import shell


def run_design(cfg: dict, run_dir: Path, target: dict, mock=False, dry_run=False) -> dict:
    selected = cfg_get(cfg, "design.engine", "rfantibody")
    if not isinstance(selected, str) or selected not in {"rfantibody", "boltzgen", "both"}:
        raise ValueError("design.engine must be rfantibody|boltzgen|both")
    engines = ["rfantibody", "boltzgen"] if selected == "both" else [selected]
    phase = Path(run_dir) / "02_design"
    phase.mkdir(parents=True, exist_ok=True)
    candidates, generated, reports = [], [], {}
    for engine in engines:
        engine_cfg = copy.deepcopy(cfg)
        engine_cfg.setdefault("design", {})["engine"] = engine
        # RF's canonical artifacts are nested for both to keep its full records
        # isolated from the combined display file and safe across resumes.
        engine_run = phase / "rfantibody" if selected == "both" and engine == "rfantibody" else Path(run_dir)
        function = design_rfantibody.run_design if engine == "rfantibody" else design_boltzgen.run_design
        began = time.monotonic()
        try:
            result = function(engine_cfg, engine_run, target, mock=mock, dry_run=dry_run)
        except BaseException as exc:
            interrupted = isinstance(exc, (shell.ExecutionCancelled, KeyboardInterrupt, TimeoutError, subprocess.TimeoutExpired))
            reports[engine] = {"engine": engine, "status": "interrupted" if interrupted else "failed", "error_type": type(exc).__name__, "error": str(exc),
                               "adapter_elapsed_seconds": time.monotonic() - began,
                               "timing_scope": "this adapter call until failure"}
            if not dry_run:
                (phase / "engine_info.json").write_text(json.dumps(reports, indent=2, default=str) + "\n")
                if generated:
                    pd.concat(generated, ignore_index=True, sort=False).to_csv(phase / "all_generated_candidates.csv", index=False)
            raise
        from .pose_integration import preserve_generation_structure
        chosen = preserve_generation_structure(result["candidates"])
        all_records = result.get("all_candidates")
        if all_records is None:
            path = Path(result.get("phase_dir", engine_run / "02_design")) / "all_generated_candidates.csv"
            all_records = pd.read_csv(path) if not dry_run and path.exists() else chosen.copy()
        else:
            all_records = all_records.copy()
        all_records = preserve_generation_structure(all_records)
        for frame in (chosen, all_records):
            frame["parent_engine"] = engine
            if "design_id" not in frame:
                if not frame.empty:
                    raise ValueError(f"{engine}: generated records missing design_id")
                frame["design_id"] = pd.Series(dtype=str)
            frame["initial_design_id"] = frame["design_id"].astype(str)
            if selected == "both":
                frame["design_id"] = engine + "__" + frame["design_id"].astype(str)
            if frame["design_id"].duplicated().any():
                raise ValueError(f"{engine}: duplicate generated design IDs")
        candidates.append(chosen)
        generated.append(all_records)
        reports[engine] = {**result["info"], "adapter_elapsed_seconds": time.monotonic() - began,
                           "timing_scope": "this adapter call, including verified cache reuse"}
    frame = pd.concat(candidates, ignore_index=True, sort=False)
    all_frame = pd.concat(generated, ignore_index=True, sort=False)
    if not dry_run:
        all_frame.to_csv(phase / "all_generated_candidates.csv", index=False)
        frame.to_csv(phase / "design_candidates.csv", index=False)
        (phase / "engine_info.json").write_text(json.dumps(reports, indent=2, default=str) + "\n")
    return {"candidates": frame, "all_candidates": all_frame, "phase_dir": phase,
            "info": {"engine": selected, "engines": reports, "num_candidates": len(frame), "num_generated_records": len(all_frame)}}
