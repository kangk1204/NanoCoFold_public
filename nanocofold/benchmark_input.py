"""Audit the public 3K1K GFP fixture without replacing archived inputs."""
from __future__ import annotations

import hashlib
from pathlib import Path

from Bio.Align import PairwiseAligner
from Bio.Data.PDBData import protein_letters_3to1


def audit_gfp_fixture(directory: Path) -> dict:
    directory = Path(directory)
    source = directory / "3K1K.pdb"
    text = source.read_text()
    components = [component for line in text.splitlines()
                  if line.startswith("SEQRES") and line[11:12] == "A"
                  for component in line[19:70].split()]
    modified = {line[12:15].strip(): line[24:27].strip() for line in text.splitlines()
                if line.startswith("MODRES") and line[16:17] == "A"
                and line[12:15].strip() != "GYS"}
    chromophore = sorted({(int(line[43:48]), line[39:42].strip())
                          for line in text.splitlines()
                          if line.startswith("SEQADV") and line[16:17] == "A"
                          and line[12:15].strip() == "GYS"})
    if len(components) != 236 or components.count("GYS") != 1 or chromophore != [(65, "SER"), (66, "TYR"), (67, "GLY")]:
        raise ValueError("3K1K chain A polymer/chromophore source does not match the verified fixture.")
    sequence, expansions = "", []
    for label_position, component in enumerate(components, 1):
        if component == "GYS":
            parent = "".join(protein_letters_3to1[name] for _, name in chromophore)
        else:
            parent = protein_letters_3to1.get(modified.get(component, component))
        if not parent:
            raise ValueError(f"Unresolved polymer component: {component}")
        start = len(sequence) + 1
        sequence += parent
        if component not in protein_letters_3to1:
            expansions.append({"component": component, "label_position": label_position,
                               "precursor_start": start, "precursor_end": len(sequence), "parent": parent})
    legacy = "".join(line.strip() for line in (directory / "target.fasta").read_text().splitlines()
                     if not line.startswith(">"))
    aligner = PairwiseAligner(mode="global", match_score=2, mismatch_score=-10,
                             open_gap_score=-2, extend_gap_score=-.1)
    alignment = aligner.align(sequence, legacy)[0]
    mapping = {}
    for (a, b), (c, d) in zip(alignment.aligned[0], alignment.aligned[1]):
        if b - a != d - c or sequence[a:b] != legacy[c:d]:
            raise ValueError("Legacy GFP differs by substitutions, not only deletions.")
        mapping.update({int(i + 1): int(a + i - c + 1) for i in range(c, d)})
    if len(mapping) != len(legacy):
        raise ValueError("Legacy GFP cannot be completely mapped to the source polymer.")
    present = set(mapping.values())
    omitted = [{"position": i, "aa": aa, "kind": "terminal" if i < min(present) or i > max(present) else "internal"}
               for i, aa in enumerate(sequence, 1) if i not in present]
    return {
        "schema_version": 1, "source": "3K1K chain A SEQRES, MODRES and SEQADV",
        "source_url": "https://www.rcsb.org/structure/3K1K",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sequence": sequence, "sequence_length": len(sequence),
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "legacy_sequence": legacy, "legacy_length": len(legacy),
        "legacy_file_sha256": hashlib.sha256((directory / "target.fasta").read_bytes()).hexdigest(),
        "legacy_to_precursor": mapping, "omitted_residues": omitted,
        "component_expansions": expansions,
        "chemical_representation": "standard amino acid precursor; SYG does not model chemically mature GYS chromophore",
        "interpretation": "Legacy input omissions are confirmed. Their effect on predictions requires paired controls.",
    }
