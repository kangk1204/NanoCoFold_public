"""Small, self-contained presentation packages; never an inference archive."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path, PurePosixPath


def package_report(files: dict[str, bytes], receipt: dict) -> bytes:
    """Archive an explicit allowlist and a manifest of its exact bytes."""
    for name, content in files.items():
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
                or ":" in name or any(ord(c) < 32 for c in name)
                or str(path) != name or name == "delivery_manifest.json"):
            raise ValueError(f"Invalid report member: {name}")
        if not isinstance(content, bytes):
            raise TypeError(f"Report member must be bytes: {name}")
    from .utils.presentation import portable_metadata
    from . import __version__
    compute_version = receipt.get("summary", {}).get("state", {}).get("application_version")
    manifest = {
        "schema_version": 1, "kind": "presentation_only", "source_readonly": True,
        "rendering_version": __version__, "compute_version": compute_version,
        "compute_version_source": "saved run_state.application_version" if compute_version else "not_recorded",
        "receipt": portable_metadata(receipt),
        "files": [{"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                  for name, content in sorted(files.items())],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo("delivery_manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2),
                         compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return stream.getvalue()


def save_result_report(run_root: Path) -> Path:
    """Publish atomically after calculation ends; original native files stay intact."""
    from .platform.results import ResultReader
    root = Path(run_root).resolve()
    data, _, name = ResultReader(root).export("report_zip")
    destination = root / name
    from .utils.atomic import write_bytes
    write_bytes(destination, data, root=root, prefix=".report-delivery-")
    return destination
