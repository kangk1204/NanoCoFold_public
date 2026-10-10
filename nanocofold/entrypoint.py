"""Select the user workspace before importing the analysis modules."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .console_progress import ConsoleProgress


def main(argv=None):
    # Python normally buffers when a user pipes the CLI into tee or a log file.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        from . import __version__
        print(f"NanoCoFold {__version__}")
        return 0
    # Resolve user CLI files in the invoking directory, before selecting the
    # workspace. Relative paths inside YAML retain the documented workspace base.
    for i, item in enumerate(args):
        for flag in ("--manifest", "--config", "--run-dir", "--vhh-fasta", "--wt-fasta"):
            if item == flag and i + 1 < len(args):
                args[i + 1] = str(Path(args[i + 1]).expanduser().resolve())
            elif item.startswith(flag + "="):
                args[i] = flag + "=" + str(Path(item.split("=", 1)[1]).expanduser().resolve())
    if args and args[0] in {"init", "setup", "serve", "batch"}:
        workspace = None
        for i, item in enumerate(args):
            if item == "--workspace" and i + 1 < len(args):
                workspace = Path(args[i + 1]).expanduser().resolve()
                args[i + 1] = str(workspace)
            elif item.startswith("--workspace="):
                workspace = Path(item.split("=", 1)[1]).expanduser().resolve()
                args[i] = "--workspace=" + str(workspace)
        if workspace is not None:
            if args[0] in {"init", "setup", "serve", "batch"}:
                from .install import init_workspace
                init_workspace(workspace)
            if workspace.is_dir():
                os.environ["NANOCOFOLD_ROOT"] = str(workspace)
                os.chdir(workspace)
    with ConsoleProgress("NanoCoFold 모듈 준비"):
        from .cli import main as cli_main
    return cli_main(args)
