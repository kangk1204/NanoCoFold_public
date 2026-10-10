"""Convert frozen monomer A3Ms into Chai's explicit unpaired aligned parquet.

Run inside the installed Chai environment. It performs no search or inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from chai_lab.data.parsing.msas.aligned_pqt import expected_basename, parse_aligned_pqt_to_msa_context


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest)
    source = json.loads(manifest_path.read_text())
    outputs = {}
    for chain, entry in source["chains"].items():
        a3m = manifest_path.parent / entry["a3m"]
        if digest(a3m) != entry["sha256"]:
            raise ValueError("Frozen monomer A3M changed before Chai conversion")
        records, header, chunks = [], None, []
        for line in a3m.read_text().splitlines():
            if line.startswith(">"):
                if header is not None:
                    records.append((header, "".join(chunks)))
                header, chunks = line[1:], []
            elif line and not line.startswith("#"):
                chunks.append(line)
        if header is not None:
            records.append((header, "".join(chunks)))
        if len(records) < 2 or records[0][1] != entry["sequence"]:
            raise ValueError("Chai MSA requires the exact query and nonquery homologs")
        frame = pd.DataFrame([{"sequence": sequence,
                               "source_database": "query" if i == 0 else "uniref90",
                               "pairing_key": "", "comment": header}
                              for i, (header, sequence) in enumerate(records)])
        destination = manifest_path.parent / expected_basename(entry["sequence"])
        temporary = destination.with_suffix(destination.suffix + ".partial")
        frame.to_parquet(temporary, index=False)
        parsed = parse_aligned_pqt_to_msa_context(temporary)
        if tuple(parsed.tokens.shape) != (len(records), len(entry["sequence"])):
            raise ValueError("Chai parsed MSA depth/width differs from frozen monomer")
        temporary.replace(destination)
        outputs[chain] = {"file": destination.name, "sha256": digest(destination),
                          "sequence_sha256": hashlib.sha256(entry["sequence"].encode()).hexdigest(),
                          "a3m_sha256": entry["sha256"], "rows": len(records), "parsed_rows": int(parsed.tokens.shape[0])}
    receipt = {"schema_version": 1, "inputs_fingerprint": source["inputs_fingerprint"],
               "pairing": "unpaired", "public_api_requests": 0, "chains": outputs,
               "source_encoding": "Merged ColabFold homologs use Chai's uniref90 compatibility feature bucket; original database labels are not recoverable and no UniRef90 search is claimed."}
    (manifest_path.parent / "conversion_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
