"""Input/configuration regressions; all files and commands are local CPU fixtures."""
from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from nanocofold import cli, config, developability, mature, mock, structure_input, target_prep
from nanocofold.utils import seq


def test_missing_target_fasta_never_falls_back_to_a_supplied_pdb(tmp_path):
    pdb = mock.make_mock_target_only("AGC", tmp_path / "fallback.pdb")
    before = pdb.read_bytes()
    cfg = config.load_config()
    cfg["target"].update(fasta=str(tmp_path / "typo.fasta"), pdb=str(pdb), sequence="")
    with pytest.raises(FileNotFoundError, match=r"target\.fasta.*typo\.fasta"):
        target_prep.resolve_target_sequence(cfg)
    assert pdb.read_bytes() == before


def test_explicit_missing_target_pdb_has_its_setting_and_filename(tmp_path):
    cfg = config.load_config()
    cfg["target"].update(pdb=str(tmp_path / "missing.pdb"), fasta=None, sequence="")
    with pytest.raises(FileNotFoundError, match=r"target\.pdb.*missing\.pdb"):
        target_prep.resolve_target_sequence(cfg)


def test_valid_target_fasta_keeps_precedence_and_input_bytes(tmp_path):
    path = tmp_path / "target.fasta"
    path.write_text(">sp|P01308|INS_HUMAN\nACDE\n", encoding="utf-8")
    before = path.read_bytes()
    cfg = config.load_config()
    cfg["target"].update(fasta=str(path), sequence="")
    assert target_prep.resolve_target_sequence(cfg) == "ACDE"
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", ["ACDEF", "", {"sequence": "ACDEF"}, {}, 0, False, 7])
def test_auxiliary_chains_require_a_collection_not_character_iteration(value):
    with pytest.raises(ValueError, match=r"target\.aux_chains"):
        target_prep._parse_aux_chains({"target": {"aux_chains": value}})


def test_auxiliary_chain_legacy_list_and_empty_null_remain_supported():
    assert target_prep._parse_aux_chains({"target": {"aux_chains": ["ACD", {"name": "partner", "sequence": "EFG"}]}}) == [
        {"name": "aux1", "sequence": "ACD"}, {"name": "partner", "sequence": "EFG"}]
    assert target_prep._parse_aux_chains({"target": {"aux_chains": None}}) == []


def test_literal_yaml_alias_text_uses_the_same_parser_when_a_run_is_reopened(tmp_path):
    args = SimpleNamespace(command="prepare", set=["run.name=*abc"], config=None,
                           run_dir=None, run_id="literal", mock=True, dry_run=False)
    cfg = cli._load(args)
    cfg["run"]["outdir"] = str(tmp_path)
    run, _ = cli._open_run(cfg, args)
    args.run_dir = str(run)
    again = cli._load(args)
    cli._open_run(again, args)
    assert again["run"]["name"] == "*abc"
    assert yaml.safe_load((run / "run_config.yaml").read_text())["run"]["name"] == "*abc"


@pytest.mark.parametrize("setting", ["target.name", "run.name", "report.title"])
def test_mapping_shaped_scalar_override_is_rejected_with_the_setting(setting):
    with pytest.raises(ValueError, match=setting.replace(".", r"\.")):
        config.load_config(overrides=[setting + "=GFP: S65T"])
    good = config.load_config(overrides=[setting + '="GFP: S65T"'])
    assert config.cfg_get(good, setting) == "GFP: S65T"


def test_historical_scalar_metadata_can_be_read_without_rewriting_it(tmp_path):
    path = tmp_path / "saved.yaml"
    path.write_text("run:\n  name: {GFP: S65T}\n", encoding="utf-8")
    before = path.read_bytes()
    assert config.load_config(path, historical=True)["run"]["name"] == {"GFP": "S65T"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("setting", ["specificity.stage_1n", "complex.msa_sourc", "rank.weights.ipsea", "score.thresholds.name", "truncate.source"])
def test_fixed_setting_typos_warn_instead_of_being_silently_exempt(setting):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = config.load_config(overrides=[setting + "=1"])
    assert any(setting in str(item.message) for item in caught)
    assert config.cfg_get(cfg, setting) == 1, "unknown extensions remain preserved, not forbidden"


def test_dynamic_metrics_and_extension_metadata_are_preserved_without_typo_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = config.load_config(overrides=["rank.weights.new_measurement=0.3", "metadata={source: custom}",
                                            "specificity.metadata={campaign: retained}"])
    assert cfg["rank"]["weights"]["new_measurement"] == .3
    assert cfg["metadata"] == {"source": "custom"}
    assert cfg["specificity"]["metadata"] == {"campaign": "retained"}
    assert not any("기본 설정에 없는 키" in str(item.message) for item in caught)


def test_pdb_author_number_reversal_is_an_explicit_blocker_without_reordering_source(tmp_path):
    path = mock.make_mock_target_only("AGCST", tmp_path / "reverse.pdb")
    lines = path.read_text().splitlines()
    atoms = [line for line in lines if line.startswith("ATOM")]
    groups = {number: [line for line in atoms if int(line[22:26]) == number] for number in range(1, 6)}
    path.write_text("SEQRES   1 A    5  ALA GLY CYS SER THR\n" + "\n".join(line for number in range(5, 0, -1) for line in groups[number]) + "\nEND\n")
    before = path.read_bytes()
    inspected = structure_input.inspect_structure(path, tmp_path / "inspect")
    assert any("역행" in message for message in inspected["chains"][0]["blockers"])
    assert inspected["chains"][0]["sequence"] == "TSCGA", "report the recorded order; never silently repair it"
    assert path.read_bytes() == before
    cfg = config.load_config()
    cfg["target"].update(pdb=str(path), sequence="", fasta=None)
    with pytest.raises(ValueError, match=r"target\.pdb.*역행"):
        target_prep.resolve_target_sequence(cfg)
    assert path.read_bytes() == before


@pytest.mark.parametrize("labels", [[(-2, " "), (-1, " "), (0, " ")], [(52, " "), (52, "A"), (52, "B")]])
def test_negative_and_insertion_numbering_remain_readable(tmp_path, labels):
    path = mock.make_mock_target_only("AGC", tmp_path / "labels.pdb")
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("ATOM"):
            number, insertion = labels[int(line[22:26]) - 1]
            line = line[:22] + f"{number:4d}" + insertion + line[27:]
        lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    inspected = structure_input.inspect_structure(path, tmp_path / "inspect")
    assert inspected["chains"][0]["sequence"] == "AGC"
    assert not inspected["chains"][0]["blockers"]


@pytest.mark.parametrize("command,setting", [("screen", "screen.vhh_fasta"), ("run", "screen.vhh_fasta"), ("mature", "mature.wt_fasta")])
def test_bad_library_path_fails_before_target_prediction_or_new_run_files(tmp_path, monkeypatch, command, setting):
    path = tmp_path / "missing-library.fasta"
    argv = [command, "--mock", "--run-id", "invalid", "--set", "run.outdir=" + str(tmp_path / "runs"), "--set", setting + "=" + str(path)]
    if command == "run":
        argv += ["--mode", "screen"]
    args = cli.build_parser().parse_args(argv)
    monkeypatch.setattr(cli, "_target_phase", lambda *a, **k: pytest.fail("prediction reached before input validation"))
    with pytest.raises(FileNotFoundError, match=setting.replace(".", r"\.") + ".*missing-library"):
        cli._execute_command(args)
    assert not (tmp_path / "runs").exists()


def test_wrong_wt_fasta_path_reports_the_supplied_filename(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"mature\.wt_fasta.*typo-wt"):
        mature.resolve_wt_sequence({"mature": {"wt_fasta": str(tmp_path / "typo-wt.fasta")}})


@pytest.mark.parametrize("name", ["deamidation", "isomerization", "oxidation"])
def test_motif_strings_are_not_split_into_single_letter_regexes(name):
    cfg = config.load_config()
    cfg["developability"][name] = "NG"
    with pytest.raises(ValueError, match="developability." + name):
        developability.sequence_liabilities("ANGGCA", cfg)
    with pytest.raises(ValueError, match="developability." + name):
        config._validate(cfg)


def test_motif_regex_lists_and_empty_disable_are_still_supported():
    cfg = config.load_config()
    cfg["developability"].update(deamidation=["N[GS]"], isomerization=[], oxidation=[])
    result = developability.sequence_liabilities("ANGGCA", cfg, {"CDR1": (1, 2), "CDR2": (3, 4), "CDR3": (5, 6)})
    assert result["dev_deamidation_cdr"] is True
    assert result["dev_oxidation_cdr"] is False
    assert result["dev_isomerization_cdr"] is False


@pytest.mark.parametrize("kind", ["fasta", "config"])
def test_non_utf8_input_has_filename_and_resave_guidance_without_modifying_it(tmp_path, kind):
    path = tmp_path / ("legacy.fasta" if kind == "fasta" else "legacy.yaml")
    path.write_bytes((">표적\nACDE\n" if kind == "fasta" else "run:\n  name: 표적\n").encode("cp949"))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="UTF-8") as error:
        (seq.read_fasta(path) if kind == "fasta" else config.load_config(path))
    assert str(path) in str(error.value)
    assert path.read_bytes() == before


def test_bom_utf8_fasta_and_yaml_remain_supported(tmp_path):
    fasta, settings = tmp_path / "bom.fasta", tmp_path / "bom.yaml"
    fasta.write_text(">나노바디2\nACDE\n", encoding="utf-8-sig")
    settings.write_text("target:\n  name: 표적\n", encoding="utf-8-sig")
    assert seq.read_fasta(fasta) == {"나노바디2": "ACDE"}
    assert config.load_config(settings)["target"]["name"] == "표적"


def test_cached_default_parsing_is_reused_but_returned_configs_are_independent(monkeypatch):
    original = config.read_config_yaml
    content = config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    calls = []
    if hasattr(config, "_parsed_default_config"):
        config._parsed_default_config.cache_clear()
    def counted(value):
        if value == content:
            calls.append(value)
        return original(value)
    monkeypatch.setattr(config, "read_config_yaml", counted)
    first = config.load_config()
    first["rank"]["weights"]["ipsae"] = -999
    second = config.load_config()
    assert second["rank"]["weights"]["ipsae"] == 1.0
    assert len(calls) == 1


def test_default_cache_rereads_edits_deletion_and_corruption(tmp_path, monkeypatch):
    path = tmp_path / "default.yaml"
    original = config.DEFAULT_CONFIG_PATH.read_text()
    path.write_text(original)
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", path)
    assert config.load_config()["target"]["name"] == "target"
    path.write_text(original.replace("name: target", "name: edited"))
    assert config.load_config()["target"]["name"] == "edited"
    path.write_text("run: [\n")
    with pytest.raises(ValueError, match="기본 설치 설정"):
        config.load_config()
    path.unlink()
    with pytest.raises(ValueError, match="기본 설치 설정"):
        config.load_config()


@pytest.mark.parametrize("missing", [False, True])
def test_opt_in_boltz_selection_is_numeric_and_independent_of_input_order(monkeypatch, missing):
    cfg = config.load_config()
    candidates = pd.DataFrame({"design_id": ["z", "b", "a"], "sequence": ["ACD"] * 3})
    prelim = pd.DataFrame({"design_id": ["z", "b", "a"], "iptm": [np.nan] * 3 if missing else ["0.8", "0.9", "0.9"]})
    monkeypatch.setattr(cli, "_score_candidates", lambda *a, **k: prelim.copy())
    if missing:
        with pytest.warns(UserWarning, match="ID"):
            selected = cli._select_boltz_candidates(cfg, candidates, prelim, {}, "design_id", 2)
    else:
        selected = cli._select_boltz_candidates(cfg, candidates, prelim, {}, "design_id", 2)
    assert selected.design_id.tolist() == ["a", "b"]
    policy = selected.attrs["boltz2_selection_policy"]
    assert policy["selected_ids"] == ["a", "b"]
    assert policy["all_iptm_unassessed"] is missing
    before = candidates.copy(deep=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        again = cli._select_boltz_candidates(cfg, candidates.iloc[::-1], prelim.iloc[::-1], {}, "design_id", 2)
    assert again.design_id.tolist() == selected.design_id.tolist()
    pd.testing.assert_frame_equal(candidates, before)


def test_empty_boltz_selection_retains_a_machine_readable_unavailable_receipt():
    candidates = pd.DataFrame({"design_id": ["a"], "sequence": ["ACD"]})
    selected = cli._select_boltz_candidates({}, candidates, pd.DataFrame(), {}, "design_id", 2)
    assert selected.empty
    assert selected.attrs["boltz2_selection_policy"]["unavailable_reason"] == "no_prediction_records"


def test_cli_default_port_avoids_the_other_project_but_explicit_old_port_stays_allowed():
    assert cli.build_parser().parse_args(["serve"]).port == 18765
    assert cli.build_parser().parse_args(["serve", "--port", "8765"]).port == 8765


@pytest.mark.parametrize("failure_flag", [True, "true", 1])
def test_tier2_does_not_prioritize_incomplete_mapping_or_mutate_diagnostics(failure_flag):
    candidates = pd.DataFrame({"design_id": ["incomplete", "complete"]})
    metrics = pd.DataFrame({"design_id": ["incomplete", "complete"],
                            "hotspot_contact_frac": [0.5, 0.25], "pdockq": [0.5, 0.5],
                            "hotspot_mapping_failed": [failure_flag, False]})
    original = metrics.copy(deep=True)
    ordered, policy = cli._tier2_selection({}, candidates, metrics, "design_id")
    assert ordered == ["complete", "incomplete"]
    assert policy["incomplete_mapping_count"] == 1
    assert policy["hotspot_contact_frac_mapping_policy"] == "incomplete_mapping_contact_fraction_not_used"
    pd.testing.assert_frame_equal(metrics, original)


def test_tier2_disabled_hotspot_weight_uses_pdockq_even_with_mapping_receipts():
    candidates = pd.DataFrame({"design_id": ["incomplete", "complete"]})
    metrics = pd.DataFrame({"design_id": ["incomplete", "complete"],
                            "hotspot_contact_frac": [0.5, 0.25], "pdockq": [0.9, 0.5],
                            "hotspot_mapping_failed": [True, False]})
    ordered, policy = cli._tier2_selection({"rank": {"weights": {"hotspot_contact_frac": 0}}},
                                          candidates, metrics, "design_id")
    assert ordered == ["incomplete", "complete"]
    assert policy["sort_columns_descending"] == ["pdockq"]
