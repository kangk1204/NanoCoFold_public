"""Reproduce the pinned Mol* assets without relaxing the app's CSP.

Mol*'s upstream viewer eagerly initializes h264-mp4-encoder, requiring both
dynamic JavaScript and WebAssembly. Disable the unused MP4 extension's eager
initializer and export factory. The input viewer does not enable extensions.
Exact matches and npm integrity make upstream changes fail closed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

VERSION = "5.12.0"
URL = f"https://registry.npmjs.org/molstar/-/molstar-{VERSION}.tgz"
INTEGRITY = "sha512-KBwdn8ie42a6kqgp/zhCjBb8fIJdBcBq/AMc17iWdYLtGF4doxmaMe5Rf0cUUwMj7FiH+/1Hp7F2Px6ywQV/rg=="
PATCHES = (
    ('let i=r.n(n)()(),a=new Promise(A=>{i.then(()=>{A()})}),s=()=>o(void 0,void 0,void 0,(function*(){yield a;let A=new i.H264MP4Encoder;return A.FS=i.FS,A}))',
     'let s=()=>Promise.reject(new Error("MP4 export is disabled in the NanoCoFold input viewer"))'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Previously downloaded npm archive (offline rebuild)")
    args = parser.parse_args()
    raw = args.archive.read_bytes() if args.archive else urllib.request.urlopen(URL, timeout=60).read()
    if "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode() != INTEGRITY:
        raise ValueError("Mol* npm archive integrity mismatch")
    root = Path(__file__).resolve().parents[1] / "assets"
    manifest_path = root / "vendor_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for source, name in (("build/viewer/molstar.js", "molstar.js"),
                             ("build/viewer/molstar.css", "molstar.css"),
                             ("LICENSE", "MOLSTAR_LICENSE.txt")):
            content = archive.extractfile("package/" + source).read()
            upstream_hash = hashlib.sha256(content).hexdigest()
            if name == "molstar.js":
                text = content.decode()
                for original, replacement in PATCHES:
                    if text.count(original) != 1:
                        raise ValueError("Mol* CSP patch did not match exactly once")
                    text = text.replace(original, replacement)
                content = text.encode()
            (root / name).write_bytes(content)
            manifest[name] = {"version": VERSION, "source": URL + "#package/" + source,
                              "sha256": hashlib.sha256(content).hexdigest(), "license": "MIT",
                              "npm_integrity": INTEGRITY, "upstream_sha256": upstream_hash}
            if name == "molstar.js":
                manifest[name]["patches"] = ["Disable unused h264 MP4 eager initializer and export factory; preserve CSP without unsafe-eval or wasm-unsafe-eval"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Vendored Mol* {VERSION}; npm integrity and exact CSP patches verified")


if __name__ == "__main__":
    main()
