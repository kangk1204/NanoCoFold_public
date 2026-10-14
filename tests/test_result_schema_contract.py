import copy

import pytest

from nanocofold.result_schema import model_observation_record, structure_payload


@pytest.mark.parametrize("mode,scope", [("complex", "whole_complex"), ("screen", "selected_target_binder_pair"),
                                      ("mature", "selected_target_binder_pair"), ("design", "selected_target_binder_pair")])
def test_common_contract_preserves_recorded_values_and_raw_bytes(mode, scope):
    record = {"candidate_id": "나노바디(A);O'Neil", "engine": "opendde", "evaluated": True,
              "error": None, "iptm": 0.0, "ipsae": 0.0, "iptm_scope": scope,
              "ipsae_scope": "selected_chain_pair", "selected": False, "final_eligible": False,
              "native_extension": {"unrounded": 0.123456789123}}
    coordinate_source = {"raw_preserved": True, "raw_sha256": "a" * 64,
                         "verification": {"status": "verified", "basis": "recorded_native_sha256"}}
    payload = {"content": "ATOM\r\nTER\r\n", "format": "pdb", "raw_content": "ATOM\r\nTER\r\n",
               "raw_format": "pdb", "coordinate_source": coordinate_source,
               "pae": {"values": [[0.0, 12.3456789], [4.5678901, 0.0]], "labels": ["A:1", "B:1"]}}
    before = copy.deepcopy((record, payload))
    row = model_observation_record(record, mode=mode)
    view = structure_payload(payload, observation=row, mode=mode)
    assert view["iptm_scope"] == row["iptm_scope"] == scope
    assert view["ipsae_scope"] == row["ipsae_scope"] == "selected_chain_pair"
    assert view["evaluated"] is True and row["selected"] is False
    assert row["iptm"] == row["ipsae"] == 0.0
    assert view["content"] == view["raw_content"] == "ATOM\r\nTER\r\n"
    assert view["pae"] == before[1]["pae"] and view["coordinate_source"] == coordinate_source
    assert row["native_extension"] == before[0]["native_extension"]
    assert (record, payload) == before


def test_missing_metadata_cannot_become_success_or_native_origin():
    row = model_observation_record({"candidate_id": "legacy"}, mode="complex")
    payload = structure_payload({"content": "ATOM\n", "format": "pdb"}, observation=row, mode="complex")
    assert payload["evaluated"] is row["evaluated"] is None
    assert payload["iptm_scope"] is row["iptm_scope"] is None
    assert payload["coordinate_source"]["raw_preserved"] is False
    assert payload["coordinate_source"]["verification"]["status"] == "unavailable"
    assert payload["raw_content"] is None


def test_failed_observation_and_unassessed_scope_are_preserved():
    row = model_observation_record({"evaluated": False, "error": "native_failed", "iptm_scope": None}, mode="screen")
    payload = structure_payload({"evaluated": None}, observation=row, mode="screen")
    assert payload["evaluated"] is False and payload["error"] == "native_failed"
    assert payload["iptm_scope"] is None
