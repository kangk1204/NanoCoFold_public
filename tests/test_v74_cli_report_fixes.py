"""Fresh computation provenance and honest report/explorer presentation."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess

import pandas as pd
import pytest

from nanocofold import __version__, cli, config, report
from nanocofold.state import RunLock, RunLockedError, RunState


@pytest.mark.parametrize("precreate", [False, True])
def test_locked_new_explicit_directory_gets_compute_version_before_handler(tmp_path, monkeypatch, precreate):
    root = tmp_path / "explicit-new"
    if precreate:
        root.mkdir()
    seen = []
    def handler(args):
        saved = RunState(Path(args.run_dir)).data
        seen.append(saved)
        with pytest.raises(RunLockedError):
            with RunLock(root):
                pytest.fail("dispatcher must retain the run lock")
        return 0
    monkeypatch.setattr(cli, "cmd_run", handler)
    assert cli.main(["run", "--mock", "--run-dir", str(root)]) == 0
    assert seen[0]["application_version"] == __version__
    assert seen[0]["application_version_source"] == {"kind": "new_cli_computation"}
    assert "scoring_version" not in seen[0]


@pytest.mark.parametrize("evidence", ["config", "state_unknown", "state_old", "artifact", "dry"])
def test_explicit_existing_or_dry_run_is_never_backfilled(tmp_path, monkeypatch, evidence):
    root = tmp_path / evidence
    root.mkdir()
    preserved = None
    if evidence == "config":
        cfg = config.load_config(overrides=["run.mock=true"])
        config.dump_resolved(cfg, root / "run_config.yaml")
        preserved = (root / "run_config.yaml").read_bytes()
    elif evidence.startswith("state"):
        state = RunState(root)
        if evidence == "state_old":
            state.data["application_version"] = "old-original-version"
        state.save()
    elif evidence == "artifact":
        (root / "old-results.csv").write_bytes(b"original,score\nold,0.123456789012345\n")
    seen = []
    monkeypatch.setattr(cli, "cmd_run", lambda args: seen.append(RunState(Path(args.run_dir)).data) or 0)
    args = ["run", "--mock", "--run-dir", str(root)] + (["--dry-run"] if evidence == "dry" else [])
    assert cli.main(args) == 0
    expected = "old-original-version" if evidence == "state_old" else None
    assert seen[0].get("application_version") == expected
    if preserved:
        assert (root / "run_config.yaml").read_bytes() == preserved
    if evidence == "artifact":
        assert (root / "old-results.csv").read_bytes() == b"original,score\nold,0.123456789012345\n"


def test_artifact_arriving_before_lock_acquisition_prevents_fresh_marker(tmp_path, monkeypatch):
    import nanocofold.state as state_module
    root = tmp_path / "contended"
    class InjectArtifact(RunLock):
        def __enter__(self):
            result = super().__enter__()
            (root / "prior-result.json").write_bytes(b'{"original":true}')
            return result
    monkeypatch.setattr(state_module, "RunLock", InjectArtifact)
    seen = []
    monkeypatch.setattr(cli, "cmd_run", lambda args: seen.append(RunState(root).data) or 0)
    assert cli.main(["run", "--mock", "--run-dir", str(root)]) == 0
    assert "application_version" not in seen[0]
    assert (root / "prior-result.json").read_bytes() == b'{"original":true}'


def _make_report(tmp_path, n=205, mock=True, selected=0, cdr=False):
    run = tmp_path / "report-run"
    (run / "01_target").mkdir(parents=True)
    residues = pd.DataFrame({"label": [f"A:{i+1}" for i in range(n)], "aa": "A", "relative_sasa": .123456789012345})
    residues.to_csv(run / "01_target/hotspot_table.csv", index=False)
    sets = pd.DataFrame({"set_id": ["auto1"] * 6, "residue": [f"A:{i+1}" for i in range(6)], "score": .987654321012345})
    sets.to_csv(run / "01_target/hotspot_sets.csv", index=False)
    inputs = {p: p.read_bytes() for p in (run / "01_target").iterdir()}
    rows = pd.DataFrame([{"design_id": "original(A)", "sequence": "ACDE", "ipsae": .7080264101234567,
                          "iptm": .00004, "dev_cdr_evaluated": cdr, "final_evaluated": True,
                          "final_eligible": cdr, "exclusion_reason": "" if cdr else "developability_unassessed"}])
    original = rows.copy(deep=True)
    cfg = {"run": {"mode": "screen", "mock": mock}, "report": {"viewer": "none"}}
    context = {"hotspots": [f"A:{i+1}" for i in range(6)], "warnings": []}
    context_before = copy.deepcopy(context)
    selection = rows.iloc[:selected].copy() if selected is not None else None
    path = report.generate_report(run, cfg, context, scored=rows, selected=selection)
    pd.testing.assert_frame_equal(rows, original)
    assert context == context_before
    for file, before in inputs.items():
        assert file.read_bytes() == before
    return path


@pytest.mark.parametrize("count", [183, 205])
def test_whole_target_table_is_folded_counted_and_explicitly_truncated(tmp_path, count):
    text = _make_report(tmp_path, n=count).read_text()
    attrs, body = re.search(r'<details id="target-residue-properties"([^>]*)>([\s\S]*?)</details>', text).groups()
    assert "open" not in attrs
    assert "전체 표적 잔기 특성" in body and "전체 표적 잔기와 특성 값" in body
    assert "결합 부위 잔기 특성" not in body
    assert f'{min(count, 200)} / {count}' in body
    assert '처음 200개 잔기' in body if count > 200 else '처음 200개 잔기' not in body
    assert body.count("<tr>") == min(count, 200) + 1
    assert 'href="01_target/hotspot_table.csv"' in body
    assert re.search(r'선택한 결합 부위 잔기 수:</span> 6 ', body)
    assert 'title="0.7080264101234567"' in text and ">0.70802641<" in text
    assert 'title="4e-05"' in text
    assert "순위와 적격 판정은 저장된 값을 사용합니다." in text
    assert 'href="03_score/scored_candidates.csv"' not in text  # No invented/broken CSV link.
    runtime = re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>', text)[1]
    from test_audit126_report_warnings import execute_subset
    labels = ["전체 표적 잔기 특성", "전체 표적 잔기와 특성 값", "합성 mock 연습 · 기록 선별 0건"]
    translated = execute_subset(runtime, labels)
    assert translated["ko"] == labels
    assert all(not re.search("[가-힣]", value) for value in translated["en"])


@pytest.mark.parametrize("mock,selected,cdr,notice,cdr_notice", [
    (True, 0, False, True, True), (True, 0, True, True, False),
    (False, 0, False, False, False), (True, 1, False, False, False),
    (True, None, False, False, False),
])
def test_mock_zero_notice_requires_recorded_zero_and_keeps_unassessed_distinction(tmp_path, mock, selected, cdr, notice, cdr_notice):
    text = _make_report(tmp_path, mock=mock, selected=selected, cdr=cdr).read_text()
    shown = re.search(r'<div id="mock-zero-selection"[\s\S]*?</div>', text)
    assert bool(shown) is notice
    if shown:
        assert ("CDR 미평가 모델:" in shown[0]) is cdr_notice
        assert text.index(shown[0]) < text.index('id="report-target"')
        assert "이 연습의 선별 결과는 0건입니다." in shown[0]


@pytest.mark.parametrize("reason,cdr,unassessed", [
    ("not_developability_unassessed", True, False),
    ("developability_unassessed_extra", True, False),
    ("ipsae;developability_unassessed", None, True),
    ("developability_unassessed;ipsae", None, True),
])
def test_cdr_explanation_recognizes_complete_reason_tokens_only(tmp_path, reason, cdr, unassessed):
    rows = pd.DataFrame([{"design_id": "original", "sequence": "ACDE", "dev_cdr_evaluated": cdr,
                          "exclusion_reason": reason, "final_evaluated": True, "final_eligible": False}])
    original = rows.copy(deep=True)
    path = report.generate_report(tmp_path, {"run": {"mode": "screen", "mock": True},
                                "report": {"viewer": "none"}}, {}, rows, rows.iloc[:0])
    notice = re.search(r'<div id="mock-zero-selection"[\s\S]*?</div>', path.read_text())[0]
    assert ("CDR 미평가 모델:" in notice) is unassessed
    pd.testing.assert_frame_equal(rows, original)


def _current_offline_source(payload):
    """Exercise shared UI sources without mutating the generated distribution."""
    template = (report.TEMPLATE_DIR / "platform_offline.html").read_text()
    scripts = list(re.finditer(r"<script>([\s\S]*?)</script>", template))
    for match in reversed(scripts):
        content = match[1]
        if "const NCF_TRANSLATIONS = Object.freeze" in content:
            template = template[:match.start(1)] + (report.TEMPLATE_DIR / "platform-i18n.js").read_text() + template[match.end(1):]
        elif "function renderScatter()" in content:
            template = template[:match.start(1)] + (report.TEMPLATE_DIR / "platform.js").read_text() + template[match.end(1):]
    # A real Plotly chart exercises redraw/click lifecycle; the deliberately
    # absent scientific structures exercise the honest missing/empty path.
    plotly, evidence = report._resolve_viewer_asset({}, "plotly.min.js", local_only=True)
    assert evidence["ok"]
    template = template.replace("__PLOTLY_JS__", plotly.decode())
    for token in ("__3DMOL_JS__", "__MOLSTAR_JS__", "__MOLSTAR_CSS__"):
        template = template.replace(token, "")
    return template.replace("__RESULT_DATA__", json.dumps(payload).replace("<", "\\u003c"))


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in real CPU Chromium")
def test_shared_explorer_summaries_raw_values_locale_empty_and_selection(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    rows = [{"candidate_id": "boltz2__original", "predictor_engine": "boltz2", "sequence": "ACDE", "evaluated": True,
             "ipsae": .7080264101234567, "iptm": .00004, "final_eligible": False, "dev_cdr_evaluated": False,
             "exclusion_reasons": ["developability_unassessed"]},
            {"candidate_id": "chai1__other", "predictor_engine": "chai1", "sequence": "FGHI", "evaluated": False,
             "ipsae": None, "iptm": None, "final_eligible": None}]
    observations = [{"candidate_id": "boltz2__original", "engine": "boltz2", "seed": 42, "iptm": .8},
                    {"candidate_id": "boltz2__original", "engine": "boltz2", "seed": 42, "iptm": None}]
    payload = {"summary": {"id": "practice", "mode": "screen", "name": "Practice", "selected_total": 0,
                           "status": "complete", "config": {"run": {"mock": True}}},
               "candidates": {"rows": rows}, "observations": {"rows": observations}, "structures": [],
               "filters": {}, "provenance": {"source_artifacts": []}}
    output = tmp_path / "explorer.html"
    output.write_text(_current_offline_source(payload))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 390, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(output.as_uri())
        expect(page.locator('#candidate-table td[title="0.7080264101234567"]')).to_have_text("0.708")
        expect(page.locator('#candidate-table td[title="0.00004"]')).to_have_text("0.000")
        expect(page.locator('[data-mock-zero-selection="true"]')).to_contain_text("Synthetic mock practice")
        page.locator('#plot-x').select_option('ipsae')
        page.locator('#plot-y').select_option('iptm')
        expect(page.locator('#scatter')).to_have_attribute('aria-label', 'Interface-score comparison scatter plot')
        expect(page.locator('#scatter-summary')).to_contain_text('1 of 2')
        expect(page.locator('#distribution-summary')).to_contain_text('2 observations')
        expect(page.locator('#distribution-summary')).to_contain_text('1 unevaluated')
        expect(page.locator('#structure-view-summary')).to_contain_text('Select a candidate and observation')
        assert page.locator('#structure-view').bounding_box()['height'] < 180
        assert page.locator('#distribution-summary a').get_attribute('href') == '#candidate-detail'
        initial = page.evaluate('JSON.stringify(window.NANOCOFOLD_OFFLINE)')
        for lang in ['ko', 'en', 'ko', 'en']:
            page.locator('#language-select').select_option(lang)
            expect(page.locator('html')).to_have_attribute('lang', lang)
            expect(page.locator('#scatter')).to_have_attribute('aria-label', '계면 점수 비교 산점도' if lang == 'ko' else 'Interface-score comparison scatter plot')
            expect(page.locator('#distribution-summary')).to_contain_text('선택 후보 관측 2건' if lang == 'ko' else '2 observations')
            assert page.evaluate('JSON.stringify(window.NANOCOFOLD_OFFLINE)') == initial
        page.evaluate("document.getElementById('scatter').emit('plotly_click',{points:[{customdata:'boltz2__original'}]})")
        expect(page.locator('#candidate-detail')).to_contain_text('boltz2__original')
        page.locator('#engine-filter').select_option('chai1')
        expect(page.locator('#scatter-summary')).to_contain_text('No evaluated values')
        assert page.locator('#scatter').bounding_box()['height'] <= 170
        expect(page.locator('#distribution-summary')).to_contain_text('Select a candidate in the table to view its observation distribution.')
        page.locator('#candidate-table tbody button').first.click()
        expect(page.locator('#distribution-summary')).to_contain_text('has not been evaluated')
        assert page.evaluate("mockZeroSelectionNotice({mock:false,selected_total:0,config:{run:{mock:true}}},[]) === null")
        assert page.evaluate("mockZeroSelectionNotice({mock:true,selected_total:1},[]) === null")
        assert page.evaluate("mockZeroSelectionNotice({mock:true},[]) === null")
        assert page.evaluate('JSON.stringify(window.NANOCOFOLD_OFFLINE)') == initial
        assert errors == []
        page.screenshot(path=str(tmp_path / 'explorer-en-mobile.png'))
        browser.close()


@pytest.mark.skipif(os.environ.get("NANOCOFOLD_BROWSER_TEST") != "1", reason="Opt-in real CPU Chromium")
def test_folded_static_report_locales_counts_raw_tooltip_and_notice(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    path = _make_report(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(path.as_uri())
        target = page.locator('#target-residue-properties')
        assert not target.evaluate("node => node.open")
        expect(target.locator('summary')).to_contain_text('Properties of all target residues')
        expect(page.locator('#mock-zero-selection')).to_contain_text('Synthetic mock practice')
        for language in ['ko', 'en', 'ko', 'en']:
            page.locator('#language-select').select_option(language)
            expect(page.locator('html')).to_have_attribute('lang', language)
            expect(target.locator('summary')).to_contain_text('전체 표적 잔기 특성' if language == 'ko' else 'Properties of all target residues')
            assert not target.evaluate("node => node.open")
            if language == 'en':
                assert not re.search('[가-힣]', target.inner_text())
        target.locator('summary').click()
        expect(target).to_contain_text('200 / 205')
        expect(target).to_contain_text('Only the first 200 residues')
        assert target.locator('a').get_attribute('href') == '01_target/hotspot_table.csv'
        expect(page.locator('td[title="0.7080264101234567"]').first).to_have_text('0.70802641')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        assert errors == []
        page.screenshot(path=str(tmp_path / 'static-en-mobile.png'))
        browser.close()
