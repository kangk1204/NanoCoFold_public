#!/usr/bin/env python
"""Chai-1(Apache-2.0) Python API 래퍼: CLI가 저장하지 않는 PAE/PDE를 npz로 함께 저장한다.

실행(chai1 env):
  python chai_fold.py --fasta complex.fasta --out-dir out [--constraint-path restraints.csv ...]
출력: 표준 cif/score npz + sample별 scores_extra_idx_N.npz(pae, pde, plddt)
scores_extra.npz는 이전 소비자와 호환되도록 sample 0을 보존한다.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path

import numpy as np


def configure_determinism(seed: int) -> dict:
    """Configure CUDA before importing Chai or building its input features.

    Unsupported deterministic operations fail explicitly; silently falling
    back to nondeterministic kernels would invalidate same-input comparisons.
    """
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Chai seed must be an integer from 0 through 4294967295")
    existing = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if existing not in (None, ":4096:8", ":16:8"):
        raise ValueError("Chai deterministic execution requires CUBLAS_WORKSPACE_CONFIG=:4096:8 or :16:8")
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch

    if existing is None and torch.cuda.is_initialized():
        raise RuntimeError("Configure Chai determinism before CUDA initialization; start a fresh process")
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    return {"schema_version": 1, "seed": seed, "deterministic_algorithms": True,
            "cudnn_deterministic": True, "cudnn_benchmark": False,
            "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
            "torch_version": str(torch.__version__), "cuda_version": torch.version.cuda,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "scope": "same inputs, software, weights, device and runtime; not cross-platform reproducibility"}


def save_extra_scores(out_dir: Path, pae: np.ndarray, plddt: np.ndarray,
                      pde: np.ndarray | None = None, *, sample_count: int = 1) -> list[Path]:
    """Preserve each sample's own arrays without borrowing sample 0 values."""
    if sample_count < 1:
        raise ValueError("Chai sample_count must be positive")
    arrays = {"pae": (np.asarray(pae), 2), "plddt": (np.asarray(plddt), 1)}
    if pde is not None:
        arrays["pde"] = (np.asarray(pde), 2)
    normalized = {}
    for name, (array, unbatched_dimensions) in arrays.items():
        if array.ndim == unbatched_dimensions and sample_count == 1:
            array = array[None]
        if array.ndim != unbatched_dimensions + 1 or array.shape[0] != sample_count:
            raise ValueError(f"Chai {name} sample dimension differs from requested count {sample_count}: {array.shape}")
        if not np.isfinite(array).all():
            raise ValueError(f"Chai {name} contains non-finite values")
        if name in {"pae", "pde"} and (array.shape[-1] != array.shape[-2] or np.any(array < 0)):
            raise ValueError(f"Chai {name} must contain square nonnegative distance matrices")
        normalized[name] = array
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(sample_count):
        payload = {name: array[index] for name, array in normalized.items()}
        path = out_dir / f"scores_extra_idx_{index}.npz"
        np.savez(path, **payload)
        written.append(path)
        if index == 0:
            np.savez(out_dir / "scores_extra.npz", **payload)
    return written


def save_chain_axis(out_dir: Path, model_index: int, asym_ids: list[int], chain_ids: list[str],
                    *, mapper_sha256: str, chai_version: str) -> Path:
    """Record actual ranking tensor axes and Chai's own CIF chain-name mapping."""
    if (not asym_ids or len(asym_ids) != len(chain_ids) or len(set(asym_ids)) != len(asym_ids)
            or len(set(chain_ids)) != len(chain_ids) or any(type(i) is not int or i <= 0 for i in asym_ids)):
        raise ValueError("Chai chain-axis identity is ambiguous")
    out_dir = Path(out_dir)
    cif = out_dir / f"pred.model_idx_{model_index}.cif"
    value = {"schema_version": 1, "model_index": model_index, "asym_ids": asym_ids, "chain_ids": chain_ids,
             "source": "SampleRanking.asym_ids", "chain_mapper": "chai_lab.data.io.cif_utils.get_chain_letter",
             "chain_mapper_sha256": mapper_sha256, "chai_version": chai_version,
             "pair_axes": ["query_chain", "key_chain"],
             "structure_sha256": hashlib.sha256(cif.read_bytes()).hexdigest()}
    path = out_dir / f"chain_axis_idx_{model_index}.json"
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    return path


def main() -> int:
    import importlib.metadata

    parser = argparse.ArgumentParser()
    parser.add_argument("--fasta", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--constraint-path", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-trunk-recycles", type=int, default=3)
    parser.add_argument("--num-diffn-timesteps", type=int, default=200)
    parser.add_argument("--num-diffn-samples", type=int, default=1)
    parser.add_argument("--device", default=None)
    parser.add_argument("--use-msa-server", action="store_true")
    parser.add_argument("--msa-directory", default=None)
    parser.add_argument("--no-low-memory", action="store_true")
    args = parser.parse_args()

    reproducibility = configure_determinism(args.seed)
    from chai_lab.chai1 import run_inference
    from chai_lab.data.io.cif_utils import get_chain_letter
    from chai_lab.utils.tensor_utils import set_seed

    # Upstream seeds the folding context after constructing features. Seed
    # feature construction as well, using the same upstream seed convention.
    set_seed([args.seed])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise FileExistsError(f"Chai output directory must be empty; preserve existing outputs: {out_dir}")
    reproducibility.update(chai_version=importlib.metadata.version("chai_lab"),
                           adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           fasta_sha256=hashlib.sha256(Path(args.fasta).read_bytes()).hexdigest(),
                           state="prepared")
    receipt = out_dir / "reproducibility.json"
    # Chai requires an empty output directory. Write our receipt after its
    # invocation, including failed invocations, rather than blocking startup.
    try:
        candidates = run_inference(
        fasta_file=Path(args.fasta),
        output_dir=out_dir,
        constraint_path=Path(args.constraint_path) if args.constraint_path else None,
        num_trunk_recycles=args.num_trunk_recycles,
        num_diffn_timesteps=args.num_diffn_timesteps,
        num_diffn_samples=args.num_diffn_samples,
        seed=args.seed,
        device=args.device,
        low_memory=not args.no_low_memory,
        use_msa_server=args.use_msa_server,
        msa_directory=Path(args.msa_directory) if args.msa_directory else None,
        )
    except BaseException as exc:
        reproducibility.update(state="failed", error_type=type(exc).__name__)
        receipt.write_text(json.dumps(reproducibility, indent=2, allow_nan=False) + "\n")
        raise
    reproducibility["state"] = "prediction_complete"
    receipt.write_text(json.dumps(reproducibility, indent=2, allow_nan=False) + "\n")

    pae = candidates.pae.detach().float().cpu().numpy()
    plddt = candidates.plddt.detach().float().cpu().numpy()
    pde = candidates.pde.detach().float().cpu().numpy() if hasattr(candidates, "pde") else None
    paths = save_extra_scores(out_dir, pae, plddt, pde, sample_count=args.num_diffn_samples)
    if len(candidates.ranking_data) != args.num_diffn_samples:
        raise ValueError("Chai ranking sample count differs from requested models")
    mapper_sha256 = hashlib.sha256(Path(inspect.getfile(get_chain_letter)).read_bytes()).hexdigest()
    for index, ranking in enumerate(candidates.ranking_data):
        asym_ids = [int(i) for i in ranking.asym_ids.detach().cpu().tolist()]
        save_chain_axis(out_dir, index, asym_ids, [get_chain_letter(i) for i in asym_ids],
                        mapper_sha256=mapper_sha256, chai_version=importlib.metadata.version("chai_lab"))
    reproducibility.update(state="complete", sample_count=len(paths),
                           structures_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in sorted(out_dir.glob("pred.model_idx_*.cif"))})
    receipt.write_text(json.dumps(reproducibility, indent=2, allow_nan=False) + "\n")
    print(f"[chai_fold] wrote {len(paths)} sample-specific PAE files and legacy sample-0 alias")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
