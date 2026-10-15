"""CDR schema validation must distinguish absent assessments from invalid bounds."""
from __future__ import annotations

import pandas as pd
import pytest

from nanocofold import cli, mature, pose_integration
from nanocofold.config import load_config
from nanocofold.platform.inputs import InputManager
from nanocofold.state import RunState


def _finalize(tmp_path, monkeypatch, regions, *, rows=0):
    cfg = load_config()
    cfg['run'].update(mode='screen', mock=True)
    cfg['mature']['cdr'].update(source='config', regions=regions)
    cfg['rank'].update(weights={'dev_flags': -.4}, fallback_to_ineligible=True)
    names = [f'candidate_{i}' for i in range(rows)]
    candidates = pd.DataFrame({'design_id': names, 'sequence': ['Q' * 20] * rows})
    metrics = pd.DataFrame({'design_id': names, **{key: True for key in ('pass_clash', 'pass_ipsae', 'pass_iptm', 'pass_plddt')}})
    monkeypatch.setattr(cli, '_score_candidates', lambda *a, **kw: metrics)
    monkeypatch.setattr(cli, '_run_sequence_scores', lambda *a, **kw: pd.DataFrame({'design_id': names}))
    monkeypatch.setattr(cli, '_final_exports', lambda *a, **kw: {'synthesis': None, 'pymol_script': None, 'handoff_gate': {}, 'gate_blocks': False})
    monkeypatch.setattr(pose_integration, 'annotate_frame', lambda frame, *a: (frame, {}))
    state = RunState(tmp_path)
    result = cli._finalize(cfg, tmp_path, state, candidates, {}, None, publish_report=False)
    return result, state


def test_empty_post_filter_pool_has_no_sequence_bound_or_invented_assessment(tmp_path, monkeypatch):
    regions = {'CDR1': [27, 27], 'CDR2': [56, 56], 'CDR3': [105, 105]}
    result, state = _finalize(tmp_path, monkeypatch, regions)
    assert result['scored'].empty and result['selected'].empty
    assert state.get_phase('final')['tables_committed'] is True
    assert state.get_phase('final')['n_selected'] == 0
    assert pd.read_csv(tmp_path / '03_score/scored_candidates.csv').empty
    assert pd.read_csv(tmp_path / '05_final/selected_candidates.csv').empty


@pytest.mark.parametrize('regions', [
    {'CDR1': [1.5, 2.]}, {'CDR1': ['1', '2']}, {'CDR1': [True, 2]},
    {'CDR1': [3, 2]}, {'CDR1': [0, 1]}, {'CDR1': [1, 3], 'CDR2': [3, 4]},
    {'wrong_name': [1, 2]}, [], False, 0,
])
def test_empty_pool_still_rejects_malformed_supplied_cdr_coordinates(tmp_path, monkeypatch, regions):
    with pytest.raises(ValueError, match='CDR'):
        _finalize(tmp_path, monkeypatch, regions)


def test_nonempty_pool_keeps_actual_sequence_upper_bound(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='서열 범위'):
        _finalize(tmp_path, monkeypatch, {'CDR3': [105, 105]}, rows=1)


@pytest.mark.parametrize('regions', [{}, None])
def test_absent_generic_annotation_is_preserved_as_unassessed_and_held(tmp_path, monkeypatch, regions):
    result, state = _finalize(tmp_path, monkeypatch, regions, rows=2)
    assert not result['scored']['dev_cdr_evaluated'].any()
    assert result['scored']['composite_score'].isna().all()
    assert result['scored']['rank'].isna().all()
    assert result['selected'].empty  # fallback cannot invent a safe CDR result
    assert any('CDR 범위가 없어' in notice for notice in state.warnings)


def test_input_review_records_missing_cdr_warning_without_discarding_input(tmp_path):
    manager = InputManager(tmp_path)
    try:
        result = manager.fasta({'content': '>target\nACDEFGHIKLMNPQRSTVWY', 'mode': 'screen',
                               'mock': False, 'vhh_fasta': '>VHH\nQQQQQQQQQQQQQQQQQQQQ',
                               'config': {'mature': {'cdr': {'source': 'config', 'regions': {}}}}})
        assert any('최종 선별을 보류' in text for text in result['review']['warnings'])
        assert result['config']['rank']['weights']['dev_flags'] != 0
        manager.reviewed(result['input_id'])
    finally:
        manager.close()


def test_mature_wt_still_requires_annotated_ranges():
    cfg = load_config()
    cfg['mature']['cdr'].update(source='config', regions={})
    with pytest.raises(ValueError, match='CDR'):
        mature.resolve_cdr_ranges(cfg, 'Q' * 20)


def test_schema_only_mode_keeps_overlap_gate_without_materializing_positions():
    # There is no sequence or assessment in this mode. Interval validation
    # must not allocate a set proportional to user-supplied coordinate values.
    assert mature.validate_cdr_ranges({'CDR1': [1, 10**12]}, None) == {'CDR1': (1, 10**12)}
    with pytest.raises(ValueError, match='겹칠'):
        mature.validate_cdr_ranges({'CDR1': [1, 10**12], 'CDR2': [10**12, 10**12 + 1]}, None)
