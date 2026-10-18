"""Audit 130: actual chain normalization and early, actionable input errors."""
import json

import pytest

from nanocofold import complex as cx
from nanocofold.config import load_config


SEQUENCES = ["QVQLVESGGG", "DIQMTQSPSS"]


def complex_overrides(entries, *settings):
    return ["run.mode=complex", "complex.proteins=" + json.dumps(entries), *settings]


@pytest.mark.parametrize("raw_ids", [("A", "A"), ("", "B"), ("A B", "C")])
def test_raw_chain_ids_are_normalized_before_inference_records(raw_ids, tmp_path, monkeypatch):
    entries = [{"id": chain, "sequence": sequence} for chain, sequence in zip(raw_ids, SEQUENCES)]
    cfg = load_config(overrides=complex_overrides(entries))

    class InputsChecked(Exception):
        pass

    def capture_identity(config, normalized, settings):
        records = {entry["id"]: entry["sequence"] for entry in normalized}
        assert records == dict(zip(["A", "B"], SEQUENCES))
        raise InputsChecked

    monkeypatch.setattr(cx, "_request_identity", capture_identity)
    with pytest.raises(InputsChecked):
        cx.run(cfg, tmp_path / "uncreated-run", None)
    assert not (tmp_path / "uncreated-run").exists()


def test_homomer_copies_keep_distinct_analysis_ids_and_valid_anchor():
    cfg = load_config(overrides=complex_overrides(
        [{"id": "same", "sequence": SEQUENCES[0], "copies": 2}],
        "complex.anchor_chain=B", "complex.primary_pair=[A,B]"))
    normalized = cx.proteins(cfg["complex"]["proteins"])
    assert [(entry["id"], entry["sequence"]) for entry in normalized] == [
        ("A", SEQUENCES[0]), ("B", SEQUENCES[0])]


@pytest.mark.parametrize("field,upper", [
    ("design.num_designs", 10000),
    ("mature.prefilter.top_n", 100000),
    ("rank.final_n", 100000),
])
def test_negative_and_zero_counts_report_the_same_actual_range(field, upper):
    messages = []
    for invalid in [-1, 0]:
        with pytest.raises(ValueError) as caught:
            load_config(overrides=[f"{field}={invalid}"])
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
    assert f"1–{upper}" in messages[0]
    assert "0 이상" not in messages[0]
    for valid in [1, upper]:
        load_config(overrides=[f"{field}={valid}"])
    with pytest.raises(ValueError, match=f"1–{upper}"):
        load_config(overrides=[f"{field}={upper + 1}"])


@pytest.mark.parametrize("anchor", ["Z", "", "A B", None, 2, ["A"]])
def test_complex_config_rejects_invalid_anchor_before_preflight(anchor):
    entries = [{"sequence": sequence} for sequence in SEQUENCES]
    with pytest.raises(ValueError, match="anchor_chain은 실제 분석 체인 ID여야 합니다"):
        load_config(overrides=complex_overrides(entries, "complex.anchor_chain=" + json.dumps(anchor)))


def test_direct_complex_run_rechecks_anchor_before_creating_outputs(tmp_path, monkeypatch):
    root = tmp_path / "uncreated-run"
    cfg = {"complex": {"proteins": [{"sequence": sequence} for sequence in SEQUENCES],
                       "anchor_chain": "Z"}}
    monkeypatch.setattr(cx, "_request_identity", lambda *args: pytest.fail("Invalid input reached inference setup"))
    with pytest.raises(ValueError, match="anchor_chain은 실제 분석 체인 ID여야 합니다"):
        cx.run(cfg, root, None)
    assert not root.exists()


def test_historical_invalid_anchor_remains_readable_but_cannot_be_reexecuted(tmp_path):
    entries = [{"sequence": sequence} for sequence in SEQUENCES]
    cfg = load_config(overrides=complex_overrides(entries, "complex.anchor_chain=Z"), historical=True)
    assert cfg["complex"]["anchor_chain"] == "Z"
    with pytest.raises(ValueError, match="anchor_chain은 실제 분석 체인 ID여야 합니다"):
        cx.run(cfg, tmp_path / "uncreated-run", None)


def test_primary_pair_still_requires_existing_distinct_canonical_ids():
    entries = [{"sequence": sequence} for sequence in SEQUENCES]
    with pytest.raises(ValueError, match="primary_pair가 실제 분석 체인에 없습니다"):
        load_config(overrides=complex_overrides(entries, "complex.primary_pair=[A,C]"))
    with pytest.raises(ValueError) as caught:
        load_config(overrides=complex_overrides(entries, "complex.primary_pair=[A,A]"))
    assert str(caught.value) == "primary_pair는 서로 다른 분석 체인 ID 2개의 목록이어야 합니다."
