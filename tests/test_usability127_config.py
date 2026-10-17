"""Usability127 U6/U7/U8 contracts; synthetic CPU-only inputs, no native engines."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nanocofold import cli, config, mock, target_prep
from nanocofold.platform.results import ResultReader
from nanocofold.utils import seq


@pytest.mark.parametrize("setting,value", [
    ("structure.provider", "alphafold9"), ("report.viewer", "notaviewer"), ("expression.host", "insect"),
    ("structure.provider", ""), ("report.viewer", ""), ("expression.host", ""),
])
def test_current_unknown_enum_fails_at_config_load(setting, value):
    with pytest.raises(ValueError, match=setting.replace(".", r"\.")):
        config.load_config(overrides=[setting + "=" + yaml.safe_dump(value).splitlines()[0]])


@pytest.mark.parametrize("setting,value", [
    *[("structure.provider", value) for value in ("boltz2", "boltz", "chai1", "none", "sequence", "mock", "BOLTZ2", "NONE", None)],
    *[("report.viewer", value) for value in ("3dmol", "none", "3DMOL", "NONE", None)],
    *[("expression.host", value) for value in ("ecoli_periplasmic", "mammalian", "MAMMALIAN", " mammalian ", None)],
])
def test_supported_alias_case_and_unset_template_values_are_preserved(setting, value):
    cfg = config.load_config()
    original_thresholds = copy.deepcopy(cfg["score"]["thresholds"])
    original_rank = copy.deepcopy(cfg["rank"])
    config.cfg_set(cfg, setting, value)
    config._validate(cfg)
    assert config.cfg_get(cfg, setting) == value
    assert cfg["score"]["thresholds"] == original_thresholds and cfg["rank"] == original_rank


@pytest.mark.parametrize("setting,value", [("structure.provider", "historical_provider"), ("report.viewer", "historical_viewer"), ("expression.host", "insect")])
def test_historical_metadata_remains_readable_without_rewriting(setting, value, tmp_path):
    path = tmp_path / "run_config.yaml"
    source = config.load_config()
    config.cfg_set(source, setting, value)
    path.write_text(yaml.safe_dump(source))
    before = path.read_bytes()
    historical = config.load_config(path, historical=True)
    assert config.cfg_get(historical, setting) == value
    assert config.cfg_get(ResultReader(tmp_path).config, setting) == value
    assert path.read_bytes() == before


def test_numeric_sequence_error_has_no_unrelated_colon_advice():
    with pytest.raises(ValueError) as error:
        config.load_config(overrides=["target.sequence=12345"])
    assert str(error.value) == "target.sequence는 문자열이어야 합니다."


@pytest.mark.parametrize("setting", ["target.sequence", "report.title"])
def test_mapping_created_by_unquoted_colon_retains_relevant_advice(setting):
    with pytest.raises(ValueError) as error:
        config.load_config(overrides=[setting + "=NanoCoFold: GFP"])
    assert "':'" in str(error.value) and "--set" in str(error.value)


@pytest.mark.parametrize("value", [-1, 2**32, True, .5, "12"])
def test_seed_diagnostic_names_setting_once(value):
    with pytest.raises(ValueError) as error:
        config.cfg_seed({"run":{"seed":value}}, "run.seed")
    assert "run.seed는" in str(error.value)
    assert str(error.value).count("seed") == 1


@pytest.mark.parametrize("value", [0, 2**32-1, None])
def test_valid_seed_boundaries_and_null_fallback_are_unchanged(value):
    assert config.cfg_seed({"run":{"seed":value}}, "run.seed") == (42 if value is None else value)


def test_public_msa_error_explains_upload_and_required_opt_in_without_uploading():
    with pytest.raises(ValueError) as error:
        config.load_config(overrides=["screen.colabfold.msa_source=public_api"])
    assert "외부 서버로 전송" in str(error.value)
    assert "screen.allow_public_msa_server=true" in str(error.value)
    consented = config.load_config(overrides=["screen.colabfold.msa_source=public_api", "screen.allow_public_msa_server=true"])
    assert consented["screen"]["allow_public_msa_server"] is True


@pytest.mark.parametrize("setting", ["target.fasta", "target.pdb", "screen.vhh_fasta", "mature.wt_fasta"])
def test_directory_is_distinguished_from_a_missing_input_file(setting, tmp_path):
    with pytest.raises(IsADirectoryError) as error:
        config.require_input_file(tmp_path, setting)
    assert setting in str(error.value) and "디렉터리가 아닌 파일" in str(error.value)
    with pytest.raises(FileNotFoundError, match="파일을 찾을 수 없습니다"):
        config.require_input_file(tmp_path / "missing", setting)
    source = tmp_path / "existing.fasta"
    source.write_text(">source\nACDE\n")
    assert config.require_input_file(source, setting) == source
    assert source.read_text() == ">source\nACDE\n"


@pytest.mark.parametrize("setting", ["target.fasta", "target.pdb"])
def test_target_sequence_directory_error_is_actionable(setting, tmp_path):
    cfg = config.load_config()
    config.cfg_set(cfg, setting, str(tmp_path))
    with pytest.raises(IsADirectoryError, match="디렉터리가 아닌 파일"):
        target_prep.resolve_target_sequence(cfg)


def test_direct_fasta_reader_directory_error_is_actionable(tmp_path):
    with pytest.raises(IsADirectoryError, match="FASTA에는 디렉터리가 아닌 파일"):
        seq.read_fasta(tmp_path)


@pytest.mark.parametrize("setting,mode", [("target.fasta", "screen"), ("target.pdb", "screen"),
    ("screen.vhh_fasta", "screen"), ("mature.wt_fasta", "mature")])
def test_cli_directory_preflight_uses_the_same_actionable_helper_before_run_writes(setting, mode, tmp_path):
    cfg = config.load_config(overrides=["run.mode=" + mode])
    config.cfg_set(cfg, setting, str(tmp_path))
    cfg["run"]["outdir"] = str(tmp_path / "uncreated-runs")
    with pytest.raises(IsADirectoryError) as error:
        cli._preflight_input_files(cfg, SimpleNamespace(command=mode))
    assert setting in str(error.value) and "디렉터리가 아닌 파일" in str(error.value)
    assert not Path(cfg["run"]["outdir"]).exists()


@pytest.mark.parametrize("sequence", ["A", "AC"])
def test_single_residue_provided_structure_warns_without_inventing_length_gate(sequence, tmp_path):
    pdb = tmp_path / "target.pdb"
    mock.make_mock_target_only(sequence, pdb)
    original = pdb.read_bytes()
    cfg = config.load_config(overrides=["run.mode=screen", "target.pdb=" + str(pdb),
        "target.hotspots=[seq1]", "truncate.enabled=false", "structure.msa.backend=empty"])
    result = target_prep.prepare_target(cfg, tmp_path / "prepared", mock=False)
    assert result["sequence"] == sequence
    assert any("아미노산 잔기가 1개만 있습니다" in warning for warning in result["warnings"]) is (len(sequence) == 1)
    assert pdb.read_bytes() == original
    assert cfg["truncate"]["enabled"] is False  # Explicit input remains unchanged.


def test_imports_are_bound_to_the_selected_checkout():
    expected = Path(__file__).resolve().parents[1] / "nanocofold/config.py"
    assert Path(config.__file__).resolve() == expected
