"""Offline, validated ColabFold A3M inputs with auditable public-template projection.

This module never searches a database or submits a sequence to a service.
MSA transplantation is an explicit approximation, not a private-parent search.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from Bio import Align

from . import provenance as identities
from .config import cfg_get, resolve_path

AMINO = set("ACDEFGHIKLMNPQRSTVWY")
ALIGNED = set("ABCDEFGHIKLMNOPQRSTUVWXYZ-")
SCHEMA_VERSION = 1
MAX_FILE_BYTES = 64 * 1024 * 1024


def sequence_hash(sequence):
    return hashlib.sha256(sequence.encode()).hexdigest()


def _query(sequence):
    if not isinstance(sequence, str) or not sequence or any(letter not in AMINO for letter in sequence):
        raise ValueError("MSA query must be an exact canonical uppercase protein sequence")
    return sequence


def _aligned(sequence):
    if not sequence or any(letter not in ALIGNED and letter not in "abcdefghijklmnopqrstuvwxyz" for letter in sequence):
        raise ValueError("A3M has unsupported symbols (allowed: amino acids, lowercase insertions, '-')")
    return "".join(letter for letter in sequence if not letter.islower())


def _records(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"A3M missing or exceeds {MAX_FILE_BYTES} bytes: {path}")
    text = path.read_text(encoding="utf-8")
    if "\x00" in text:
        raise ValueError("A3M contains NUL bytes")
    meta, rows, header, chunks = None, [], None, []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if rows or header is not None or meta is not None:
                raise ValueError("A3M metadata must occur only at the beginning")
            match = re.fullmatch(r"#([0-9]+(?:,[0-9]+)*)\t([0-9]+(?:,[0-9]+)*)", line)
            if not match:
                raise ValueError("Malformed ColabFold A3M length/cardinality header")
            lengths, counts = ([int(value) for value in group.split(",")] for group in match.groups())
            if len(lengths) != len(counts) or any(value < 1 for value in lengths + counts):
                raise ValueError("Invalid ColabFold A3M chain lengths/cardinalities")
            meta = (lengths, counts)
        elif line.startswith(">"):
            if len(line) == 1 or any(ord(letter) < 32 and letter != "\t" for letter in line):
                raise ValueError("Invalid A3M sequence header")
            if header is not None:
                rows.append((header, "".join(chunks)))
            header, chunks = line, []
        else:
            if header is None or any(letter.isspace() for letter in line):
                raise ValueError("A3M sequence has no header or contains whitespace")
            chunks.append(line)
    if header is not None:
        rows.append((header, "".join(chunks)))
    if not rows or len(rows) > 100000:
        raise ValueError("A3M has no sequences or too many rows")
    return meta, rows


@dataclass(frozen=True)
class Monomer:
    sequence: str
    rows: tuple[tuple[str, str], ...]
    source_sha256: str

    @property
    def homolog_count(self):
        return len(self.rows) - 1

    def text(self):
        return "".join(header + "\n" + row + "\n" for header, row in self.rows)


def read_monomer(path, expected_sequence=None, max_sequences=2048):
    if type(max_sequences) is not int or not 2 <= max_sequences <= 10000:
        raise ValueError("MSA max_sequences must be integer 2..10000")
    meta, raw = _records(path)
    query = _query(raw[0][1])
    if expected_sequence is not None and query != expected_sequence:
        raise ValueError("A3M first query sequence differs from the requested sequence")
    if meta is not None and meta != ([len(query)], [1]):
        raise ValueError("Monomer A3M metadata must contain one chain with cardinality 1")
    seen, rows = {query}, [raw[0]]
    for header, row in raw[1:]:
        aligned = _aligned(row)
        if len(aligned) != len(query):
            raise ValueError("A3M aligned row width differs from query length")
        if set(aligned) == {"-"} or aligned == query or row in seen:
            continue
        seen.add(row)
        if len(rows) < max_sequences:
            rows.append((header, row))
    if len(rows) < 2:
        raise ValueError("MSA requires at least one nonquery, nonduplicate homolog per chain")
    return Monomer(query, tuple(rows), identities.file_digest(path))


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def promote_monomer(cache_dir, sequence, source_a3m, provenance=None):
    alignment = read_monomer(source_a3m, _query(sequence), max_sequences=10000)
    root = Path(cache_dir).resolve()
    directory = root / sequence_hash(sequence)
    if not directory.resolve().is_relative_to(root):
        raise ValueError("MSA cache paths must remain within the cache root")
    text = alignment.text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    if directory.exists():
        existing = cached_monomer(cache_dir, sequence)
        if existing["sha256"] != digest:
            raise ValueError("Existing monomer MSA differs; preserve it and use a new cache")
        return existing
    # Publish the alignment and its receipt as one directory rename. A crash
    # before publication leaves no incomplete authoritative cache entry.
    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=sequence_hash(sequence) + ".", dir=staging_root))
    try:
        (staging / "query.a3m").write_text(text)
        _atomic_json(staging / "receipt.json", {"schema_version": 1, "sequence_sha256": sequence_hash(sequence),
                     "a3m_sha256": digest, "source_a3m_sha256": alignment.source_sha256,
                     "homolog_count": alignment.homolog_count, "source_provenance": provenance or {}})
        for filename in ("query.a3m", "receipt.json"):
            with (staging / filename).open("rb") as stream:
                os.fsync(stream.fileno())
        descriptor = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            staging.rename(directory)
        except OSError:
            # A concurrent publisher may have completed the same entry. Never
            # replace or repair an existing corrupt/different cache silently.
            if not directory.exists():
                raise
            existing = cached_monomer(cache_dir, sequence)
            if existing["sha256"] != digest:
                raise ValueError("Existing monomer MSA differs; preserve it and use a new cache")
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return cached_monomer(cache_dir, sequence)


def cached_monomer(cache_dir, sequence):
    root = Path(cache_dir).resolve()
    directory = root / sequence_hash(_query(sequence))
    path, receipt_path = directory / "query.a3m", directory / "receipt.json"
    if any(not candidate.resolve().is_relative_to(root) for candidate in (directory, path, receipt_path)):
        raise ValueError("MSA cache paths must remain within the cache root")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("Verified monomer MSA receipt missing/corrupt") from exc
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("sequence_sha256") != sequence_hash(sequence)
            or not path.is_file() or receipt.get("a3m_sha256") != identities.file_digest(path)):
        raise ValueError("Verified monomer MSA content/hash mismatch")
    alignment = read_monomer(path, sequence, max_sequences=10000)
    if type(receipt.get("homolog_count")) is not int or receipt["homolog_count"] != alignment.homolog_count:
        raise ValueError("Verified monomer MSA homolog depth differs from receipt")
    return {"sequence": sequence, "a3m": str(path), "sha256": receipt["a3m_sha256"], "receipt": receipt}


def _cdrs(ranges, length, *, required=True):
    if isinstance(ranges, str):
        try:
            ranges = json.loads(ranges)
        except ValueError as exc:
            raise ValueError("Malformed parent CDR JSON") from exc
    if not ranges and not required:
        return {}
    if not isinstance(ranges, dict) or set(ranges) != {"CDR1", "CDR2", "CDR3"}:
        raise ValueError("MSA mutation/projection requires three explicit parent CDR intervals")
    output, previous = {}, 0
    for name in ("CDR1", "CDR2", "CDR3"):
        bounds = ranges[name]
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2 or any(type(value) is not int for value in bounds):
            raise ValueError("CDR interval must be two integers")
        start, end = bounds
        if not previous < start <= end <= length:
            raise ValueError("CDR intervals overlap or exceed parent sequence")
        output[name], previous = [start, end], end
    return output


def _frameworks(sequence, cdrs):
    output, previous = [], 0
    for start, end in cdrs.values():
        output.append(list(range(previous, start - 1)))
        previous = end
    output.append(list(range(previous, len(sequence))))
    return output


def _cells(row):
    cells, prefix = [], ""
    for letter in row:
        if letter.islower():
            if cells:
                cells[-1] += letter
            else:
                prefix += letter
        else:
            cells.append(letter)
    return prefix, cells


def project_parent(template: Monomer, parent_sequence, parent_cdrs, template_cdrs=None):
    """Use exact framework anchors and mask CDR homology, or unique full alignment."""
    parent_sequence = _query(parent_sequence)
    if parent_sequence == template.sequence:
        return template, {"projection_kind": "exact_query", "template_to_parent": list(range(len(parent_sequence)))}
    parent_cdrs = _cdrs(parent_cdrs, len(parent_sequence))
    mapping, kind = None, None
    if template_cdrs:
        template_cdrs = _cdrs(template_cdrs, len(template.sequence))
        source_frameworks = _frameworks(template.sequence, template_cdrs)
        parent_frameworks = _frameworks(parent_sequence, parent_cdrs)
        if all("".join(template.sequence[i] for i in a) == "".join(parent_sequence[i] for i in b)
               for a, b in zip(source_frameworks, parent_frameworks)):
            mapping = {parent: source for a, b in zip(source_frameworks, parent_frameworks) for source, parent in zip(a, b)}
            kind = "framework_anchored_cdr_masked"
    if mapping is None:
        if max(len(template.sequence), len(parent_sequence)) > 400:
            raise ValueError("Parent MSA projection exceeds bounded nanobody alignment size")
        aligner = Align.PairwiseAligner(mode="global", match_score=2, mismatch_score=-1, open_gap_score=-6, extend_gap_score=-.5)
        alternatives = iter(aligner.align(template.sequence, parent_sequence))
        alignment = next(alternatives)
        if next(alternatives, None) is not None:
            raise ValueError("Ambiguous template→parent residue alignment; provide exact framework CDR anchors")
        mapping = {}
        coordinates = alignment.coordinates
        for i in range(coordinates.shape[1] - 1):
            a0, b0 = coordinates[:, i]
            a1, b1 = coordinates[:, i + 1]
            if a1 > a0 and b1 > b0:
                if a1 - a0 != b1 - b0:
                    raise ValueError("Unexpected alignment block dimensions")
                mapping.update({int(b): int(a) for a, b in zip(range(a0, a1), range(b0, b1))})
        identical = sum(template.sequence[a] == parent_sequence[b] for b, a in mapping.items())
        if identical / max(len(template.sequence), len(parent_sequence)) < .5:
            raise ValueError("Public template is insufficiently related to the parent")
        kind = "unique_global_alignment"
    # A generated CDR is not evidence of homology to the template's loop,
    # even when the full alignment happens to be unambiguous.
    cdr_positions = {i for start, end in parent_cdrs.values() for i in range(start - 1, end)}
    mapping = {parent: source for parent, source in mapping.items() if parent not in cdr_positions}
    rows, seen = [(">parent_query", parent_sequence)], {parent_sequence}
    for header, row in template.rows[1:]:
        prefix, source = _cells(row)
        columns = []
        for index in range(len(parent_sequence)):
            original = mapping.get(index)
            if original is None:
                columns.append("-")
            else:
                cell = source[original]
                if mapping.get(index + 1) != original + 1:
                    cell = cell[0]  # Insertions across masked/deleted boundaries cannot be mapped.
                columns.append(cell)
        projected = (prefix if mapping.get(0) == 0 else "") + "".join(columns)
        aligned = _aligned(projected)
        if (projected not in seen and aligned != parent_sequence
                and any(letter in ALIGNED - {"-"} for letter in aligned)):
            rows.append((header, projected))
            seen.add(projected)
    if len(rows) < 2:
        raise ValueError("Projected parent MSA has no remaining framework homologs")
    provenance = {"projection_kind": kind, "parent_to_template": [mapping.get(i) for i in range(len(parent_sequence))],
                  "unmapped_parent_positions": [i + 1 for i in range(len(parent_sequence)) if i not in mapping],
                  "template_sequence_sha256": sequence_hash(template.sequence), "source_a3m_sha256": template.source_sha256,
                  "private_query_sent_to_service": False}
    return Monomer(parent_sequence, tuple(rows), template.source_sha256), provenance


def prepare_manifest(destination, *, static_chains, parents, public_templates, max_sequences=2048):
    """Publish an immutable run-local MSA collection; repeat only identical inputs."""
    destination = Path(destination)
    if not isinstance(static_chains, dict) or not static_chains or "A" not in static_chains:
        raise ValueError("MSA static_chains must contain primary target A")
    templates = []
    inputs = {"static_chains": {}, "parents": [], "public_templates": [], "max_sequences": max_sequences}
    for entry in public_templates:
        alignment = read_monomer(entry["a3m"], entry.get("sequence"), max_sequences)
        if entry.get("sha256") and entry["sha256"] != alignment.source_sha256:
            raise ValueError("Public template A3M hash differs from trusted cache")
        ranges = _cdrs(entry.get("cdr_ranges"), len(alignment.sequence), required=False)
        templates.append((entry, alignment, ranges))
        inputs["public_templates"].append({"id": entry["id"], "sequence": alignment.sequence,
                                           "sha256": alignment.source_sha256, "cdr_ranges": ranges})
    records, files = {"static_chains": {}, "parents": {}, "queries": {}}, {}
    def retain(name, alignment, metadata):
        text = alignment.text()
        relative = "monomers/" + name + ".a3m"
        files[relative] = text
        return {"sequence": alignment.sequence, "a3m": relative, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "homolog_count": alignment.homolog_count, **metadata}
    for chain, entry in static_chains.items():
        if not isinstance(chain, str) or not re.fullmatch("[A-Z]", chain):
            raise ValueError("MSA static chain ID must be a canonical A..Z letter")
        alignment = read_monomer(entry["a3m"], entry["sequence"], max_sequences)
        if entry.get("sha256") and entry["sha256"] != alignment.source_sha256:
            raise ValueError("Static chain A3M hash differs from trusted cache")
        inputs["static_chains"][chain] = {"sequence": alignment.sequence, "sha256": alignment.source_sha256}
        records["static_chains"][chain] = retain("static_" + chain, alignment, {"source_a3m_sha256": alignment.source_sha256})
    for parent in parents.to_dict("records"):
        identifier, sequence = str(parent["design_id"]), _query(parent["sequence"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier) or identifier in records["parents"]:
            raise ValueError("Invalid/duplicate parent ID in MSA collection")
        ranges = _cdrs(parent.get("cdr_ranges"), len(sequence), required=False)
        inputs["parents"].append({"design_id": identifier, "sequence": sequence, "cdr_ranges": ranges})
        choices = sorted(templates, key=lambda value: (value[1].sequence != sequence, str(value[0]["id"])))
        problems, selected, valid_choices = [], None, []
        for entry, template, template_ranges in choices:
            try:
                projected, projection = project_parent(template, sequence, ranges, template_ranges)
                priority = {"exact_query": 0, "framework_anchored_cdr_masked": 1,
                            "unique_global_alignment": 2}[projection["projection_kind"]]
                mapping = projection.get("parent_to_template", [])
                matching = sum(sequence[i] == template.sequence[source]
                               for i, source in enumerate(mapping) if source is not None)
                valid_choices.append(((priority, -matching, str(entry["id"])), entry, projected, projection))
            except ValueError as exc:
                problems.append(f"{entry['id']}: {exc}")
        if not valid_choices:
            raise ValueError(f"No valid cached MSA/template for parent {identifier}: " + "; ".join(problems))
        _, entry, projected, projection = min(valid_choices, key=lambda value: value[0])
        selected = retain("parent_" + sequence_hash(identifier), projected,
                          {"cdr_ranges": ranges, "public_template_id": entry["id"], "projection": projection})
        records["parents"][identifier] = selected
    if not records["parents"]:
        raise ValueError("MSA collection requires at least one binder parent")
    fingerprint = identities.fingerprint(inputs)
    manifest = {"schema_version": 1, "inputs_fingerprint": fingerprint, "max_sequences": max_sequences,
                "pairing": "unpaired", **records}
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing != manifest:
            raise ValueError("MSA collection inputs changed; preserve it and use a new directory")
        Collection(destination, max_sequences=max_sequences)
        return manifest_path
    destination.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        path = destination / relative
        if path.exists() and path.read_text() != text:
            raise ValueError("Uncommitted MSA file differs; preserve it and use a new directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(".partial")
            temporary.write_text(text)
            temporary.replace(path)
    _atomic_json(manifest_path, manifest)
    return manifest_path


def _unique_chains(chains):
    sequences, cardinality = [], []
    for sequence in chains.values():
        _query(sequence)
        if sequence not in sequences:
            sequences.append(sequence)
            cardinality.append(0)
        cardinality[sequences.index(sequence)] += 1
    expanded = [sequence for sequence, count in zip(sequences, cardinality) for _ in range(count)]
    if expanded != list(chains.values()):
        raise ValueError("Noncontiguous duplicate chains would be reordered by ColabFold; revise chain order")
    return sequences, cardinality


def read_multimer(path, chains, *, expected_sha256=None, max_sequences=2048):
    """Validate an explicitly unpaired ColabFold multimer A3M against all chains.

    The first full query and each unpaired block's first query must match. Lower
    case insertions do not consume aligned columns. Paired homologs require a
    different policy and are deliberately rejected by this unpaired interface.
    """
    sequences, cardinality = _unique_chains(chains)
    if len(chains) > 25 or sum(map(len, chains.values())) > 2000:
        raise ValueError("ColabFold MSA complex exceeds 25 chains / 2000 residues")
    digest = identities.file_digest(path)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError("Precomputed multimer A3M content/hash mismatch")
    meta, rows = _records(path)
    lengths = [len(sequence) for sequence in sequences]
    if meta != (lengths, cardinality):
        raise ValueError("Multimer A3M chain lengths/cardinalities/order differ from input")
    if rows[0][1] != "".join(sequences):
        raise ValueError("Multimer A3M first query differs from actual chains")
    blocks = [[] for _ in sequences]
    for header, row in rows[1:]:
        aligned = _aligned(row)
        if len(aligned) != sum(lengths):
            raise ValueError("Multimer A3M aligned row width differs from chain lengths")
        prefix, cells = _cells(row)
        parts, start = [], 0
        for width in lengths:
            parts.append("".join(cells[start:start + width]))
            start += width
        occupied = [i for i, part in enumerate(parts) if any(letter != "-" for letter in _aligned(part))]
        if not occupied:
            continue
        if len(occupied) != 1:
            raise ValueError("Unpaired multimer MSA contains a paired homolog row")
        index = occupied[0]
        part = (prefix if index == 0 else "") + parts[index]
        if not blocks[index] and part != sequences[index]:
            raise ValueError("Unpaired MSA block starts with a stale/nonquery sequence")
        blocks[index].append((header, part))
    alignments = []
    if type(max_sequences) is not int or not 2 <= max_sequences <= 10000:
        raise ValueError("MSA max_sequences must be integer 2..10000")
    for sequence, block in zip(sequences, blocks):
        seen, retained = {sequence}, [(">query", sequence)]
        for header, row in block[1:]:
            if _aligned(row) == sequence or row in seen:
                continue
            seen.add(row)
            if len(retained) < max_sequences:
                retained.append((header, row))
        if not block or len(retained) < 2:
            raise ValueError("MSA requires at least one nonquery, nonduplicate homolog per chain")
        alignments.append(Monomer(sequence, tuple(retained), digest))
    return _query_descriptor(sequences, cardinality, alignments,
                             {"schema_version": 1, "mode": "precomputed", "input_a3m_sha256": digest,
                              "chain_lengths": lengths, "chain_cardinality": cardinality,
                              "pairing": "unpaired", "homolog_counts": [a.homolog_count for a in alignments]})


def _multimer_lines(sequences, cardinality, alignments):
    yield "#" + ",".join(str(len(sequence)) for sequence in sequences) + "\t" + ",".join(map(str, cardinality)) + "\n"
    yield ">query\n" + "".join(sequences) + "\n"
    for index, (sequence, alignment) in enumerate(zip(sequences, alignments)):
        before = "-" * sum(len(query) for query in sequences[:index])
        after = "-" * sum(len(query) for query in sequences[index + 1:])
        for row_index, (header, row) in enumerate(alignment.rows):
            # Every unpaired block must start with its updated actual query.
            yield f">chain_{index}_{row_index} {header[1:]}\n"
            yield before + (sequence if row_index == 0 else row) + after + "\n"


@dataclass(frozen=True)
class QueryMSA:
    sequences: tuple[str, ...]
    cardinality: tuple[int, ...]
    alignments: tuple[Monomer, ...]
    identity: dict

    def write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.writelines(_multimer_lines(self.sequences, self.cardinality, self.alignments))
        if identities.file_digest(temporary) != self.identity["query_a3m_sha256"]:
            raise ValueError("Materialized query MSA content differs from frozen identity")
        temporary.replace(path)
        return path


def _query_descriptor(sequences, cardinality, alignments, identity):
    digest = hashlib.sha256()
    for line in _multimer_lines(sequences, cardinality, alignments):
        digest.update(line.encode())
    return QueryMSA(tuple(sequences), tuple(cardinality), tuple(alignments),
                    {**identity, "query_a3m_sha256": digest.hexdigest()})


class Collection:
    def __init__(self, directory, *, max_sequences=2048):
        self.directory = Path(directory).resolve()
        self.max_sequences = max_sequences
        path = self.directory / "manifest.json"
        try:
            self.manifest = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("Precomputed MSA manifest missing/corrupt; prepare target and parent alignments first") from exc
        if (not isinstance(self.manifest, dict) or self.manifest.get("schema_version") != 1
                or self.manifest.get("pairing") != "unpaired"):
            raise ValueError("Unsupported precomputed MSA manifest schema/pairing")
        self.manifest_sha256 = identities.file_digest(path)
        self.loaded = {}
        queries = self.manifest.get("queries", {})
        if not isinstance(queries, dict):
            raise ValueError("MSA per-query entries must be an object")
        for group in ("static_chains", "parents"):
            if not isinstance(self.manifest.get(group), dict):
                raise ValueError("MSA manifest chain/parent entries must be objects")
            for entry in self.manifest[group].values():
                self._load(entry)

    def _path(self, entry):
        if not isinstance(entry, dict) or not isinstance(entry.get("a3m"), str):
            raise ValueError("MSA manifest entry is malformed")
        path = (self.directory / entry["a3m"]).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("MSA manifest paths must remain within the collection")
        return path

    def _load(self, entry):
        path = self._path(entry)
        if not isinstance(entry.get("sha256"), str):
            raise ValueError("MSA manifest entry lacks a trusted hash")
        key = str(path), entry["sha256"]
        if key not in self.loaded:
            if not path.is_file() or identities.file_digest(path) != entry["sha256"]:
                raise ValueError("Precomputed MSA source content/hash mismatch")
            self.loaded[key] = read_monomer(path, _query(entry.get("sequence")), self.max_sequences)
        return self.loaded[key]

    def for_query(self, identifier, row, chains, binder_chain, cfg):
        sequences, cardinality = _unique_chains(chains)
        direct = self.manifest.get("queries", {}).get(identifier)
        if direct is not None:
            if not isinstance(direct, dict) or not isinstance(direct.get("sha256"), str):
                raise ValueError("Per-query MSA entry requires a trusted content hash")
            if direct.get("chains") != chains:
                raise ValueError("Per-query MSA manifest chain sequences/order differ from input")
            query = read_multimer(self._path(direct), chains, expected_sha256=direct.get("sha256"),
                                  max_sequences=self.max_sequences)
            return QueryMSA(query.sequences, query.cardinality, query.alignments,
                            {**query.identity, "manifest_sha256": self.manifest_sha256})
        parents = self.manifest["parents"]
        key = row.get("parent_id")
        if not isinstance(key, str) or not key:
            key = identifier if identifier in parents else None
        if key is None:
            exact = [name for name, entry in parents.items() if entry["sequence"] == chains[binder_chain]]
            if len(exact) == 1:
                key = exact[0]
            elif cfg_get(cfg, "run.mode") == "mature" and "WT" in parents:
                key = "WT"
        if key not in parents:
            raise ValueError(f"No explicit valid parent MSA for query {identifier}")
        parent = parents[key]
        parent_sequence, mutant = parent["sequence"], chains[binder_chain]
        recorded = row.get("parent_sequence")
        if isinstance(recorded, str) and recorded != parent_sequence:
            raise ValueError("Library parent sequence differs from MSA manifest")
        if len(mutant) != len(parent_sequence):
            raise ValueError("Parent MSA reuse requires unchanged chain length")
        changed = [i for i, (a, b) in enumerate(zip(parent_sequence, mutant), 1) if a != b]
        if changed:
            cdrs = _cdrs(parent.get("cdr_ranges"), len(parent_sequence))
            allowed = {i for start, end in cdrs.values() for i in range(start, end + 1)}
            if len(changed) > 5 or not set(changed).issubset(allowed):
                raise ValueError("MSA reuse permits at most five same-length CDR mutations")
        sources, alignments, by_sequence = [], [], {}
        for chain, sequence in chains.items():
            entry = parent if chain == binder_chain else self.manifest["static_chains"].get(chain)
            if entry is None or (chain != binder_chain and entry.get("sequence") != sequence):
                raise ValueError(f"Static chain {chain} is missing or reordered in the MSA manifest")
            alignment = self._load(entry)
            if sequence != alignment.sequence:
                rows = ((">actual_query", sequence),) + tuple(
                    (header, homolog) for header, homolog in alignment.rows[1:] if _aligned(homolog) != sequence)
                if len(rows) < 2:
                    raise ValueError("Mutant MSA has no nonquery homolog after exact-query removal")
                alignment = Monomer(sequence, rows, alignment.source_sha256)
            if sequence in by_sequence and by_sequence[sequence].source_sha256 != alignment.source_sha256:
                raise ValueError("Identical replicated chains have inconsistent MSAs")
            by_sequence[sequence] = alignment
            sources.append({"chain": chain, "sha256": entry["sha256"], "homolog_count": alignment.homolog_count})
        alignments = tuple(by_sequence[sequence] for sequence in sequences)
        identity = {"schema_version": 1, "mode": "precomputed", "manifest_sha256": self.manifest_sha256,
                    "sources": sources, "parent_id": key, "parent_sequence_sha256": sequence_hash(parent_sequence),
                    "mutation_positions": changed,
                    "chain_lengths": [len(sequence) for sequence in sequences], "chain_cardinality": cardinality,
                    "pairing": "unpaired", "projection": parent.get("projection")}
        return _query_descriptor(sequences, cardinality, alignments, identity)


def from_config(cfg):
    directory = resolve_path(cfg, cfg_get(cfg, "screen.colabfold.msa_dir"))
    if directory is None:
        raise ValueError("screen.colabfold.msa_dir is required for real ColabFold MSA inference")
    collection = Collection(directory, max_sequences=cfg_get(cfg, "screen.colabfold.msa_max_sequences", 2048))
    expected = cfg_get(cfg, "screen.colabfold.msa_manifest_sha256")
    if expected and expected != collection.manifest_sha256:
        raise ValueError("Precomputed MSA manifest hash differs from frozen run settings")
    return collection
