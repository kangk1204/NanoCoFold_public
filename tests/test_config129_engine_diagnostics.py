"""Configuration diagnostics without inference or changes to saved results."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from nanocofold import cli, config
from nanocofold.platform.results import ResultReader
from nanocofold.utils import msa


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("engine", ["nope", "", None, 7, True, [], {}, "BOLTZ2", "boltz2 "])
def test_specificity_engine_is_rejected_when_loading_current_settings(engine, enabled):
    with pytest.raises(ValueError, match=r"specificity\.engine"):
        config.load_config(overrides=[f"specificity.engine={json.dumps(engine)}",
                                      f"specificity.enabled={str(enabled).lower()}"])


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("engine", ["boltz2", "chai1"])
def test_supported_specificity_engines_keep_their_original_value(engine, enabled):
    cfg = config.load_config(overrides=[f"specificity.engine={engine}",
                                       f"specificity.enabled={str(enabled).lower()}"])
    assert cfg["specificity"]["engine"] == engine


@pytest.mark.parametrize("engine", ["legacy_engine", "", None])
def test_historical_specificity_metadata_is_readable_and_unchanged(engine, tmp_path):
    cfg = config.load_config()
    cfg["specificity"]["engine"] = engine
    saved = tmp_path / "run_config.yaml"
    saved.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    before = saved.read_bytes()
    assert config.load_config(saved, historical=True)["specificity"]["engine"] == engine
    assert ResultReader(tmp_path).config["specificity"]["engine"] == engine
    assert saved.read_bytes() == before


@pytest.mark.parametrize("command", ["screen", "mature"])
def test_invalid_specificity_engine_stops_cli_before_creating_a_run(command, tmp_path, capsys):
    run = tmp_path / "must-not-be-created"
    assert cli.main([command, "--mock", "--run-dir", str(run),
                     "--set", "specificity.engine=nope"]) != 0
    output = capsys.readouterr()
    diagnostic = output.out + output.err
    assert "specificity.engine" in diagnostic
    assert "boltz2" in diagnostic and "chai1" in diagnostic
    assert not run.exists()


def test_unknown_msa_backend_error_lists_supported_choices_without_running_tools(tmp_path, monkeypatch):
    def unexpected_tool(*args, **kwargs):
        pytest.fail("An invalid backend must not invoke an external tool")
    monkeypatch.setattr(msa.shell, "run", unexpected_tool)
    with pytest.raises(ValueError) as error:
        msa.ensure_a3m("ACDEFGHIKL", {"backend": "nope", "cache_dir": str(tmp_path)})
    diagnostic = str(error.value)
    assert "nope" in diagnostic
    for backend in ("cache", "mmseqs", "colabfold_search", "empty"):
        assert backend in diagnostic


def test_new_diagnostics_use_the_actual_english_and_korean_translator(tmp_path):
    with pytest.raises(ValueError) as specificity_error:
        config.load_config(overrides=["specificity.engine=nope"])
    with pytest.raises(ValueError) as msa_error:
        msa.ensure_a3m("ACDEFGHIKL", {"backend": "nope", "cache_dir": str(tmp_path)})
    messages = [str(specificity_error.value), str(msa_error.value)]
    translator = Path(__file__).resolve().parents[1] / "nanocofold/templates/platform-i18n.js"
    script = """
const fs = require('node:fs'), vm = require('node:vm');
const context = {}; vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const i = context.I18N, messages = JSON.parse(process.argv[2]);
const output = {};
for (const language of ['en', 'ko']) {
  i.setLanguage(language, {persist: false});
  output[language] = messages.map(message => i.value(i.term(message)));
}
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run(["node", "-e", script, str(translator), json.dumps(messages)],
                            text=True, capture_output=True, check=True)
    rendered = json.loads(result.stdout)
    assert rendered["ko"] == messages
    assert rendered["en"] == [
        "specificity.engine must be boltz2 or chai1.",
        "Unsupported MSA backend: nope. Allowed values: cache, mmseqs, colabfold_search, empty.",
    ]
