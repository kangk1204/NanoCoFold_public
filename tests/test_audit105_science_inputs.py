"""Regression coverage for audit105 science/input boundaries; no inference claims."""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from nanocofold import developability, handoff, mock, rank_select, score_complex, screen, specificity, structure_input, target_prep, truncate
from nanocofold.config import load_config
from nanocofold.utils import pdb_io, seq


@pytest.fixture
def cfg():
    return load_config()


def test_partial_domain_coordinates_record_input_coverage_and_preserve_sequence(tmp_path, cfg):
    protein = "ACDEFGHIKLMNPQRSTVWY"
    path = mock.make_mock_target_only(protein, tmp_path / "domain.pdb")
    original = path.read_bytes()
    input_sequence = protein + "G" * 200
    cfg["target"].update(sequence=input_sequence, pdb=str(path), chain="A", hotspots=["seq1"])
    cfg["structure"].update(provider="none", msa={"backend": "empty"})
    cfg["truncate"]["enabled"] = False
    result = target_prep.prepare_target(cfg, tmp_path / "run")
    mapping = result["qc"]["sequence_structure_mapping"]
    assert mapping["structure_coverage"] == 1
    assert mapping["input_coordinate_coverage"] == pytest.approx(20 / 220)
    assert mapping["unmapped_input_residues"] == 200
    assert mapping["unmapped_input_ranges"] == [[21, 220]]
    assert mapping["partial_input_coverage"] is True
    assert any("200잔기" in message and "평가하지 못" in message for message in result["warnings"])
    assert json.loads((tmp_path / "run/01_target/target_qc.json").read_text())["sequence_structure_mapping"] == mapping
    assert result["sequence"] == input_sequence
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="제공 구조에 존재하지"):
        target_prep._resolve_explicit_hotspots([{"chain": "A", "kind": "seq", "number": 100}], "A", input_sequence,
                                             {f"A:{n}": n for n in range(1, 21)}, {n: f"A:{n}" for n in range(1, 21)})


def test_small_partial_coverage_is_also_visible(tmp_path):
    protein = "ACDEFGHIKLMNPQRSTVWY"
    path = mock.make_mock_target_only(protein, tmp_path / "domain.pdb")
    warnings, qc = [], {}
    target_prep._structure_index_maps(path, "A", protein + "GG", warnings=warnings, mapping_qc=qc)
    assert qc["input_coordinate_coverage"] == pytest.approx(20 / 22)
    assert qc["unmapped_input_ranges"] == [[21, 22]]
    assert warnings


def test_nucleotide_ambiguity_is_a_hint_without_destructive_translation(tmp_path, cfg):
    protein_or_dna = "ACGT" * 15
    assert seq.clean_sequence(protein_or_dna) == protein_or_dna
    assert "DNA" in seq.nucleotide_ambiguity_hint(protein_or_dna)
    assert seq.nucleotide_ambiguity_hint("ACDEFGHIKLMNPQRSTVWY" * 3) is None
    assert seq.nucleotide_ambiguity_hint("ACG") is None
    cfg["target"].update(sequence=protein_or_dna, pdb=None, hotspots=[])
    cfg["structure"].update(provider="none", msa={"backend": "empty"})
    cfg["truncate"]["enabled"] = False
    result = target_prep.prepare_target(cfg, tmp_path / "run")
    assert result["sequence"] == protein_or_dna
    assert any("DNA" in text for text in result["warnings"])


@pytest.mark.parametrize("resnames", [("DA", "DC", "DG"), ("A", "U", "C"), ("PSU", "U", "C")])
def test_nucleic_context_is_preserved_and_blocks_unreviewed_design(tmp_path, resnames):
    structure = mock.build_complex_structure("AG", "AAA", [1])
    for residue, name in zip(structure[0]["B"], resnames):
        residue.resname = name
    path = mock.write_pdb(structure, tmp_path / "protein_nucleic.pdb")
    original = path.read_bytes()
    result = structure_input.inspect_structure(path, tmp_path / "inspect")
    protein = result["chains"][0]
    assert protein["sequence"] == "AG"
    assert not protein["blockers"]
    assert any("핵산" in blocker for blocker in protein["design_blockers"])
    context = pdb_io.load_structure(result["context_path"])
    for old, kept in zip(structure[0]["B"], context[0]["B"]):
        assert kept.resname == old.resname
        assert kept["CA"].get_coord() == pytest.approx(old["CA"].get_coord(), abs=.001)
    assert path.read_bytes() == original


def test_all_hetatm_protein_chain_is_explicit_failure(tmp_path):
    structure = mock.build_complex_structure("AG", "AG", [1])
    for residue in structure[0]["B"]:
        residue.id = ("H_" + residue.resname, residue.id[1], " ")
    source = mock.write_pdb(structure, tmp_path / "hetatm_protein.pdb")
    with pytest.raises(ValueError, match="모든 표준 단백질 잔기가 HETATM"):
        structure_input.inspect_structure(source, tmp_path / "inspect")


@pytest.mark.parametrize("source,confidence,expected", [
    ("generic", {"iptm": .95, "pair_chains_iptm": {"A": {"D": .15}, "D": {"A": .2}}}, .15),
    ("boltz2", {"iptm": .95, "pair_chains_iptm": {"0": {"3": .15}, "3": {"0": .2}}}, .15),
    ("chai1", {"iptm": .95, "chain_axis": {"chain_ids": ["D", "C", "B", "A"]},
               "per_chain_pair_iptm": [[[1, .9, .9, .2], [.9, 1, .9, .9], [.9, .9, 1, .9], [.15, .9, .9, 1]]]}, .15),
    ("opendde", {"iptm": .95, "engine": "opendde", "chain_axis": ["D", "C", "B", "A"],
                 "chain_pair_iptm": [[1, .9, .9, .2], [.9, 1, .9, .9], [.9, .9, 1, .9], [.15, .9, .9, 1]]}, .15),
])
def test_auxiliary_complex_iptm_cannot_promote_target_binder(source, confidence, expected):
    value, recorded_source, reason = score_complex.target_binder_iptm(confidence, "A", "D", ["A", "B", "C", "D"], source=source)
    assert value == expected
    assert "pair" in recorded_source and reason is None
    assert not score_complex.pass_flags({"iptm": value}, {})["pass_iptm"]


@pytest.mark.parametrize("confidence", [{"iptm": .95}, {"iptm": .95, "pair_chains_iptm": {"A": None}},
                                          {"iptm": .95, "chain_axis": {"chain_ids": ["A", "A", "C"]}, "per_chain_pair_iptm": [[1]]}])
def test_unmapped_multichain_confidence_is_unassessed(confidence):
    value, _, reason = score_complex.target_binder_iptm(confidence, "A", "C", ["A", "B", "C"])
    assert math.isnan(value) and reason


def test_null_confidence_keeps_measured_geometry(tmp_path, cfg):
    output = tmp_path / "prediction"
    mock.make_mock_boltz_output(output, "test", "A" * 8, "A" * 8, [3])
    confidence = next(output.glob("confidence_*.json"))
    confidence.write_text(json.dumps({"iptm": None, "ptm": None}))
    result = score_complex.score_complex(output, "A", "B", [3, "A:3", 3], cfg)
    assert math.isnan(result["iptm"]) and math.isnan(result["ptm"])
    assert result["n_contacts"] > 0
    assert result["n_hotspots_requested"] == 1
    assert not result["pass_iptm"]


def test_unassessed_cdr_liabilities_do_not_rank_or_change_assessed_population(cfg):
    complete = pd.DataFrame([{"design_id": "a", "sequence": "AAAA", "dev_flags": 1, "dev_cdr_evaluated": True},
                             {"design_id": "b", "sequence": "CCCC", "dev_flags": 3, "dev_cdr_evaluated": True}])
    cfg["rank"]["weights"] = {"dev_flags": -.4}
    original = rank_select.rank_candidates(complete, cfg).set_index("design_id")
    missing = {"design_id": "unassessed", "sequence": "AAAA", "dev_flags": 0, "dev_cdr_evaluated": False}
    ranked = rank_select.rank_candidates(pd.concat([complete, pd.DataFrame([missing])], ignore_index=True), cfg).set_index("design_id")
    assert ranked.loc["unassessed", ["rank", "composite_score"]].isna().all()
    for design_id in ("a", "b"):
        assert ranked.loc[design_id, "composite_score"] == original.loc[design_id, "composite_score"]
    cfg["rank"].update(fallback_to_ineligible=True, final_n=3, per_cluster=3)
    selected = rank_select.select_final(ranked.reset_index(), cfg)
    assert "unassessed" not in set(selected.design_id)


def test_cdr3_clustering_and_tied_ranking_are_input_order_independent(cfg):
    a, b = "AADRYGSSWYLDRRSYDY", "AADRYGSSWYLDQRRSPDEY"
    assert rank_select.cdr3_identity(a, b) == rank_select.cdr3_identity(b, a)
    frame = pd.DataFrame({"design_id": ["z", "a"], "sequence": [a, b], "cdr3_sequence": [a, b], "iptm": [.8, .8]})
    cfg["rank"]["weights"] = {"iptm": 1.0}
    first = rank_select.rank_candidates(frame, cfg)
    second = rank_select.rank_candidates(frame.iloc[::-1], cfg)
    assert first.design_id.tolist() == second.design_id.tolist()
    assert first.cluster_id.tolist() == second.cluster_id.tolist()


def test_descending_author_number_discontinuity_cannot_join_short_segments(tmp_path):
    structure = mock.build_complex_structure("AAAAAA", "AAAA", [1])
    chain = structure[0]["A"]
    for residue, number in zip(chain, [500, 501, 502, 10, 11, 12]):
        residue.id = (" ", number, " ")
    path = mock.write_pdb(structure, tmp_path / "descending.pdb")
    with pytest.raises(ValueError, match="truncation 결과 잔기가 0"):
        truncate.truncate_target(path, "A", ["A:500"], tmp_path / "truncated.pdb", radius=30, min_segment=5)


def test_zero_contact_prediction_remains_complete_specificity_evidence(cfg):
    cfg["specificity"].update(engine="boltz2", context="isolated")
    cfg["run"]["mock"] = False
    rows = []
    sequence = "ACDE"
    for seed in (1, 2, 3):
        for state in ("cleaved", "full"):
            rows.append({"design_id": "candidate", "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
                         "target_state": state, "seed": seed, "engine": "boltz2", "status": "complete", "context": "isolated",
                         "comparison_conditions": "a" * 64, "restraint": "none", "evidence_type": "real",
                         "ipsae": .8 if state == "cleaved" else 0., "iptm": .9 if state == "cleaved" else .1,
                         "interface_plddt": 90. if state == "cleaved" else np.nan,
                         "interface_pae": 3. if state == "cleaved" else np.nan,
                         "n_contacts": 10 if state == "cleaved" else 0,
                         "hotspot_contact_frac": 1. if state == "cleaved" else 0., "n_clashes": 0})
    candidates = pd.DataFrame([{"design_id": "candidate", "sequence": sequence}])
    result = specificity.aggregate(candidates, pd.DataFrame(rows), cfg, [1, 2, 3], expected_conditions="a" * 64).iloc[0]
    assert result.specificity_complete and result.positive_quality_pass
    assert result.delta_ipsae == .8 and pd.isna(result.full_interface_pae)
    failed = pd.DataFrame(rows)
    failed.loc[1, "status"] = "failed"
    assert not specificity.aggregate(candidates, failed, cfg, [1, 2, 3], expected_conditions="a" * 64).iloc[0].specificity_complete


@pytest.mark.parametrize("unassessed", [None, np.nan, "True", 1])
def test_handoff_never_converts_unassessed_eligibility_to_true(tmp_path, cfg, unassessed):
    frame = pd.DataFrame([{"design_id": "candidate", "sequence": "ACDE", "final_eligible": unassessed, "dev_severe": np.nan}])
    handoff.write_synthesis_sheet(frame, cfg, tmp_path)
    exported = pd.read_csv(tmp_path / "synthesis_plate_list.csv")
    assert pd.isna(exported.loc[0, "final_eligible"])
    assert "미평가" in exported.loc[0, "notes"] and "nan" not in exported.loc[0, "notes"]


def test_null_seed_is_reproducible_in_mock_esm(tmp_path, cfg):
    cfg["run"]["seed"] = None
    sequences = {"a": "ACDE"}
    first = developability.run_esm_sequence_scores(sequences, "ACDE", cfg, tmp_path / "one", mock=True)
    cfg["run"]["seed"] = 42
    second = developability.run_esm_sequence_scores(sequences, "ACDE", cfg, tmp_path / "two", mock=True)
    assert first.esm_mean_logprob.tolist() == second.esm_mean_logprob.tolist()


def test_pdockq2_uses_author_contact_weighting_and_recorded_pair_summary():
    structure = mock.build_complex_structure("AA", "AA", [1])
    for residue, point in zip(structure[0]["A"], [(0., 0., 0.), (12., 0., 0.)]):
        residue["CA"].set_coord(np.asarray(point))
    for residue, point in zip(structure[0]["B"], [(3., 0., 0.), (4., 0., 0.)]):
        residue["CA"].set_coord(np.asarray(point))
    confidence = {"A:1": 20., "A:2": 100., "B:1": 60., "B:2": 90.}
    pae = np.ones((4, 4))
    pae[:2, 2:] = [[1, 2], [30, 3]]
    pae[2:, :2] = [[4, 30], [5, 6]]
    _, index_map = score_complex.build_index_map(structure)
    result = score_complex.pdockq2_from_structure(structure, "A", "B", confidence, pae, index_map)
    weighted = [((20+20+100)/3) * np.mean(1 / (1+(np.asarray([1., 2., 3.])/10)**2)),
                ((60+90+90)/3) * np.mean(1 / (1+(np.asarray([4., 5., 6.])/10)**2))]
    values = [1.31034849 / (1+math.exp(-.0747157696*(x-84.7326239)))+.00501886443 for x in weighted]
    assert result["pdockq2_n_contacts"] == 3
    assert result["pdockq2_target"] == round(values[0], 4)
    assert result["pdockq2_binder"] == round(values[1], 4)
    assert result["pdockq2"] == round(min(values), 4)


def test_multisample_default_selection_is_explicitly_recorded(tmp_path, cfg):
    for index in (0, 1):
        mock.make_mock_chai_output(tmp_path, "candidate", "A" * 5, "A" * 5, [1], model_idx=index)
    first = score_complex.score_complex(tmp_path, "A", "B", [1], cfg)
    assert first["prediction_sample_index"] == 0
    assert first["prediction_retained_sample_count"] == 2
    assert first["prediction_sample_selection"] == "first_retained_model"
    assert "샘플 0만" in first["prediction_sample_note"]
    chosen = score_complex.find_prediction_files(tmp_path, model_index=1)
    second = score_complex.score_complex(chosen.cif, "A", "B", [1], cfg, pred=chosen)
    assert second["prediction_sample_index"] == 1
    assert second["prediction_retained_sample_count"] == 2
    assert second["prediction_sample_selection"] == "explicit_model_index"


def test_interface_pae_includes_high_uncertainty_contact_pairs():
    pae = np.asarray([[0., 25.], [30., 0.]])
    assert score_complex.interface_pae(pae, {"A:1": 0, "B:1": 1}, [{"a": "A:1", "b": "B:1"}], cutoff=1.) == 27.5


def test_explicit_file_model_index_cannot_silently_select_another_sample(tmp_path):
    path = mock.make_mock_chai_output(tmp_path, "candidate", "A" * 5, "A" * 5, [], model_idx=0)["cif"]
    with pytest.raises(ValueError, match="모델 번호"):
        score_complex.find_prediction_files(path, model_index=1)
    chosen = score_complex.find_prediction_files(path, model_index=0)
    assert chosen.sample_index == 0
    assert chosen.sample_selection == "explicit_model_index"
    anonymous = mock.write_pdb(mock.build_complex_structure("AA", "AA", []), tmp_path / "structure.pdb")
    with pytest.raises(ValueError, match="확인할 수 없습니다"):
        score_complex.find_prediction_files(anonymous, model_index=0)
    assert score_complex.find_prediction_files(anonymous).sample_index is None


@pytest.mark.parametrize("value", [True, np.bool_(True), -1, .5, 1.0, "1"])
def test_sample_selector_rejects_invalid_direct_types_before_lookup(tmp_path, value):
    with pytest.raises(ValueError, match="0 이상의 정수"):
        score_complex.find_prediction_files(tmp_path, model_index=value)


def test_null_seed_screen_prediction_preserves_fallback_reproducibility(tmp_path, cfg):
    candidates = pd.DataFrame([{"design_id": "candidate", "sequence": "A" * 5}])
    target = {"sequence": "A" * 5, "hotspot_seq_positions": []}
    cfg["run"]["seed"] = None
    screen.predict_complexes(cfg, tmp_path / "null_seed", target, candidates, mock=True)
    cfg["run"]["seed"] = 42
    screen.predict_complexes(cfg, tmp_path / "fallback_seed", target, candidates, mock=True)
    first = tmp_path / "null_seed/candidate/pred.model_idx_0.cif"
    second = tmp_path / "fallback_seed/candidate/pred.model_idx_0.cif"
    assert first.read_bytes() == second.read_bytes()
