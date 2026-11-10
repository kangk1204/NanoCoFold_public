from pathlib import Path
import json

import pytest

from nanocofold.utils import msa

QUERY = "ACDEFGHIKLMNPQRSTVWY"


def search_config(tmp_path, backend="colabfold_search", *, manifest=True):
    binary = tmp_path / "search-tool"
    binary.write_text("tool-v1")
    db = tmp_path / "db"
    db.mkdir(exist_ok=True)
    cfg = {"backend": backend, "cache_dir": str(tmp_path / "cache"), "db": str(db),
           "colabfold_search": str(binary), "mmseqs_binary": str(binary),
           "threads": 2, "sensitivity": 5.7, "max_seqs": 30}
    if manifest:
        receipt = tmp_path / "database.json"
        receipt.write_text('{"revision":"DB-A"}')
        cfg["database_manifest"] = str(receipt)
    return cfg


def successful_search(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if "--db-load-mode" in argv:
            fasta, output = Path(argv[-3]), Path(argv[-1])
            header, sequence = fasta.read_text().splitlines()[:2]
            (output / (header[1:] + ".a3m")).write_text(header + "\n" + sequence + "\n>hit\n" + sequence + "\n")
        elif argv[1] == "unpackdb":
            output = Path(argv[3]); output.mkdir()
            sequence = (output.parent / "query.fasta").read_text().splitlines()[1]
            (output / "0.a3m").write_text(">query\n" + sequence + "\n>hit\n" + sequence + "\n")
    monkeypatch.setattr(msa.shell, "run", run)
    return calls


@pytest.mark.parametrize("backend", ["mmseqs", "colabfold_search"])
def test_search_uses_native_filename_and_preserves_legacy_cache(tmp_path, monkeypatch, backend):
    cfg = search_config(tmp_path, backend)
    cache = Path(cfg["cache_dir"]); cache.mkdir()
    old = cache / (msa.sequence_hash(QUERY) + ".a3m")
    old.write_text(">query\n" + QUERY + "\n>OLD_DB\n" + QUERY + "\n")
    before = old.read_bytes()
    calls = successful_search(monkeypatch)
    result, warnings = msa.ensure_a3m(QUERY, cfg)
    assert result != "empty" and not warnings and calls
    assert "OLD_DB" not in Path(result).read_text()
    assert old.read_bytes() == before
    assert msa.ensure_a3m(QUERY, {**cfg, "backend":"cache"})[0] == str(old)
    if backend == "colabfold_search":
        assert calls[0][calls[0].index("--threads")+1] == "2"
        assert calls[0][calls[0].index("-s")+1] == "5.7"


@pytest.mark.parametrize("backend", ["mmseqs", "colabfold_search"])
def test_matching_receipt_reuses_and_changed_inputs_create_new_attempt(tmp_path, monkeypatch, backend):
    cfg = search_config(tmp_path, backend)
    calls = successful_search(monkeypatch)
    first = Path(msa.ensure_a3m(QUERY, cfg)[0]); before = first.read_bytes(); n = len(calls)
    assert msa.ensure_a3m(QUERY, cfg)[0] == str(first) and len(calls) == n
    paths = [first]
    for field, value in [("sensitivity",7.5), ("max_seqs",77), ("db",str(tmp_path / "DB-B"))]:
        cfg[field] = value
        result = Path(msa.ensure_a3m(QUERY,cfg)[0]); paths.append(result)
        assert result != paths[-2] and len(calls) > n
        n = len(calls)
    Path(cfg["database_manifest"]).write_text('{"revision":"DB-C"}')
    paths.append(Path(msa.ensure_a3m(QUERY,cfg)[0]))
    assert paths[-1] != paths[-2] and len(calls) > n
    assert len(set(paths)) == len(paths) and first.read_bytes() == before


def test_search_without_database_manifest_never_assumes_database_identity(tmp_path, monkeypatch):
    cfg = search_config(tmp_path, manifest=False)
    calls = successful_search(monkeypatch)
    a = Path(msa.ensure_a3m(QUERY,cfg)[0]); b = Path(msa.ensure_a3m(QUERY,cfg)[0])
    assert a != b and a.read_bytes() == b.read_bytes() and len(calls) == 2
    assert json.loads((a.parent / "receipt.json").read_text())["reusable"] is False


def test_corrupted_cached_alignment_is_retained_but_not_reused(tmp_path, monkeypatch):
    cfg = search_config(tmp_path); calls = successful_search(monkeypatch)
    first = Path(msa.ensure_a3m(QUERY,cfg)[0]); first.write_text("damaged")
    second = Path(msa.ensure_a3m(QUERY,cfg)[0])
    assert first != second and first.read_text() == "damaged" and len(calls) == 2


def test_different_queries_have_independent_search_workspaces(tmp_path, monkeypatch):
    cfg = search_config(tmp_path); successful_search(monkeypatch)
    a = Path(msa.ensure_a3m(QUERY,cfg)[0]); b = Path(msa.ensure_a3m(QUERY[:-1]+"A",cfg)[0])
    assert a.parent.parent != b.parent.parent
    msa.validate_target_a3m(a, QUERY); msa.validate_target_a3m(b, QUERY[:-1]+"A")


def test_wrong_query_output_never_gets_a_completion_receipt(tmp_path, monkeypatch):
    cfg = search_config(tmp_path)
    def run(argv, **kwargs): (Path(argv[-1]) / "query.a3m").write_text(">query\nAAAA\n")
    monkeypatch.setattr(msa.shell,"run",run)
    with pytest.raises(ValueError, match="first query"):
        msa.ensure_a3m(QUERY,cfg)
    assert not list(Path(cfg["cache_dir"]).rglob("latest.json"))


def test_cancel_propagates_without_publishing_search_success(tmp_path, monkeypatch):
    cfg = search_config(tmp_path)
    def run(*args, **kwargs): raise msa.shell.ExecutionCancelled("cancelled")
    monkeypatch.setattr(msa.shell,"run",run)
    with pytest.raises(msa.shell.ExecutionCancelled): msa.ensure_a3m(QUERY,cfg)
    assert not list(Path(cfg["cache_dir"]).rglob("latest.json"))


def test_changed_database_manifest_during_search_cannot_be_published(tmp_path, monkeypatch):
    cfg = search_config(tmp_path)
    successful_search(monkeypatch)
    original = msa.shell.run
    def run(*args, **kwargs):
        original(*args, **kwargs)
        Path(cfg["database_manifest"]).write_text('{"revision":"changed-mid-search"}')
    monkeypatch.setattr(msa.shell, "run", run)
    with pytest.raises(ValueError, match="MSA search inputs changed"):
        msa.ensure_a3m(QUERY, cfg)
    assert not list(Path(cfg["cache_dir"]).rglob("latest.json"))


def test_simultaneous_same_search_publishes_one_valid_result(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    cfg = search_config(tmp_path)
    calls = successful_search(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: msa.ensure_a3m(QUERY, cfg)[0], range(2)))
    assert results[0] == results[1] and len(calls) == 1
    msa.validate_target_a3m(results[0], QUERY)


def test_search_lock_cannot_follow_symlink(tmp_path, monkeypatch):
    cfg = search_config(tmp_path); successful_search(monkeypatch)
    result = Path(msa.ensure_a3m(QUERY, cfg)[0])
    lock = result.parent.parent / ".lock"
    lock.unlink()
    protected = tmp_path / "protected"; protected.write_text("preserve")
    lock.symlink_to(protected)
    with pytest.raises(OSError): msa.ensure_a3m(QUERY, cfg)
    assert protected.read_text() == "preserve"
