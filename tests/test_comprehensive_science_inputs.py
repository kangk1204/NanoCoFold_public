"""CPU-only regressions for confirmed 2026-10-16 review findings."""
from __future__ import annotations

import ast
import csv
import inspect
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from Bio.PDB.Atom import Atom
from Bio.PDB.Residue import Residue

from nanocofold import cli, hotspots, mature, mock, pose_assessment, rank_select, score_complex, truncate
from nanocofold.config import load_config
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.regions import apply_regions
from nanocofold.state import RunState
from nanocofold.utils import pdb_io


def _finalize(tmp_path, monkeypatch, values, *, regions=None):
    cfg = load_config()
    cfg['run'].update(mode='screen', mock=True)
    cfg['mature']['cdr'].update(source='config', regions=regions or {'CDR1': [1, 2], 'CDR2': [3, 4], 'CDR3': [5, 6]})
    cfg['rank'].update(weights={'esm_score': 1.0}, require_hotspot=False, n_final=10, max_dev_severe=100)
    cfg['rank']['cdr3'].update(per_cluster=10)
    names = [f'candidate_{i}' for i in range(len(values))]
    candidates = pd.DataFrame({'design_id': names, 'sequence': ['Q' * 20] * len(values)})
    metrics = pd.DataFrame({'design_id': names, **{key: True for key in ('pass_clash', 'pass_ipsae', 'pass_iptm', 'pass_plddt')}})
    monkeypatch.setattr(cli, '_score_candidates', lambda *a, **kw: metrics)
    monkeypatch.setattr(cli, '_run_sequence_scores', lambda *a, **kw: pd.DataFrame({'design_id': names, 'esm_mean_logprob': values}))
    monkeypatch.setattr(cli, '_final_exports', lambda *a, **kw: {'synthesis': None, 'pymol_script': None, 'handoff_gate': {}, 'gate_blocks': False})
    from nanocofold import pose_integration
    monkeypatch.setattr(pose_integration, 'annotate_frame', lambda frame, *a: (frame, {}))
    root = tmp_path / 'run'
    root.mkdir()
    return cli._finalize(cfg, root, RunState(root), candidates, {}, None, publish_report=False)


@pytest.mark.parametrize('values', [[-2., -3., np.nan], [-2., np.nan], [-2., -2., np.inf, -np.inf]])
def test_finalize_never_imputes_missing_esm(tmp_path, monkeypatch, values):
    result = _finalize(tmp_path, monkeypatch, values)
    scored = result['scored'].set_index('design_id')
    selected = set(result['selected']['design_id'])
    for i, value in enumerate(values):
        row = scored.loc[f'candidate_{i}']
        if not math.isfinite(value):
            assert pd.isna(row['esm_score'])
            assert pd.isna(row['rank'])
            assert row['ranking_unavailable_reason'] == 'weighted_metrics_unavailable'
            assert f'candidate_{i}' not in selected
        else:
            assert math.isfinite(row['esm_score'])
            assert pd.notna(row['rank'])


@pytest.mark.parametrize('bounds', [[1.5, 2.9], ['1', '2'], [True, 2], [3, 2]])
def test_finalize_uses_same_strict_cdr_coordinates_as_mature(tmp_path, monkeypatch, bounds):
    regions = {'CDR1': bounds}
    with pytest.raises(ValueError, match='CDR'):
        mature.validate_cdr_ranges(regions, 20)
    with pytest.raises(ValueError, match='CDR'):
        _finalize(tmp_path, monkeypatch, [-2.], regions=regions)


def test_empty_shared_ca_matches_have_array_contract(tmp_path):
    a = pdb_io.load_structure(mock.make_mock_target_only('AA', tmp_path / 'a.pdb'))
    b = pdb_io.load_structure(mock.make_mock_target_only('AA', tmp_path / 'b.pdb'))
    left, right, labels = pdb_io.shared_ca_coords(a, b, 'A', ['A:999'])
    assert left.shape == right.shape == (0, 3)
    assert labels == []


def test_binder_without_confidence_never_takes_empty_mean(tmp_path):
    structure = mock.build_complex_structure('AAAA', 'AAAA', [2])
    path = mock.write_pdb(structure, tmp_path / 'model.pdb')
    pred = score_complex.find_prediction_files(path)
    context = score_complex.prepare_scoring_context(pred)
    context.plddt = {'A:1': 90.}
    with warnings.catch_warnings():
        warnings.simplefilter('error', RuntimeWarning)
        result = score_complex.score_complex(path, 'A', 'B', [], load_config(), pred=pred, context=context)
    assert math.isnan(result['binder_plddt'])


@pytest.mark.parametrize('bounds', [[6, 3], [0, 2], [1], ['1', 2], [1.5, 3], [True, 3]])
def test_hotspot_size_range_invalid_even_for_empty_table(bounds):
    with pytest.raises(ValueError, match='residues_per_set'):
        hotspots.auto_hotspot_sets(pd.DataFrame(), {'residues_per_set': bounds}, 'not-read.pdb', 'A')


def test_plddt_nan_cannot_change_normalization_by_residue_order(tmp_path):
    structure = pdb_io.load_structure(mock.make_mock_target_only('AA', tmp_path / 'confidence.pdb'))
    residues = list(pdb_io.iter_residues(structure[0]['A']))
    for residue, value in zip(residues, [.9, float('nan')]):
        for atom in residue:
            atom.set_bfactor(value)
    first = pdb_io.parse_plddt(structure)
    for residue, value in zip(residues, [float('nan'), .9]):
        for atom in residue:
            atom.set_bfactor(value)
    second = pdb_io.parse_plddt(structure)
    assert first['A:1'] == second['A:2'] == .9


def test_heavy_atoms_exclude_deuterium_and_preserve_aligned_mercury():
    residue = Residue((' ', 1, ' '), 'ALA', '')
    for serial, (name, fullname, element) in enumerate([('D', ' D  ', 'D'), ('HG', 'HG  ', 'HG'), ('H', ' H  ', 'H')]):
        atom = Atom(name, np.zeros(3), 1., 1., ' ', fullname, serial, element=element)
        if name == 'HG':
            atom.element = ''
        residue.add(atom)
    assert [a.name for a in pdb_io.iter_heavy_atoms(residue)] == ['HG']


def test_glycan_boundary_uses_full_distance(tmp_path, monkeypatch):
    path = mock.make_mock_target_only('AAA', tmp_path / 'target.pdb')
    monkeypatch.setattr(hotspots, '_glycan_atoms', lambda structure: np.zeros((1, 3)))
    monkeypatch.setattr(hotspots, '_min_distance_to_atoms', lambda *a: 7.99)
    table = hotspots.hotspot_table(path, 'A')
    assert table['dist_to_glycan'].tolist() == [7.99] * 3
    result = hotspots.validate_hotspots(path, 'A', ['A:1'])
    assert any('글리칸' in value for value in result['warnings'])
    table['relative_sasa'] = .9
    sets = hotspots.auto_hotspot_sets(table, {'residues_per_set': [1, 3], 'exclude_terminal': 0}, path, 'A')
    assert sets and sets[0]['glycan_risk'] is True


def test_unanchored_cdr_guess_does_not_span_framework_but_explicit_long_loop_survives():
    oversized = 'A' * 77
    sequence = 'QVQLC' + oversized + 'WGQGTVSS'
    assert rank_select.guess_cdr3(sequence) != oversized
    # The heuristic limit is not a biological length gate.
    frame = pd.DataFrame({'sequence': [sequence, 'QVQLC' + 'G' * 77 + 'WGQGTVSS'],
                          'cdr3_sequence': [oversized, 'G' * 77]})
    assert rank_select.cluster_cdr3(frame).nunique() == 2
    assert rank_select.guess_cdr3('QVQLC' + 'A' * 40 + 'WGQGTVSS') == 'A' * 40


def _complex_payload(**changes):
    return {'mock': True, 'proteins': [{'name': 'one', 'sequence': 'ACDEFGHIK'}, {'name': 'two', 'sequence': 'LMNPQRSTV'}], **changes}


@pytest.mark.parametrize('changes', [{'anchor_chain': 'Z'}, {'interest_positions': [0]}, {'interest_positions': [2.5]}, {'interest_positions': [True]}, {'interest_positions': [2, 2]}, {'interest_positions': [None]}, {'interest_positions': '2'}])
def test_complex_review_rejects_invalid_reference_coordinates_before_jobs(tmp_path, changes):
    manager = InputManager(tmp_path)
    try:
        with pytest.raises(ValueError, match='anchor_chain|interest_positions'):
            manager.complex(_complex_payload(**changes))
    finally:
        manager.close()


def test_complex_review_warns_about_possible_dna_without_modifying_sequence(tmp_path):
    manager = InputManager(tmp_path)
    try:
        sequence = 'ACGT' * 15
        payload = _complex_payload()
        payload['proteins'][0]['sequence'] = sequence
        result = manager.complex(payload)
        assert result['config']['complex']['proteins'][0]['sequence'] == sequence
        assert any('DNA' in value for value in result['review']['warnings'])
    finally:
        manager.close()


def test_cdr_preflight_preserves_conservative_hold_and_explains_action(tmp_path):
    manager = InputManager(tmp_path)
    try:
        result = manager.fasta({'content': '>target\nACDEFGHIKLMNPQRSTVWY', 'mode': 'screen', 'mock': True})
        warnings_ = result['review']['warnings']
        assert any('ANARCI' in text and '최종 선별을 보류' in text for text in warnings_)
        assert result['config']['rank']['weights']['dev_flags'] != 0
    finally:
        manager.close()


def test_region_csv_escapes_formula_without_changing_original(tmp_path):
    original = tmp_path / 'original.txt'
    original.write_text('=malicious()')
    folder = tmp_path / 'review'
    folder.mkdir()
    path = folder / 'residue_mapping.csv'
    with path.open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=['protein', 'output_chain', 'output_position'])
        writer.writeheader()
        writer.writerow({'protein': '=malicious()', 'output_chain': 'A', 'output_position': 1})
    cfg = {'target': {'name': 'target', 'sequence': 'ACD', 'hotspots': [], 'aux_chains': []}, 'structure': {'provider': 'mock'}}
    apply_regions(folder, cfg, {}, {})
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
    assert rows[0]['protein'] == "'=malicious()"
    assert original.read_text() == '=malicious()'


def test_cli_help_is_english_and_wizard_has_distinct_port():
    parser = cli.build_parser()
    texts = [parser.description]
    actions = parser._subparsers._group_actions[0].choices
    for sub in actions.values():
        texts.extend(action.help or '' for action in sub._actions)
    assert not any('\uac00' <= char <= '\ud7a3' for text in texts for char in text)
    assert parser.parse_args(['wizard']).port == 8766
    assert parser.parse_args(['serve']).port == 8765


def test_priority_tiers_include_every_returned_priority():
    module = ast.parse(inspect.getsource(pose_assessment))
    helper = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == '_priority')
    values = {node.value.value for node in ast.walk(helper) if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)}
    tier_values = next(ast.literal_eval(node.value) for node in ast.walk(module) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'tiers' for target in node.targets))
    assert values <= set(tier_values)


def test_truncation_missing_hotspot_guard_is_independently_reached(tmp_path):
    structure = mock.build_complex_structure('A' * 6, 'AA', [1])
    # Two far-separated sites: a valid two-residue segment and one isolated
    # hotspot that is removed by the minimum-segment rule.
    for i, residue in enumerate(structure[0]['A'], 1):
        for atom in residue:
            atom.set_coord(atom.coord - residue['CA'].coord + np.array([3. * i if i <= 2 else 1000. * i, 0., 0.]))
    path = mock.write_pdb(structure, tmp_path / 'target.pdb')
    with pytest.raises(ValueError, match='결합 부위가 사라졌습니다'):
        truncate.truncate_target(path, 'A', ['A:1', 'A:6'], tmp_path / 'out.pdb', radius=4., min_segment=2)
    assert not (tmp_path / 'out.pdb').exists()


def test_rf2_cutoff_is_independent_of_equal_score_input_order():
    from nanocofold.design_rfantibody import _top_scoring_designs
    rows = pd.DataFrame({'design_id': ['z', 'a', 'm'], 'rf2_pae': [2., 2., 2.]})
    assert list(_top_scoring_designs(rows, 2)['design_id']) == ['a', 'm']
    assert list(_top_scoring_designs(rows.iloc[::-1], 2)['design_id']) == ['a', 'm']


def _ensemble(tmp_path):
    structure = mock.build_complex_structure('ACDEFGHIK', 'LMNPQRSTV', [2])
    first = structure[0]
    second = first.copy()
    second.id = 1
    second.serial_num = 2
    for atom in second.get_atoms():
        atom.set_coord(atom.coord + np.array([100., 0., 0.]))
    structure.add(second)
    return mock.write_pdb(structure, tmp_path / 'ensemble.pdb')


def test_complex_reference_explicit_model_preserves_author_coordinates_and_original(tmp_path):
    source = _ensemble(tmp_path)
    original = source.read_text()
    manager = InputManager(tmp_path / 'workspace')
    try:
        result = manager.complex(_complex_payload(reference={'filename': 'ensemble.pdb', 'content': original, 'model_index': 2}))
        root = Path(result['config_path']).parent
        assert (root / 'reference.pdb').read_text() == original
        selected = pdb_io.load_structure(result['config']['complex']['reference_path'])
        raw = pdb_io.load_structure(source)
        assert len(list(selected.get_models())) == 1
        assert pdb_io.chain_ids(selected) == ['A', 'B']
        assert selected[0]['A'][1]['CA'].coord == pytest.approx(raw[1]['A'][1]['CA'].coord)
        assert result['config']['complex']['reference_model_selection']['model_index'] == 2
        assert result['config']['complex']['reference_model_selection']['model_count'] == 2
        manager.reviewed(result['input_id'])
    finally:
        manager.close()


def test_wt_reference_explicit_model_is_reviewed_without_mutating_upload(tmp_path):
    source = _ensemble(tmp_path)
    original = source.read_text()
    manager = InputManager(tmp_path / 'workspace')
    try:
        result = manager.fasta({'content': '>target\nACDEFGHIKLMNPQRSTVWY', 'mode': 'mature', 'mock': True,
                               'wt_fasta': '>WT\nACDEFGHIK',
                               'wt_structure': {'filename': 'ensemble.pdb', 'content': original, 'model_index': 2}})
        root = Path(result['config_path']).parent
        assert (root / 'wt_source.pdb').read_text() == original
        selection = result['config']['mature']['wt_structure_review']['model_selection']
        assert selection['model_index'] == 2 and selection['model_count'] == 2
        selected = pdb_io.load_structure(result['config']['mature']['wt_structure'])
        assert selected[0]['A'][1]['CA'].coord[0] > 90
        manager.reviewed(result['input_id'])
    finally:
        manager.close()


def test_interface_average_is_stable_across_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys
    path = mock.write_pdb(mock.build_complex_structure('AAAA', 'AAAA', [2], pull_closer=3.0), tmp_path / 'complex.pdb')
    program = '''
import json, sys
from nanocofold import score_complex
from nanocofold.config import load_config
pred = score_complex.find_prediction_files(sys.argv[1])
context = score_complex.prepare_scoring_context(pred)
for i, label in enumerate(sorted(context.labels)):
    context.plddt[label] = 70.0 + (i - 3.5) * 1e-10
value = score_complex.score_complex(sys.argv[1], 'A', 'B', [], load_config(), pred=pred, context=context)['interface_plddt']
print(json.dumps(value))
'''
    outputs = [subprocess.check_output([sys.executable, '-c', program, str(path)],
               env={**os.environ, 'PYTHONHASHSEED': str(seed), 'CUDA_VISIBLE_DEVICES': ''}, text=True).strip()
               for seed in (0, 1, 32)]
    assert len(set(outputs)) == 1
    assert math.isfinite(json.loads(outputs[0]))


def test_license_audit_csv_treats_metadata_as_text(tmp_path, monkeypatch):
    from types import SimpleNamespace
    python = tmp_path / 'envs/example/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    def fake_run(argv, **kwargs):
        kwargs['log_path'].write_text('command\nstatus\nName,License\n=untrusted(),MIT\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.shell, 'run', fake_run)
    assert cli._audit_licenses() == 0
    rows = list(csv.DictReader((tmp_path / 'license_audit.csv').open()))
    assert rows[0]['Name'] == "'=untrusted()"


def test_complex_reference_failure_redacts_private_paths_and_retains_guidance(monkeypatch):
    from nanocofold import complex as complexes
    def failed_reference(path):
        raise ValueError('비교 구조를 찾지 못했습니다. 파일을 다시 선택하세요: /lustre/private/account/reference.pdb')
    monkeypatch.setattr(complexes.pdb_io, 'load_structure', failed_reference)
    result, structure = complexes.reference_comparison(None, [], '/lustre/private/account/reference.pdb', {})
    assert result['available'] is False and structure is None
    assert '/lustre' not in result['reason'] and 'account' not in result['reason']
    assert '다시 선택하세요' in result['reason']
