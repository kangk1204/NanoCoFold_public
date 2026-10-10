"""Opt-in CPU FP32 adapter for Boltz versions hardcoding bf16-mixed.

Only the child process's Trainer precision changes; installed Boltz is untouched.
Numerical equivalence to native BF16 is not asserted.
"""
from __future__ import annotations

import argparse
import os
import sys


def validate_cpu_args(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--accelerator", action="append")
    parsed, _ = parser.parse_known_args(argv)
    if not argv or argv[0] != "predict" or parsed.accelerator != ["cpu"]:
        raise ValueError("CPU FP32 requires exactly one explicit --accelerator cpu.")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    validate_cpu_args(argv)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    from boltz import main as backend

    native_trainer = backend.Trainer
    def cpu_trainer(*args, **kwargs):
        if kwargs.get("accelerator") != "cpu":
            raise ValueError("CPU FP32 refuses non-CPU Trainer execution.")
        kwargs["precision"] = 32
        return native_trainer(*args, **kwargs)
    backend.Trainer = cpu_trainer
    try:
        print("[NanoCoFold] CPU precision=32; model weights and sampling arguments unchanged.", flush=True)
        backend.cli(args=argv)
    finally:
        backend.Trainer = native_trainer


if __name__ == "__main__":
    main()
