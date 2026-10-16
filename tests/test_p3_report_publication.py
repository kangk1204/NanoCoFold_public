"""An interrupted report refresh must preserve every previously linked snapshot."""
from hashlib import sha256
from html.parser import HTMLParser
import io
import json
import re
from urllib.parse import unquote, urlsplit
import zipfile

import pandas as pd
import pytest

from nanocofold import report
from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run


class ExplorerLink(HTMLParser):
    href = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "a" and attributes.get("id") == "complete-result-explorer":
            self.href = attributes["href"]


def linked_explorer(entry):
    link = ExplorerLink()
    link.feed(entry.read_text())
    assert link.href
    assert not urlsplit(link.href).scheme and not urlsplit(link.href).fragment
    return entry.parent / unquote(link.href), link.href


def payload(path):
    match = re.search(r'<script id="result-data" type="application/json">(.*?)</script>', path.read_text(), re.S)
    assert match
    return json.loads(match.group(1))


def changed_population(run):
    path = run / "03_score/scored_candidates.csv"
    frame = pd.read_csv(path)
    row = frame.iloc[0].copy()
    row["design_id"], row["iptm"] = "new-record", .9
    pd.concat([frame, row.to_frame().T], ignore_index=True).to_csv(path, index=False)


@pytest.mark.parametrize("failure", ["template", "explorer", "main"])
def test_late_publication_failures_preserve_prior_entry_and_explorer(tmp_path, monkeypatch, failure):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    old_explorer, _ = linked_explorer(entry)
    old_entry_bytes, old_explorer_bytes = entry.read_bytes(), old_explorer.read_bytes()
    legacy = run / "report_explorer.html"
    if legacy != old_explorer:
        legacy.write_bytes(b"historical fixed-name explorer must remain available")
    legacy_bytes = legacy.read_bytes()
    changed_population(run)
    source_bytes = (run / "03_score/scored_candidates.csv").read_bytes()
    html_before = set(run.glob("*.html"))
    if failure == "template":
        def fail_render(self, *args, **kwargs):
            raise OSError("injected template failure")
        monkeypatch.setattr(report.Environment, "get_template", lambda *a, **k: type("BrokenTemplate", (), {"render": fail_render})())
    else:
        original = report._atomic_write
        def fail_write(destination, content, **kwargs):
            if (failure == "main") == (destination == entry):
                raise OSError("injected " + failure + " publication failure")
            return original(destination, content, **kwargs)
        monkeypatch.setattr(report, "_atomic_write", fail_write)
    with pytest.raises(OSError, match="injected"):
        report.generate_report(run, cfg, {})
    assert entry.read_bytes() == old_entry_bytes
    assert old_explorer.read_bytes() == old_explorer_bytes
    assert legacy.read_bytes() == legacy_bytes
    assert (run / "03_score/scored_candidates.csv").read_bytes() == source_bytes
    assert len(payload(old_explorer)["candidates"]["rows"]) == 2
    if failure == "template":
        assert set(run.glob("*.html")) == html_before
    assert not list(run.glob(".report-*"))


def test_success_links_immutable_content_and_preserves_all_prior_snapshots(tmp_path, monkeypatch):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    old_explorer, _ = linked_explorer(entry)
    old_bytes = old_explorer.read_bytes()
    legacy = run / "report_explorer.html"
    if legacy != old_explorer:
        legacy.write_bytes(b"legacy snapshot")
    legacy_bytes = legacy.read_bytes()
    changed_population(run)
    report.generate_report(run, cfg, {})
    new_explorer, _ = linked_explorer(entry)
    assert new_explorer != old_explorer
    assert sha256(new_explorer.read_bytes()).hexdigest() in new_explorer.stem
    assert len(payload(new_explorer)["candidates"]["rows"]) == 3
    assert old_explorer.read_bytes() == old_bytes and legacy.read_bytes() == legacy_bytes
    original = report._atomic_write
    def main_only(destination, content, **kwargs):
        assert destination == entry, "Identical immutable explorers must be reused"
        return original(destination, content, **kwargs)
    monkeypatch.setattr(report, "_atomic_write", main_only)
    report.generate_report(run, cfg, {})
    assert linked_explorer(entry)[0] == new_explorer


def test_content_address_collision_never_overwrites_existing_bytes(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    explorer, _ = linked_explorer(entry)
    old_entry = entry.read_bytes()
    explorer.write_bytes(b"unexpected bytes at an immutable snapshot path")
    with pytest.raises(OSError, match="내용|content"):
        report.generate_report(run, cfg, {})
    assert entry.read_bytes() == old_entry
    assert explorer.read_bytes() == b"unexpected bytes at an immutable snapshot path"


def test_relative_unicode_space_link_survives_copy_and_automatic_exports(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    output = run / "검토 복사 #1.html"
    entry = report.generate_report(run, cfg, {}, out_path=output)
    explorer, href = linked_explorer(entry)
    assert "%20" in href and "%23" in href and "/" not in href
    assert explorer.parent == entry.parent
    reader = ResultReader(run)
    assert explorer.name in {item["path"] for item in reader.artifacts()["artifacts"]}
    assert reader.artifact_path(explorer.name).read_bytes() == explorer.read_bytes()
    with zipfile.ZipFile(io.BytesIO(reader.export("zip")[0])) as archive:
        assert archive.read("run/" + explorer.name) == explorer.read_bytes()
        assert archive.read("run/" + entry.name) == entry.read_bytes()
    copied = tmp_path / "복사한 보고서"; copied.mkdir()
    (copied / entry.name).write_bytes(entry.read_bytes())
    (copied / explorer.name).write_bytes(explorer.read_bytes())
    assert linked_explorer(copied / entry.name)[0].read_bytes() == explorer.read_bytes()


def test_none_viewer_preserves_old_explorers_without_publishing_another(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    explorer, _ = linked_explorer(entry)
    before = {path: path.read_bytes() for path in run.glob("*explorer*.html")}
    cfg["report"]["viewer"] = "none"
    report.generate_report(run, cfg, {})
    link = ExplorerLink(); link.feed(entry.read_text())
    assert link.href is None
    assert {path: path.read_bytes() for path in run.glob("*explorer*.html")} == before
    assert explorer.read_bytes()


def test_cloud_bundle_records_equivalent_view_omissions_and_preserves_originals(tmp_path):
    from nanocofold.platform.cloud_results import build_bundle

    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {})
    explorer, href = linked_explorer(entry)
    before = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    destination = tmp_path / "cloud-bundle"
    metadata = build_bundle(run, destination)
    assert "report_offline.html" in metadata["exports"]
    assert explorer.name not in metadata["exports"]
    with zipfile.ZipFile(destination / "results.zip") as archive:
        receipt = json.loads(archive.read("report_packaging_manifest.json"))
        omitted = {row["path"]: row for row in receipt["derived_reports_omitted"]}
        for path in (entry, explorer):
            member = "original/" + path.name
            assert member not in archive.namelist()
            assert omitted[member]["sha256"] == sha256(path.read_bytes()).hexdigest()
            assert omitted[member]["separate_download"] == "report_delivery.zip"
        assert archive.read("original/03_score/scored_candidates.csv") == before[run / "03_score/scored_candidates.csv"]
    assert all(path.read_bytes() == content for path, content in before.items())


def test_long_entry_filename_keeps_explorer_within_filename_limit(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    entry = report.generate_report(run, cfg, {}, out_path=run / ("검토" * 35 + ".html"))
    explorer, _ = linked_explorer(entry)
    assert len(explorer.name.encode("utf-8")) <= 255
    assert sha256(explorer.read_bytes()).hexdigest() in explorer.stem
