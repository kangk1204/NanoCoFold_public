"""Clock-only refreshes reuse exact published bytes; cleanup remains explicit."""
import json

from nanocofold import report
from nanocofold.platform.retention import RetentionManager
from test_audit21_presentation import saved_run
from test_p3_report_publication import linked_explorer, changed_population
from audit121_fixtures import fixture_run


def test_report_clock_only_refresh_reuses_snapshot_and_preserves_source_state(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    state_path = run / "run_state.json"
    state = json.loads(state_path.read_text())
    state["execution"] = {"state": "running", "command": "report", "started": 1.}
    state_path.write_text(json.dumps(state))
    entry = report.generate_report(run, cfg, {})
    original, _ = linked_explorer(entry)
    original_bytes = original.read_bytes()
    for number in range(2, 5):
        state["previous_execution"] = {"state": "complete", "command": "report", "started": number - 1., "finished": number - .5}
        state["execution"]["started"] = float(number)
        state_path.write_text(json.dumps(state))
        state_bytes = state_path.read_bytes()
        report.generate_report(run, cfg, {})
        assert linked_explorer(entry)[0] == original
        assert original.read_bytes() == original_bytes
        assert state_path.read_bytes() == state_bytes
    assert len(list(run.glob("report_explorer-*.html"))) == 1
    changed_population(run)
    report.generate_report(run, cfg, {})
    assert linked_explorer(entry)[0] != original
    assert original.read_bytes() == original_bytes


def test_result_state_changes_are_not_hidden_by_snapshot_reuse(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    old, _ = linked_explorer(entry)
    state_path = run / "run_state.json"
    state = json.loads(state_path.read_text())
    state["execution"] = {"state": "failed", "error": "recorded failure"}
    state_path.write_text(json.dumps(state))
    report.generate_report(run, cfg, {})
    assert linked_explorer(entry)[0] != old


def test_retention_classifies_generated_snapshots_but_preserves_unknown_html():
    for name in ["report_explorer-" + "a" * 64 + ".html", "검토 복사_explorer-" + "f" * 64 + ".html", "report_explorer.html",
                 "manifest_review_explorer-" + "b" * 64 + ".html", "input_reference_explorer-" + "c" * 64 + ".html"]:
        assert RetentionManager._keep({"path": name}) is False
    for name in ["my_results.html", "report_explorer-not-a-digest.html", "history/report_explorer-" + "a" * 64 + ".html"]:
        assert RetentionManager._keep({"path": name}) is True


def test_explicit_retention_removes_generated_explorers_and_preserves_result_rows(tmp_path):
    workspace, run, _ = fixture_run(tmp_path)
    snapshots = [run / ("report_explorer-" + char * 64 + ".html") for char in "ab"]
    for snapshot in snapshots:
        snapshot.write_text("derived PAE preview")
    manager = RetentionManager(workspace)
    plan = manager.plan(run)
    assert all(snapshot.name in {row["path"] for row in plan["deletable_files"]} for snapshot in snapshots)
    before = (run / "03_score/scored_candidates.csv").read_bytes()
    manager.apply(run, plan["plan_digest"], True)
    assert not any(snapshot.exists() for snapshot in snapshots)
    assert (run / "03_score/scored_candidates.csv").read_bytes() == before


def test_download_captures_one_consistent_entry_during_concurrent_refresh(tmp_path, monkeypatch):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    old_entry = entry.read_bytes()
    old_explorer, _ = linked_explorer(entry)
    changed_population(run)
    report.generate_report(run, cfg, {})
    new_entry = entry.read_bytes()
    entry.write_bytes(old_entry)
    original = report.report_explorer_path
    def concurrent_publish(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        entry.write_bytes(new_entry)
        return result
    monkeypatch.setattr(report, "report_explorer_path", concurrent_publish)
    files = report.report_snapshot_files(entry)
    assert files[entry.name] == old_entry
    assert files[old_explorer.name] == old_explorer.read_bytes()
