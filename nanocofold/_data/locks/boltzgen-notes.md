# BoltzGen adapter installation contract

The engine uses the official `boltzgen==0.3.2` PyPI wheel, SHA256
`f832063040eb851deacca5641c1e41a5518696110142a937f888bd8db6806d96`.
Its 125 Python/YAML package files match upstream source commit
`31d9d9b9c72245b4ed6fe8742d6fbf4e1a3552a0`. Main commit
`a3149cf18eeb58648d1abbb27539bd73f746cdda` differs in one writer bug fix;
the installed package is the published 0.3.2 version.

The complete Linux x86_64 Python 3.12 dependency resolution is a candidate
environment, with PyTorch `2.8.0+cu128` installed from its official CUDA 12.8
index. It must pass import, CUDA, specification and real GPU execution checks
on the selected host. Resolution and synthetic adapter tests are not model
performance or biological validation.

Run `nanocofold setup --profile boltzgen` to install BoltzGen and the ANARCI
environment. The optional profile downloads five files, approximately 5.92 GiB.
The four checkpoints and `mols.zip` use immutable Hugging Face revisions and
upstream LFS SHA256 identities in `model-assets.json`; the installer verifies
the complete downloaded/reused contents. The molecules loader reads the ZIP
directly. Cache paths are passed explicitly to the CLI, with network disabled
for inference. This avoids unpinned automatic Hugging Face model resolution.

The scaffold is supplied by `design.boltzgen.scaffold` (or `design.framework`),
with an explicit chain when there are several. Its three CDR intervals must be
validated by ANARCI, or supplied as explicit 1-based sequence intervals. The
adapter preserves cysteines and framework sequence, masks CDR structure, and
places the scaffold in a separate structure group from the target. Original
chain/residue indices are recorded separately from the canonical BoltzGen
indices. Hotspot mode `none` omits target binding types, even if the prepared
input contains automatic hotspot hints.

The initial pipeline runs design, inverse folding, folding and analysis.
All generated records and raw files remain available; no affinity prediction
or hard filtering step is run. Initial scores are engine-specific. Each valid
parent must pass the common downstream ANARCI/structure validation before CDR
library expansion. The published CLI has no supported deterministic seed
control; requested seeds are recorded without claiming repeatable backbones.
