"""Specificity must execute the declared generation policy, CPU fixtures only."""
from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from nanocofold import campaign, design_boltzgen, design_rfantibody, mature
from nanocofold.config import load_config
from nanocofold.utils.shell import ExecutionCancelled


WT = "ACDEFGHIKLMNPQRSTVWY"
CDRS = {"CDR1": [2, 3], "CDR2": [5, 6], "CDR3": [9, 10]}


def options():
    cfg = load_config()
    cfg["run"].update(mode="mature", mock=False)
    cfg["specificity"]["enabled"] = True
    cfg["mature"].update(wt_sequence=WT, wt_fasta=None)
    cfg["mature"]["cdr"].update(source="config", regions=copy.deepcopy(CDRS))
    # Disabled prefilter and explicit CDRs avoid every native model invocation.
    cfg["mature"]["prefilter"]["mode"] = "none"
    return cfg


def library(cfg, directory):
    directory.mkdir()
    return campaign._candidate_library(cfg, directory, {})


@pytest.mark.parametrize("limit", [1, 3])
def test_specificity_mature_applies_dms_variant_bound_and_preserves_wt(tmp_path, limit):
    cfg = options()
    cfg["mature"]["library"].update(strategy="dms", max_variants=limit)
    before = copy.deepcopy(cfg)
    expected = mature.generate_library(WT, CDRS, cfg).rename(columns={"variant_id": "design_id"})
    actual = library(cfg, tmp_path / "bounded")
    assert len(actual) == limit + 1
    assert dict(zip(actual.design_id, actual.sequence)) == dict(zip(expected.design_id, expected.sequence))
    assert actual.loc[actual.design_id.eq("WT"), "sequence"].tolist() == [WT]
    assert cfg == before


def test_specificity_mature_random_policy_matches_seeded_two_mutation_library(tmp_path):
    cfg = options()
    cfg["mature"]["library"].update(strategy="random", size=5, min_mutations=2, max_mutations=2, seed=13)
    before = copy.deepcopy(cfg)
    expected = mature.generate_library(WT, CDRS, cfg).rename(columns={"variant_id": "design_id"})
    actual = library(cfg, tmp_path / "random")
    assert len(actual) == 5
    assert dict(zip(actual.design_id, actual.sequence)) == dict(zip(expected.design_id, expected.sequence))
    assert actual.mutation_count.tolist() == [0, 2, 2, 2, 2]
    assert actual.library_strategy.eq("random").all()
    assert actual.library_seed.eq(13).all()
    for row in actual.itertuples():
        assert sum(a != b for a, b in zip(WT, row.sequence)) == (0 if row.design_id == "WT" else 2)
    repeated = library(cfg, tmp_path / "same_seed")
    pd.testing.assert_frame_equal(actual, repeated)
    cfg["mature"]["library"]["seed"] = 14
    changed = library(cfg, tmp_path / "different_seed")
    assert actual.sequence.tolist() != changed.sequence.tolist()
    cfg["mature"]["library"]["seed"] = 13
    assert cfg == before


def test_specificity_mature_default_dms_keeps_existing_library(tmp_path):
    cfg = options()
    expected = mature.generate_dms_library(WT, CDRS).rename(columns={"variant_id": "design_id"})
    actual = library(cfg, tmp_path / "default")
    assert dict(zip(actual.design_id, actual.sequence)) == dict(zip(expected.design_id, expected.sequence))
    assert actual.dev_cdr_evaluated.all()
    assert not actual.design_id.duplicated().any()


def _backend(engine, calls):
    def run(cfg, run_dir, target, mock=False, dry_run=False):
        calls.append({"engine": engine, "declared_engine": cfg["design"]["engine"],
                      "num_designs": cfg["design"]["num_designs"],
                      "seqs_per_struct": cfg["design"]["mpnn"]["seqs_per_struct"],
                      "mock": mock, "dry_run": dry_run})
        # Same source ID across engines exercises shared dispatch namespacing.
        sequence = WT if engine == "rfantibody" else "Y" + WT[1:]
        frame = pd.DataFrame([{"design_id": "fixture", "sequence": sequence, "source": engine}])
        return {"candidates": frame, "all_candidates": frame.copy(),
                "info": {"engine": engine, "fixture_only": True},
                "phase_dir": run_dir / "02_design"}
    return run


@pytest.mark.parametrize("engine", ["rfantibody", "boltzgen", "both"])
@pytest.mark.parametrize("requested,seqs,expected_count,expected_seqs", [(1000, 4, 128, 2), (2, 1, 2, 1)])
def test_specificity_design_uses_declared_backends_and_campaign_caps(
    tmp_path, monkeypatch, engine, requested, seqs, expected_count, expected_seqs,
):
    cfg = options()
    cfg["run"]["mode"] = "design"
    cfg["design"].update(engine=engine, num_designs=requested)
    cfg["design"]["mpnn"]["seqs_per_struct"] = seqs
    before = copy.deepcopy(cfg)
    calls = []
    monkeypatch.setattr(design_rfantibody, "run_design", _backend("rfantibody", calls))
    monkeypatch.setattr(design_boltzgen, "run_design", _backend("boltzgen", calls))
    root = tmp_path / "design"
    actual = library(cfg, root)
    expected_engines = ["rfantibody", "boltzgen"] if engine == "both" else [engine]
    assert [call["engine"] for call in calls] == expected_engines
    assert [call["declared_engine"] for call in calls] == expected_engines
    assert all(call["num_designs"] == expected_count and call["seqs_per_struct"] == expected_seqs for call in calls)
    assert all(call["mock"] is False and call["dry_run"] is False for call in calls)
    assert actual.parent_engine.tolist() == expected_engines
    assert actual.source.tolist() == expected_engines
    assert actual.initial_design_id.eq("fixture").all()
    assert actual.design_id.tolist() == ([engine + "__fixture" for engine in expected_engines]
                                        if engine == "both" else ["fixture"])
    generation = pd.read_csv(root / "02_design/all_generated_candidates.csv")
    assert generation.parent_engine.tolist() == expected_engines
    assert set(json.loads((root / "02_design/engine_info.json").read_text())) == set(expected_engines)
    assert cfg == before


def test_specificity_shared_design_cancellation_preserves_first_backend_records(tmp_path, monkeypatch):
    cfg = options()
    cfg["run"]["mode"] = "design"
    cfg["design"]["engine"] = "both"
    calls = []
    monkeypatch.setattr(design_rfantibody, "run_design", _backend("rfantibody", calls))
    def cancelled(*_args, **_kwargs):
        raise ExecutionCancelled("synthetic cancellation")
    monkeypatch.setattr(design_boltzgen, "run_design", cancelled)
    root = tmp_path / "cancelled"
    root.mkdir()
    original = root / "existing-result.bin"
    original.write_bytes(b"preserve original results")
    with pytest.raises(ExecutionCancelled, match="synthetic cancellation"):
        campaign._candidate_library(cfg, root, {})
    assert original.read_bytes() == b"preserve original results"
    records = pd.read_csv(root / "02_design/all_generated_candidates.csv")
    assert records.design_id.tolist() == ["rfantibody__fixture"]
    assert records.parent_engine.tolist() == ["rfantibody"]
    status = json.loads((root / "02_design/engine_info.json").read_text())
    assert status["boltzgen"]["status"] == "interrupted"
    assert status["boltzgen"]["error_type"] == "ExecutionCancelled"
