"""Reports consume saved numbering and distinguish parent interval mapping."""
import json

import pytest
import yaml

from nanocofold import mock
from nanocofold.platform.results import ResultReader
from nanocofold.platform.structure_review import cdr_annotations
from nanocofold.scripts.anarci_run import compute_cdr_ranges
from nanocofold.utils import pdb_io


WT = "ACCDEFGHIK"
RANGES = {"CDR1": [2, 3], "CDR2": [5, 6], "CDR3": [8, 9]}


def numbered(sequence=WT):
    return {"imgt_positions": [{"index": i, "aa": aa} for i, aa in enumerate(sequence, 1)],
            "cdr_ranges": RANGES, "scheme_supported": True}


def saved_run(tmp_path, sequence=WT, *, batch=False):
    (tmp_path / "run_config.yaml").write_text(yaml.safe_dump({"run": {"mode": "mature"}}))
    (tmp_path / "run_state.json").write_text(json.dumps({"phases": {"mature": {
        "wt_sequence": WT, "cdr_ranges": RANGES}}}))
    log = tmp_path / ("04_developability/anarci_batch.log" if batch else "02_mature/anarci.log")
    log.parent.mkdir(parents=True)
    payload = {"results": {"variant": numbered(sequence)}} if batch else numbered()
    log.write_text("$ anarci\ninvalid line\n[]\n" + json.dumps(payload) + "\n# exit=0\n")
    binder = pdb_io.get_chain(mock.build_complex_structure("ACDEFGHIKL", sequence, [4], seed=7), "B")
    row = {"candidate_id": "opendde__variant", "source_sequence_id": "variant", "sequence": sequence}
    return ResultReader(tmp_path), row, binder, log


@pytest.mark.parametrize("batch", [False, True])
def test_exact_saved_numbering_is_visible_without_changing_run(tmp_path, batch):
    reader, row, binder, log = saved_run(tmp_path, batch=batch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = cdr_annotations(reader, row, binder)
    assert result["source"] == "saved_anarci_numbering"
    assert result["source_path"] == str(log.relative_to(tmp_path))
    assert result["ranges"]["CDR1"]["labels"] == ["B:2", "B:3"]
    assert all(p.read_bytes() == data for p, data in before.items())


def test_mutant_maps_parent_intervals_only_with_exact_selected_sequence(tmp_path):
    sequence = "ACCDAFGHIK"
    reader, row, binder, _ = saved_run(tmp_path, sequence)
    selected = tmp_path / "02_mature/prefiltered_candidates.csv"
    selected.write_text(f"variant_id,sequence\nvariant,{sequence}\n")
    before = selected.read_bytes()
    result = cdr_annotations(reader, row, binder)
    assert result["source"] == "saved_parent_cdr_substitution_ranges"
    assert set(result["ranges"]) == set(RANGES)
    assert "WT" in result["note"]
    assert selected.read_bytes() == before
    selected.write_text(f"variant_id,sequence\nvariant,{WT}\n")
    assert not cdr_annotations(reader, row, binder)["ranges"]


@pytest.mark.parametrize("sequence", ["VCCDEFGHIK", "ACCDEFGHIKL"])
def test_framework_changes_and_length_changes_cannot_inherit_parent_ranges(tmp_path, sequence):
    reader, row, binder, _ = saved_run(tmp_path, sequence)
    (tmp_path / "02_mature/prefiltered_candidates.csv").write_text(f"variant_id,sequence\nvariant,{sequence}\n")
    assert not cdr_annotations(reader, row, binder)["ranges"]


@pytest.mark.parametrize("damage", ["wrong_sequence", "wrong_indices", "boolean_index", "bad_positions",
                                     "unsupported_scheme", "invalid_entry", "invalid_results"])
def test_corrupt_batch_numbering_stays_unavailable(tmp_path, damage):
    reader, row, binder, log = saved_run(tmp_path, batch=True)
    entry = numbered()
    if damage == "wrong_sequence":
        entry["imgt_positions"][0]["aa"] = "V"
    elif damage == "wrong_indices":
        entry["imgt_positions"][0]["index"] = 2
    elif damage == "boolean_index":
        entry["imgt_positions"][0]["index"] = True
    elif damage == "bad_positions":
        entry["imgt_positions"] = [None]
    elif damage == "unsupported_scheme":
        entry["scheme_supported"] = False
    elif damage == "invalid_entry":
        entry = []
    payload = {"results": {"variant": entry}}
    if damage == "invalid_results":
        payload["results"] = []
    log.write_text(json.dumps(payload))
    assert not cdr_annotations(reader, row, binder)["ranges"]


def test_unrecorded_candidate_and_changed_parent_receipt_are_rejected(tmp_path):
    sequence = "ACCDAFGHIK"
    reader, row, binder, _ = saved_run(tmp_path, sequence)
    assert not cdr_annotations(reader, row, binder)["ranges"]
    (tmp_path / "02_mature/prefiltered_candidates.csv").write_text(f"variant_id,sequence\nother,{sequence}\n")
    assert not cdr_annotations(reader, row, binder)["ranges"]
    (tmp_path / "02_mature/prefiltered_candidates.csv").write_text(f"variant_id,sequence\nvariant,{sequence}\n")
    reader.state["phases"]["mature"]["cdr_ranges"] = {"CDR3": [8, 9]}
    assert not cdr_annotations(reader, row, binder)["ranges"]


TAGGED = "MA" + WT + "HHHHHH"


def tagged_numbering():
    imgt = [1, 27, 28, 50, 56, 57, 100, 105, 106, 128]
    numbering = [((number, " "), aa) for number, aa in zip(imgt, WT)]
    return compute_cdr_ranges(numbering, source_sequence=TAGGED)


def test_saved_tagged_domain_uses_full_sequence_positions_without_shifting_again(tmp_path):
    reader, row, binder, log = saved_run(tmp_path, TAGGED, batch=True)
    entry = tagged_numbering()
    log.write_text(json.dumps({"results": {"variant": entry}}))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = cdr_annotations(reader, row, binder)
    assert result["source"] == "saved_anarci_numbering"
    assert result["ranges"]["CDR1"]["labels"] == ["B:4", "B:5"]
    assert result["ranges"]["CDR3"]["labels"] == ["B:10", "B:11"]
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("damage", ["duplicate", "outside", "wrong_aa", "missing_cdr", "missing_start",
    "missing_end", "missing_both", "boolean_start", "wrong_end", "missing_index", "wrong_imgt_range", "overlap"])
def test_unverified_tagged_domains_remain_unavailable(tmp_path, damage):
    reader, row, binder, log = saved_run(tmp_path, TAGGED, batch=True)
    entry = tagged_numbering()
    if damage == "duplicate":
        entry["imgt_positions"][1]["index"] = entry["imgt_positions"][0]["index"]
    elif damage == "outside":
        entry["imgt_positions"][-1]["index"] = len(TAGGED) + 1
    elif damage == "wrong_aa":
        entry["imgt_positions"][0]["aa"] = "V"
    elif damage == "missing_cdr":
        del entry["cdr_ranges"]["CDR2"]
    elif damage in {"missing_start", "missing_both"}:
        del entry["domain_start"]
        if damage == "missing_both":
            del entry["domain_end"]
    elif damage == "missing_end":
        del entry["domain_end"]
    elif damage == "boolean_start":
        entry["domain_start"] = True
    elif damage == "wrong_end":
        entry["domain_end"] -= 1
    elif damage == "missing_index":
        del entry["imgt_positions"][3]
    elif damage == "wrong_imgt_range":
        entry["cdr_ranges"]["CDR1"] = [5, 6]
    elif damage == "overlap":
        entry["cdr_ranges"]["CDR2"] = entry["cdr_ranges"]["CDR1"]
    log.write_text(json.dumps({"results": {"variant": entry}}))
    assert not cdr_annotations(reader, row, binder)["ranges"]


def test_repeated_domain_receipt_cannot_map_to_ambiguous_full_sequence(tmp_path):
    sequence = "MA" + WT + WT + "HHHHHH"
    reader, row, binder, log = saved_run(tmp_path, sequence, batch=True)
    log.write_text(json.dumps({"results": {"variant": tagged_numbering()}}))
    assert not cdr_annotations(reader, row, binder)["ranges"]
