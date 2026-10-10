"""Run AntiFold 0.3.1 with explicit local weights and no implicit downloads.

Upstream main ignores --model_path and its loader always checks site-packages/
models/model.pt. This adapter changes only loading, then delegates its CLI to
upstream; no installed package files are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path


def load_local_model(checkpoint_path: Path):
    from antifold import antiscripts
    import torch

    model, _ = antiscripts.antifold.esm.pretrained._load_IF1_local()
    model = antiscripts.load_IF1_checkpoint(model, str(checkpoint_path))
    model.eval()
    return model.to(torch.device('cuda' if torch.cuda.is_available() else 'cpu'))


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--model-path', required=True)
    parser.add_argument('--model-sha256')
    options, upstream = parser.parse_known_args(argv)
    checkpoint = Path(options.model_path).expanduser().resolve()
    if not checkpoint.is_file() or checkpoint.stat().st_size == 0:
        parser.error(f'AntiFold local weights missing: {checkpoint}. Run nanocofold setup --profile mature.')
    if options.model_sha256:
        digest = hashlib.sha256()
        with checkpoint.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != options.model_sha256:
            parser.error(f'AntiFold local weight hash mismatch: {checkpoint}')
    import antifold.main as entrypoint
    import urllib.request

    def refuse_download(*args, **kwargs):
        raise RuntimeError('AntiFold runtime downloads are disabled; install local weights with nanocofold setup.')

    # Explicit local construction avoids upstream's downloader and its ignored
    # CLI model_path parameter. Guard remaining accidental HTTP downloads too.
    saved_load = entrypoint.load_model
    saved_retrieve, saved_open = urllib.request.urlretrieve, urllib.request.urlopen
    saved_argv = sys.argv
    try:
        entrypoint.load_model = lambda *args, **kwargs: load_local_model(checkpoint)
        urllib.request.urlretrieve = refuse_download
        urllib.request.urlopen = refuse_download
        sys.argv = ['antifold.main', *upstream]
        args = entrypoint.cmdline_args()
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.DEBUG if args.verbose >= 2 else logging.INFO,
                            format='[%(asctime)s] %(message)s', stream=sys.stdout)
        entrypoint.check_valid_input(args)
        entrypoint.main(args)
    finally:
        entrypoint.load_model = saved_load
        urllib.request.urlretrieve, urllib.request.urlopen = saved_retrieve, saved_open
        sys.argv = saved_argv


if __name__ == '__main__':
    main()
