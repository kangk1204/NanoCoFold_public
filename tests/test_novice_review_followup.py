"""Novice review regressions: CPU fixtures only, no external MSA or GPU work."""
import argparse
import ast
import copy
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import pandas as pd
import pytest

from nanocofold import cli, report
from nanocofold.platform.results import ResultReader
from test_audit21_presentation import saved_run
from test_audit126_report_warnings import execute_subset
from test_supplied_html_boot import supplied_server

ROOT = Path(__file__).resolve().parents[1]


def test_init_reports_the_first_copy_and_preserves_existing_files(tmp_path):
    workspace = tmp_path / "new workspace"
    command = [sys.executable, "-c", "from nanocofold.entrypoint import main; raise SystemExit(main())",
               "init", "--workspace", str(workspace)]
    env = dict(os.environ, PYTHONPATH=str(ROOT), CUDA_VISIBLE_DEVICES="")
    first = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=45)
    receipt = json.loads(first.stdout)
    assert receipt["created"] and "config/default.yaml" in receipt["created"]
    assert all((workspace / name).is_file() for name in receipt["created"])
    original = workspace / "config/default.yaml"
    original.write_bytes(b"user settings must remain\n")
    second = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=45)
    assert json.loads(second.stdout)["created"] == []
    assert original.read_bytes() == b"user settings must remain\n"


@pytest.mark.parametrize("mode", ["screen", "mature"])
@pytest.mark.parametrize("sequence,warn", [("ACGT" * 15, True), ("ACGT" * 7, False), ("ACDEFGHIKLMNPQRSTVWY" * 3, False)])
def test_cli_sequence_hint_is_recorded_once_without_changing_input(tmp_path, capsys, mode, sequence, warn):
    fasta = tmp_path / "original.fasta"
    fasta.write_text(">candidate_원본\n" + sequence + "\n")
    cfg = {"run": {"mode": mode}, "target": {}, "screen": {"vhh_fasta": str(fasta)},
           "mature": {"wt_fasta": str(fasta)}}
    args = argparse.Namespace(command=mode, config_warnings=[])
    original = copy.deepcopy(cfg), fasta.read_bytes()
    cli._preflight_input_files(cfg, args)
    cli._preflight_input_files(cfg, args)
    assert len(args.config_warnings) == int(warn)
    output = capsys.readouterr().out
    assert output.count("[경고]") == int(warn)
    if warn:
        assert "candidate_원본" in args.config_warnings[0]
        assert "DNA" in args.config_warnings[0]
    assert cfg == original[0] and fasta.read_bytes() == original[1]


def test_inline_wt_hint_reaches_saved_run_warnings(tmp_path):
    args = argparse.Namespace(command="mature", config_warnings=[])
    cfg = {"run": {"mode": "mature"}, "target": {}, "mature": {"wt_sequence": "ACGT" * 15}}
    cli._preflight_input_files(cfg, args)
    from nanocofold.state import RunState
    state = RunState(tmp_path)
    state.add_warnings(args.config_warnings)
    assert json.loads((tmp_path / "run_state.json").read_text())["warnings"] == args.config_warnings
    assert "mature.wt_sequence" in state.warnings[0]


@pytest.mark.parametrize("extra,expected", [
    ({"n_hotspots_requested": 0, "n_hotspots_contacted": 0}, "not_applicable"),
    ({}, "not_assessed"),
    ({"n_hotspots_requested": 4, "hotspot_mapping_failed": True}, "not_assessed"),
    ({"n_hotspots_requested": 4, "n_hotspots_contacted": 0, "hotspot_contact_frac": 0, "pass_hotspot": False}, None),
])
def test_threshold_display_keeps_recorded_flags_and_distinguishes_no_request(extra, expected):
    row = {"design_id": "original", "hotspot_contact_frac": None, "pass_hotspot": True, **extra}
    row["threshold_checks"] = [{"metric": "hotspot_contact_frac", "value": row["hotspot_contact_frac"],
                                "threshold": .5, "operator": ">=", "passed": row["pass_hotspot"]}]
    original = copy.deepcopy(row)
    checks = report._threshold_check_rows([row], {})
    assert checks[0].get("display_status") == expected
    assert checks[0]["passed"] == original["threshold_checks"][0]["passed"]
    assert checks[0]["threshold"] == .5
    assert row == original


def test_rendered_thresholds_and_zip_instructions_are_bilingual_and_preserve_records(tmp_path):
    run, cfg = saved_run(tmp_path, count=2)
    frame = pd.read_csv(run / "03_score/scored_candidates.csv")
    frame["n_hotspots_requested"] = 0
    frame["n_hotspots_contacted"] = 0
    frame["hotspot_contact_frac"] = float("nan")
    frame["pass_hotspot"] = True
    frame.to_csv(run / "03_score/scored_candidates.csv", index=False)
    original = frame.copy(deep=True)
    document = report.generate_report(run, cfg, {}, scored=frame).read_text()
    table = document.split('id="recorded-threshold-checks"', 1)[1].split("</table>", 1)[0]
    assert table.count("해당 없음") == 4
    assert "Not applicable" in document
    pd.testing.assert_frame_equal(frame, original)
    protected = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with zipfile.ZipFile(io.BytesIO(ResultReader(run).export("report_zip")[0])) as archive:
        instructions = archive.read("README.txt").decode()
        assert "ZIP을 모두 풀고 report.html" in instructions
        assert "Extract the entire ZIP" in instructions
        assert archive.testzip() is None
    assert all(path.read_bytes() == content for path, content in protected.items())


def test_authored_warning_templates_translate_in_the_actual_report_subset():
    messages = []
    for filename in ["config.py", "cli.py", "rank_select.py"]:
        for call in ast.walk(ast.parse((ROOT / "nanocofold" / filename).read_text())):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr not in {"warn", "add_warnings"} or not call.args:
                continue
            def sample(node):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    return node.value
                if isinstance(node, ast.JoinedStr):
                    return "".join(sample(v.value) if isinstance(v, ast.FormattedValue) else sample(v) for v in node.values)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    return sample(node.left) + sample(node.right)
                return "sample"
            arguments = call.args[0].elts if isinstance(call.args[0], ast.List) else [call.args[0]]
            for argument in arguments:
                message = sample(argument)
                if re.search("[가-힣]", message):
                    messages.append(message)
    assert len(messages) >= 26
    from html import escape
    runtime = report._report_i18n_runtime("".join('<li data-report-i18n>' + escape(s) + '</li>' for s in messages))
    translations = execute_subset(runtime, messages)
    assert translations["ko"] == messages
    assert all(not re.search("[가-힣]", value) for value in translations["en"])


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium UI gate")
@pytest.mark.parametrize("locale,saved,query,expected", [
    ("ko-KR", None, "", "ko"), ("en-US", None, "", "en"), ("fr-FR", None, "", "en"),
    ("ko-KR", "en", "", "en"), ("en-US", "ko", "", "ko"), ("ko-KR", "ko", "?lang=en", "en"),
])
def test_browser_language_is_only_the_initial_fallback(supplied_server, locale, saved, query, expected):
    from playwright.sync_api import sync_playwright, expect
    _, url = supplied_server
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        context = browser.new_context(locale=locale)
        if saved:
            context.add_init_script("localStorage.setItem('nanocofold.language', " + json.dumps(saved) + ")")
        page = context.new_page()
        page.goto(url + query)
        expect(page.locator("body")).to_have_attribute("data-startup", "ready")
        expect(page.locator("html")).to_have_attribute("lang", expected)
        expect(page.locator("#language-select")).to_have_value(expected)
        context.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Real Chromium UI gate")
@pytest.mark.parametrize("language", ["ko", "en"])
def test_mode_specific_instructions_and_step_boundaries(supplied_server, language):
    from playwright.sync_api import sync_playwright, expect
    _, url = supplied_server
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        page = browser.new_page()
        for i, mode in enumerate(["complex", "design", "screen", "mature"]):
            page.goto(url + "?lang=" + language)
            expect(page.locator("body")).to_have_attribute("data-startup", "ready")
            page.locator(".novice-purpose-choice").nth(i).click()
            text = page.locator("#view-input > p.next-step").inner_text()
            assert ("체인" in text) == (mode == "complex") if language == "ko" else ("chain" in text) == (mode == "complex")
            if language == "en":
                assert not re.search("[가-힣]", text)
        page.locator("#load-tutorial").click()
        expect(page.locator("#wt-fasta")).to_have_value(re.compile(r">.+"))
        page.locator("#novice-next").click()
        page.locator("#novice-msa").select_option("cache")
        page.locator("#novice-next").click()
        for _ in range(5):
            page.locator("#novice-next").dispatch_event("click")
        assert "4/4" in page.locator(".novice-heading").inner_text()
        for _ in range(8):
            page.locator("#novice-previous").dispatch_event("click")
        assert "1/4" in page.locator(".novice-heading").inner_text()
