"""Real ID/MSA/adapter protocol with synthetic replies; no network or inference."""
import ast
import io
import json
import re
import sys
import tarfile
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from nanocofold import cascade, colabfold, colabfold_msa as msa, colabfold_msa_acquire as acquisition
from nanocofold import engine_comparison as comparison, handoff, mock, provenance
from nanocofold.config import load_config
from nanocofold.platform.inputs import parse_fasta
from nanocofold.utils import seq

IDS = ["normal", "나노바디2", "VHH(A3)", "it's", "Nb;5"]
TARGET, BINDER = "ACDEFG", "LMNPQR"


def configuration(tmp_path, source="cache"):
    cfg = load_config()
    cfg["run"].update(mode="screen", mock=False)
    cfg["screen"].update(allow_public_msa_server=True)
    cfg["screen"]["restraint"]["enabled"] = False
    cfg["screen"]["colabfold"].update(msa_mode="precomputed", msa_source=source, msa_cache_dir=str(tmp_path / "cache"),
        msa_max_sequences=16, batch_size=32, num_models=1, num_seeds=1)
    return cfg


def cached(cfg, tmp_path):
    for i, sequence in enumerate((TARGET, BINDER)):
        path = tmp_path / f"source_{i}.a3m"
        path.write_text(f">query\n{sequence}\n>homolog\nY{sequence[1:]}\n")
        msa.promote_monomer(cfg["screen"]["colabfold"]["msa_cache_dir"], sequence, path)


def frame(ids=IDS):
    return pd.DataFrame([{"design_id": identifier, "sequence": BINDER} for identifier in ids])


def prepare(kind, cfg, root, candidates):
    state = SimpleNamespace(begin_phase=lambda *args: None, set_phase=lambda *args: None)
    fn = comparison.prepare_msa if kind == "comparison" else cascade.prepare_msa
    result = fn(cfg, root, state, {"sequence": TARGET}, candidates)
    return result[0], result[1]


@pytest.mark.parametrize("kind", ["comparison", "cascade"])
def test_actual_cached_prepare_preserves_original_id_and_exact_unpaired_query(tmp_path, kind):
    cfg = configuration(tmp_path)
    cached(cfg, tmp_path)
    applied, candidates = prepare(kind, cfg, tmp_path / "run", frame())
    assert candidates.design_id.tolist() == IDS
    collection = msa.from_config(applied)
    assert list(collection.manifest["parents"]) == sorted(IDS)  # JSON order is canonical, IDs are intact.
    aliases = collection.manifest["id_aliases"]
    assert aliases == seq.candidate_id_aliases(IDS, preserve_ascii=False)
    assert len(set(aliases.values())) == len(IDS)
    for row in candidates.to_dict("records"):
        query = collection.for_query(row["design_id"], row, {"A": TARGET, "B": BINDER}, "B", applied)
        assert query.identity["parent_id"] == row["design_id"]
        assert query.identity["parent_alias"] == aliases[row["design_id"]]
        path = query.write(tmp_path / (aliases[row["design_id"]] + ".a3m"))
        verified = msa.read_multimer(path, {"A": TARGET, "B": BINDER})
        assert verified.sequences == (TARGET, BINDER)
    before = {str(p): p.read_bytes() for p in (tmp_path / "run").rglob("*") if p.is_file()}
    again, unchanged = prepare(kind, cfg, tmp_path / "run", frame())
    assert again["screen"]["colabfold"]["msa_manifest_sha256"] == applied["screen"]["colabfold"]["msa_manifest_sha256"]
    assert unchanged.design_id.tolist() == IDS
    assert all(Path(p).read_bytes() == raw for p, raw in before.items())


class ProtocolTransport:
    """Fixture service implements actual numeric queries/ticket/archive protocol."""
    def __init__(self, expected_alias_path):
        self.calls, self.sequences, self.expected_alias_path = [], [], expected_alias_path

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "POST":
            # The ID mapping must be validated/published before external I/O.
            aliases = json.loads(self.expected_alias_path.read_text())["original_to_alias"]
            assert set(aliases) == set(IDS)
            assert all(re.fullmatch(r"[A-Za-z0-9_.-]+", alias) for alias in aliases.values())
            query = kwargs["data"]["q"]
            records = query.strip().splitlines()
            assert all(re.fullmatch(r">\d+", records[i]) for i in range(0, len(records), 2))
            assert not any(identifier in query for identifier in IDS)
            self.sequences = records[1::2]
            return acquisition.Response(200, {}, b'{"status":"COMPLETE","id":"fixture-ticket"}')
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as package:
            for filename in acquisition.ARCHIVE_NAMES:
                text = "\x00".join(f">{101+i}\n{sequence}\n>homolog\nY{sequence[1:]}\n"
                    for i, sequence in enumerate(self.sequences))
                info = tarfile.TarInfo(filename)
                info.size = len(text.encode())
                package.addfile(info, io.BytesIO(text.encode()))
        return acquisition.Response(200, {}, payload.getvalue())


@pytest.mark.parametrize("kind", ["comparison", "cascade"])
def test_actual_authorized_public_protocol_uses_numeric_ids_after_local_alias_validation(tmp_path, monkeypatch, kind):
    cfg = configuration(tmp_path, "public_api")
    filename = "comparison_id_aliases.json" if kind == "comparison" else "colabfold_id_aliases.json"
    transport = ProtocolTransport(tmp_path / "run/01_msa" / filename)
    monkeypatch.setattr(acquisition, "OfficialTransport", lambda: transport)
    applied, candidates = prepare(kind, cfg, tmp_path / "run", frame())
    assert transport.calls == [("POST", "/ticket/msa"), ("GET", "/result/download/fixture-ticket")]
    assert set(transport.sequences) == {TARGET, BINDER}
    assert set(msa.from_config(applied).manifest["parents"]) == set(IDS)
    assert candidates.design_id.tolist() == IDS
    # A cache replay repeats no POST, GET, ticket creation, or transfer.
    prepare(kind, cfg, tmp_path / "run", frame())
    assert len(transport.calls) == 2


@pytest.mark.parametrize("kind", ["comparison", "cascade"])
@pytest.mark.parametrize("ids", [["clone", "CLONE"], ["나노바디2", unicodedata.normalize("NFD", "나노바디2")],
    ["Nb1", "Ｎｂ１"], ["Nb\u200b1"], ["Nb\x851"], ["bad/path"], ["one", " one "]])
def test_invalid_or_ambiguous_ids_fail_before_folders_acquisition_or_external_transmission(tmp_path, monkeypatch, kind, ids):
    calls = []
    monkeypatch.setattr(comparison, "_acquire_reviewed_api", lambda *args: calls.append(args))
    monkeypatch.setattr(cascade, "_acquire_public_msas", lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        prepare(kind, configuration(tmp_path, "public_api"), tmp_path / "run", frame(ids))
    assert calls == [] and not (tmp_path / "run").exists()


def test_common_fasta_and_web_parser_preserve_spelling_and_reject_unicode_collisions(tmp_path):
    text = "".join(f">{identifier}\n{BINDER}\n" for identifier in IDS)
    path = tmp_path / "library.fasta"
    path.write_text(text, encoding="utf-8")
    assert list(seq.read_fasta(path)) == IDS
    assert [identifier for identifier, _ in parse_fasta(text)] == IDS
    for bad in ("Nb\u200b1", "Nb\x851"):
        with pytest.raises(ValueError): seq.validate_candidate_id(bad)
    for separator in ("\x85", "\x0b", "\x0c"):
        hidden = ">Nb" + separator + BINDER
        path.write_text(hidden)
        with pytest.raises(ValueError): seq.read_fasta(path)
        # The browser parser calls this same guard before splitting lines.
        with pytest.raises(ValueError): seq.validate_fasta_controls(hidden)
    text = f">나노바디2\n{BINDER}\n>{unicodedata.normalize('NFD', '나노바디2')}\n{BINDER}\n"
    path.write_text(text)
    with pytest.raises(ValueError, match="중복"): seq.read_fasta(path)
    with pytest.raises(ValueError): parse_fasta(text)


def test_tampered_alias_map_and_changed_preupload_ids_are_rejected_without_rewrite(tmp_path):
    cfg = configuration(tmp_path)
    cached(cfg, tmp_path)
    applied, _ = prepare("comparison", cfg, tmp_path / "run", frame())
    manifest_path = Path(applied["screen"]["colabfold"]["msa_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["id_aliases"]["it's"] = manifest["id_aliases"]["normal"]
    manifest_path.write_text(json.dumps(manifest))
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="별칭"): msa.Collection(manifest_path.parent)
    assert manifest_path.read_bytes() == before
    alias_path = tmp_path / "run/01_msa/comparison_id_aliases.json"
    before = alias_path.read_bytes()
    with pytest.raises(ValueError, match="별칭"): prepare("comparison", cfg, tmp_path / "run", frame(["other"]))
    assert alias_path.read_bytes() == before


def test_legacy_ascii_msa_collection_reopens_without_manifest_or_file_rewrite(tmp_path):
    cfg = configuration(tmp_path)
    cached(cfg, tmp_path)
    applied, _ = prepare("comparison", cfg, tmp_path / "run", frame(["normal"]))
    root = Path(applied["screen"]["colabfold"]["msa_dir"])
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.pop("id_aliases")
    entry = manifest["parents"]["normal"]
    old_path = "monomers/parent_" + msa.sequence_hash("normal") + ".a3m"
    (root / old_path).write_bytes((root / entry["a3m"]).read_bytes())
    entry["a3m"] = old_path
    (root / "manifest.json").write_text(json.dumps(manifest))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    prepare("comparison", cfg, tmp_path / "run", frame(["normal"]))
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_real_colabfold_adapter_normalizes_safe_native_job_names_back_to_original_ids(tmp_path, monkeypatch):
    cfg = configuration(tmp_path)
    cached(cfg, tmp_path)
    applied, candidates = prepare("comparison", cfg, tmp_path / "run", frame())
    monkeypatch.setattr(provenance, "runtime_identity", lambda *args, **kwargs: {"synthetic_cpu_contract": True})
    monkeypatch.setattr(colabfold.shell, "env_executable", lambda *args, **kwargs: "fixture_colabfold_batch")
    calls, originals = [], {}
    def fixture_native(env, argv, **kwargs):
        rows = pd.read_csv(argv[1]); raw = Path(argv[2]); raw.mkdir(parents=True)
        calls.append(rows.id.tolist())
        for row in rows.to_dict("records"):
            identifier = row["id"]
            assert re.fullmatch(r"[A-Za-z0-9_.-]+", identifier)
            assert Path(row["a3mpath"]).stem == identifier
            assert msa.read_multimer(row["a3mpath"], {"A": TARGET, "B": BINDER}).sequences == (TARGET, BINDER)
            suffix = "rank_001_alphafold2_multimer_v3_model_1_seed_042"
            path = raw / (identifier + "_unrelaxed_" + suffix + ".pdb")
            mock.write_pdb(mock.build_complex_structure(TARGET, BINDER, [], seed=42), path)
            originals[identifier] = path.read_bytes()
            (raw / (identifier + "_scores_" + suffix + ".json")).write_text(json.dumps({"iptm": .8, "ptm": .9,
                "plddt": [80] * 12, "pae": [[4] * 12 for _ in range(12)]}))
            (raw / (identifier + ".done.txt")).write_text("complete\n")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(colabfold.shell, "run_in_env", fixture_native)
    result = colabfold.predict(applied, tmp_path / "run/02_prediction", {"sequence": TARGET}, candidates)
    assert result.design_id.tolist() == IDS and len(calls) == 1 and "error" not in result
    for row in result.to_dict("records"):
        assert Path(row["pred_dir"]).parent.name == row["design_id"]
        path = Path(row["pred_dir"]) / "colabfold_model_0.pdb"
        assert path.read_bytes() == originals[row["native_job_id"]]
        marker = json.loads((path.parent.parent / "colabfold_complete.json").read_text())
        assert marker["original_id"] == row["design_id"] and marker["native_job_id"] == row["native_job_id"]
    colabfold.predict(applied, tmp_path / "run/02_prediction", {"sequence": TARGET}, candidates)
    assert len(calls) == 1  # Validated cache does not resubmit a native call.
    # Earlier completions can be reused, but never assigned a newly inferred
    # native job name. Retain their bytes and report absent origin metadata.
    first = result.iloc[0]
    marker_path = Path(first.pred_dir).parent / "colabfold_complete.json"
    marker = json.loads(marker_path.read_text())
    marker.pop("native_job_id")
    marker_path.write_text(json.dumps(marker))
    before = marker_path.read_bytes()
    replay = colabfold.predict(applied, tmp_path / "run/02_prediction", {"sequence": TARGET}, candidates)
    assert replay.iloc[0].native_job_id is None or pd.isna(replay.iloc[0].native_job_id)
    assert len(calls) == 1 and marker_path.read_bytes() == before


@pytest.mark.parametrize("identifier", IDS + ['a"b', 'close); harmless'])
def test_pymol_python_block_loads_exact_unicode_quote_semicolon_path(tmp_path, monkeypatch, identifier):
    model = tmp_path / identifier / "model.pdb"
    model.parent.mkdir()
    model.write_bytes(b"HEADER synthetic coordinate\nEND\n")
    pml = tmp_path / "output.pml"
    handoff.write_pymol_session_script([(identifier, str(model), "B")], pml)
    text = pml.read_bytes().decode("utf-8")
    mapping = json.loads(next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("# candidate_id_map:")))
    code = text.split("\npython\n", 1)[1].split("\npython end\n", 1)[0]
    ast.parse(code)  # Literal filename cannot create another Python statement.
    calls = []
    fake = SimpleNamespace(load=lambda path, name: calls.append((path, name, Path(path).read_bytes())))
    monkeypatch.setitem(sys.modules, "pymol", SimpleNamespace(cmd=fake))
    exec(compile(code, "generated-pml-python-block", "exec"), {})
    assert calls == [(str(model), mapping["object"], model.read_bytes())]
    assert mapping["candidate_id"] == identifier and re.fullmatch(r"[A-Za-z0-9_]+", mapping["object"])
