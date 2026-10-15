#!/usr/bin/env python
"""ESM-2(MIT) zero-shot 스코어링 (esm env 전용).

모드 1 (변이): --variants CSV(variant_id, position, wt_aa, mut_aa) --wt-sequence SEQ → esm_llr
모드 2 (서열): --sequences CSV(id, sequence) [--reference-sequence REF] → esm_pll, esm_delta
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Bind only our pure-Python package when an engine executes the installed file.
# The parent can be another Python ABI's site-packages; it must never enter
# sys.path and shadow the engine's own NumPy, pandas or Torch distributions.
if not __package__ and "nanocofold" not in sys.modules:
    package_root = Path(__file__).resolve().parents[1]
    specification = importlib.util.spec_from_file_location(
        "nanocofold", package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    package = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = package
    specification.loader.exec_module(package)
from nanocofold.utils.atomic import write_csv

import pandas as pd
import torch
import esm


def _load(model_name: str, device: str):
    loader = getattr(esm.pretrained, model_name)
    model, alphabet = loader()
    model = model.eval().to(device)
    return model, alphabet


def _log_probs(model, alphabet, sequence: str, device: str):
    batch_converter = alphabet.get_batch_converter()
    _, _, tokens = batch_converter([("seq", sequence)])
    tokens = tokens.to(device)
    with torch.no_grad():
        logits = model(tokens, return_contacts=False)["logits"]
        log_probs = torch.log_softmax(logits, dim=-1)[0].cpu()
    return log_probs


def _mean_pll(log_probs, alphabet, sequence: str) -> float:
    values = []
    for index, aa in enumerate(sequence, start=1):
        if aa in alphabet.tok_to_idx:
            values.append(float(log_probs[index, alphabet.tok_to_idx[aa]]))
    return float(sum(values) / len(values)) if values else float("nan")


def run_variants(args, model, alphabet, device) -> int:
    wt_sequence = args.wt_sequence.strip().upper()
    log_probs = _log_probs(model, alphabet, wt_sequence, device)
    frame = pd.read_csv(args.variants)
    values: list[float] = []
    for _, row in frame.iterrows():
        position = int(row["position"])
        wt_aa = str(row["wt_aa"]).upper()
        mut_aa = str(row["mut_aa"]).upper()
        if position >= log_probs.shape[0] or wt_aa not in alphabet.tok_to_idx or mut_aa not in alphabet.tok_to_idx:
            values.append(float("nan"))
            continue
        values.append(float(log_probs[position, alphabet.tok_to_idx[mut_aa]] - log_probs[position, alphabet.tok_to_idx[wt_aa]]))
    frame["esm_llr"] = values
    write_csv(args.output, frame)
    print(f"[esm] {len(frame)} variants scored ({args.model}, {device})")
    return 0


def run_sequences(args, model, alphabet, device) -> int:
    frame = pd.read_csv(args.sequences)
    reference_pll = None
    if args.reference_sequence:
        reference_log_probs = _log_probs(model, alphabet, args.reference_sequence.strip().upper(), device)
        reference_pll = _mean_pll(reference_log_probs, alphabet, args.reference_sequence.strip().upper())
    plls: list[float] = []
    deltas: list[float] = []
    for _, row in frame.iterrows():
        sequence = str(row["sequence"]).strip().upper()
        log_probs = _log_probs(model, alphabet, sequence, device)
        pll = _mean_pll(log_probs, alphabet, sequence)
        plls.append(pll)
        deltas.append(pll - reference_pll if reference_pll is not None else float("nan"))
    output = frame.copy()
    # masked PLL 이 아니라 unmasked 평균 로그확률이므로 이름을 정확히 표기한다(F14).
    output["esm_mean_logprob"] = plls
    output["esm_delta"] = deltas
    write_csv(args.output, output)
    print(f"[esm] {len(frame)} sequences scored (mean log-prob; {args.model}, {device})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants")
    parser.add_argument("--wt-sequence")
    parser.add_argument("--sequences")
    parser.add_argument("--reference-sequence")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="esm2_t33_650M_UR50D")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    model, alphabet = _load(args.model, device)

    if args.variants and args.wt_sequence:
        return run_variants(args, model, alphabet, device)
    if args.sequences:
        return run_sequences(args, model, alphabet, device)
    parser.error("--variants/--wt-sequence 또는 --sequences 중 하나가 필요합니다.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
