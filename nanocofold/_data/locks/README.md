# Pinned Linux dependency sets

These complete package lists pin engine dependencies rather than resolving
only the top-level engine packages. Package files are pinned by exact
version and SHA256. Supported target: Linux x86_64, glibc 2.34 or newer.

| Environment | Python | Package count | Torch build |
|---|---|---:|---|
| core | 3.12.13 | 24 | CPU application; no torch |
| antibody | 3.11.15 | 42 | 2.14.0, CUDA 13.0 |
| boltz2 | 3.11.15 | 89 | 2.14.0, CUDA 13.0 |
| chai1 | 3.11.15 | 120 | 2.8.0+cu128 |
| esm / AntiFold | 3.11.15 | 58 | 2.11.0+cu128 |
| RFantibody | 3.10.20 | 63, including editable RFantibody | 2.8.0+cu128 |

The installer checks compatibility with the selected GPU and driver.
Installation success does not establish prediction accuracy.
RFantibody source is separately fixed at commit
`8fe311415754e0276d1a39c87c57e69c88927a2d`. Its public source metadata requests
older Torch/CUDA packages; the explicit dependency override uses the
recorded CUDA 12.8 stack. Chai-1's torch<2.7 metadata is also overridden explicitly.
The install does not ask a dependency resolver to infer these overrides.

`*-source.json` records dependency metadata used as input;
`*.in` is the complete exact requirement input; `*.txt` contains allowed package
file hashes. The editable RFantibody source is installed separately after commit
verification. DGL's exact public wheel URL includes its content hash.

Generate a lock from an enumerated input with:

```bash
uv pip compile ENGINE.in --no-deps --generate-hashes --python-version 3.11 \
  --python-platform x86_64-manylinux_2_34 -o ENGINE.txt
```

Use Python 3.10 for RFantibody and 3.12 for core. Torch-family `*-torch.in` files
are compiled separately with the official
`https://download.pytorch.org/whl/cu128` index. `envs/install_locked.sh` installs
PyPI locks and Torch locks in separate `--no-deps --require-hashes` commands.
This index separation matters: Torch also hosts repackaged Triton/Pillow wheels
whose public versions match PyPI while their SHA256 differs.

Model snapshots are separately recorded in `model-assets.json` and the RF weight
manifest. A new workspace may opt in to read-only model source reuse with
`NANOCOFOLD_MODEL_SOURCE_ROOTS=/existing/workspace:/other/workspace`. A model is
linked only after its curated SHA256 matches; missing files download into the
new workspace. Existing mismatching destination files are preserved and rejected.
The installer verifies every extracted Boltz molecule against its hashed tar,
and stores a per-file content/stat index. Runtime fingerprints reuse only
unchanged entries, hash changed/new files, and include the index file's hash.
