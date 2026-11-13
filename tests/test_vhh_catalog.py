"""Persist candidate lookup across runs and upgrades without changing scientific inputs."""
import hashlib

from nanocofold.platform.result_catalog import ResultCatalog
from nanocofold.platform.results import ResultReader


def saved(root, name):
    run = root / "runs" / name
    (run / "03_score").mkdir(parents=True)
    (run / "05_final").mkdir()
    (run / "run_config.yaml").write_text("run:\n  mode: screen\n  name: " + name + "\n")
    (run / "run_state.json").write_text('{"execution":{"state":"complete"},"phases":{"final":{}}}')
    (run / "03_score/scored_candidates.csv").write_text(
        "candidate_id,sequence,predictor_engine,final_eligible,selection_rank\n"
        "vhh-1,ACDEFG,boltz2,true,1\nvhh-10,ACDEFG,chai1,true,1\nWT,QWERTY,boltz2,false,1\n")
    (run / "05_final/selected_candidates.csv").write_text("candidate_id,sequence,predictor_engine,selection_rank\nvhh-10,ACDEFG,chai1,1\n")
    return run


def fingerprint(run):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}


def test_catalog_record_search_and_upgrade(tmp_path, monkeypatch):
    from nanocofold.platform import result_catalog
    catalog = ResultCatalog(tmp_path)
    roots = [saved(tmp_path, name) for name in ("first", "second")]
    for run in roots:
        catalog.register(run.name, run)
    receipts = [fingerprint(root) for root in roots]
    assert [r.get("selection_rank") for r in ResultReader(roots[0]).all_rows()] == [None, 1, None]
    with catalog.connect() as db:
        assert db.execute("SELECT count(*) FROM vhh_sequences").fetchone()[0] == 2
        db.execute("DELETE FROM vhh_entries")
        db.execute("UPDATE results SET vhh_index=0")
    assert catalog.list()["sequence_index_pending"] == 2
    catalog = ResultCatalog(tmp_path)
    assert catalog.backfill_vhh() == 2
    assert [fingerprint(root) for root in roots] == receipts
    assert not catalog.backfill_vhh()
    monkeypatch.setattr(result_catalog, "ResultReader", lambda *_: (_ for _ in ()).throw(AssertionError("Search reread a result")))
    first = catalog.list(search="cd e", limit=1)
    second = catalog.list(search="cd e", limit=1, cursor=first["next_cursor"])
    assert {first["items"][0]["id"],second["items"][0]["id"]} == {"first", "second"}
    assert first["items"][0]["matching_candidate_count"] == 2
    assert catalog.list(search="vhh-10")["items"][0]["matching_candidate"] == "vhh-10"
    assert catalog.list(search="QWER")["items"][0]["matching_candidate"] == "WT"
    assert not catalog.list(search="%_")["items"]


def test_replacement_and_unavailable_old_run(tmp_path):
    catalog = ResultCatalog(tmp_path)
    for name in ("one","two"):
        run = saved(tmp_path, name)
        catalog.register(name, run)
    table = run / "03_score/scored_candidates.csv"
    table.write_text(table.read_text().replace("ACDEFG", "KLMNPQ"))
    with catalog.connect() as db:
        db.execute("UPDATE results SET vhh_index=0 WHERE id='two'")
        db.execute("DELETE FROM vhh_entries WHERE result_id='two'")
    assert catalog.backfill_vhh() == 1
    assert catalog.list()["sequence_index_unavailable"] == 1
    assert len(catalog.list(search="ACD")["items"]) == 1
    catalog.register("two",run)
    assert catalog.list(search="KLM")["items"][0]["id"] == "two"
    assert not catalog.list().get("sequence_index_unavailable")
    pick = run / "05_final/selected_candidates.csv"
    for data in ("vhh-10,ACDEFG,chai1,1", "vhh-10,KLMNPQ,boltz2,1", "vhh-10,KLMNPQ,chai1,1\nvhh-10,KLMNPQ,chai1,1"):
        pick.write_text("candidate_id,sequence,predictor_engine,selection_rank\n" + data + "\n")
        assert all(row.get("selection_rank") is None for row in ResultReader(run).all_rows())
