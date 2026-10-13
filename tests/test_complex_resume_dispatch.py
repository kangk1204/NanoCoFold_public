"""Real orchestration and persistence; synthetic native files are CPU contracts."""
from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from Bio.PDB import PDBIO
from Bio.PDB.Atom import Atom
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.Residue import Residue
from Bio.PDB.Structure import Structure

from nanocofold import complex as cx, complex_comparison as comparison, opendde, provenance
from nanocofold.config import load_config
from nanocofold.state import RunState
from nanocofold.utils.seq import AA1TO3


def config(*, engines=None, seeds=(42, 43), models=1, sequences=('ACDEFG', 'LMNPQR')):
    cfg = load_config()
    cfg['run'].update(mode='complex', mock=False, resume=True)
    cfg['score']['ipsae']['use_official'] = False
    cfg['complex'] = {'seeds': list(seeds), 'models_per_seed': models, 'recycles': 2, 'diffusion_steps': 37,
                      'proteins': [{'name': f'protein{i}', 'sequence': seq} for i, seq in enumerate(sequences)]}
    cfg['complex'].update({'comparison_engines': engines, 'msa_source': 'cache'} if engines else {'engine': 'boltz2'})
    return cfg


def native_files(folder, records, models=1):
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(models):
        structure = Structure('fixture'); model = Model(0); structure.add(model)
        serial = 0
        for axis, (letter, sequence) in enumerate(records.items()):
            chain = Chain(letter); model.add(chain)
            for n, aa in enumerate(sequence, 1):
                residue = Residue((' ', n, ' '), AA1TO3[aa], ' '); chain.add(residue)
                serial += 1
                residue.add(Atom('CA', np.array([n * 3.8, axis * 6., .1 * n * n]), 80., 1., ' ', ' CA ', serial, element='C'))
        stem = f'native_model_{index}'
        writer = PDBIO(); writer.set_structure(structure); writer.save(str(folder / (stem + '.pdb')))
        size = sum(map(len, records.values()))
        (folder / ('confidence_' + stem + '.json')).write_text(json.dumps({'iptm': .8, 'plddt': [80.] * size}))
        np.savez_compressed(folder / ('pae_' + stem + '.npz'), pae=np.full((size, size), 4.))
    return folder


@pytest.fixture
def native_flow(monkeypatch, tmp_path):
    calls = []
    failures = {('boltz2', 43)}
    monkeypatch.setattr(provenance, 'runtime_identity', lambda *a, **k: {'engine': a[1], 'fixture_runtime': True})
    def predict(cfg, folder, records, *args, **kwargs):
        engine, seed = 'boltz2', cfg['run']['seed']
        calls.append((engine, seed))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'boltz2.log').write_text('intentional CPU fixture attempt\n')
        if (engine, seed) in failures:
            raise ValueError('intentional partial failure')
        native_files(folder / 'boltz_out/boltz_results_fixture/predictions/model', records, cfg['score']['boltz2_diffusion_samples'])
    monkeypatch.setattr(cx.screen, '_predict_boltz2', predict)
    return SimpleNamespace(calls=calls, failures=failures, root=tmp_path / 'run')


def ledger(root):
    return json.loads((root / 'complex_results.json').read_text())


def all_files(root):
    return {str(p.relative_to(root)): provenance.file_digest(p) for p in root.rglob('*') if p.is_file()}


def test_partial_resume_runs_only_failed_seed_and_keeps_success_bytes(native_flow):
    cfg, flow = config(), native_flow
    assert cx.run(cfg, flow.root, RunState(flow.root)) == 0
    first = ledger(flow.root)
    assert [row['evaluated'] for row in first['rows']] == [True, False]
    success = copy.deepcopy(first['rows'][0])
    success_bytes = {name: (flow.root / name).read_bytes() for name in success['artifacts']}
    failed_attempt = flow.root / first['rows'][1]['attempt']
    failed_manifest = all_files(failed_attempt)
    flow.failures.clear(); flow.failures.add(('boltz2', 42))
    assert cx.run(cfg, flow.root, RunState(flow.root)) == 0
    final = ledger(flow.root)
    assert flow.calls == [('boltz2', 42), ('boltz2', 43), ('boltz2', 43)]
    assert all(row['evaluated'] for row in final['rows'])
    assert final['rows'][0] == success
    assert {name: (flow.root / name).read_bytes() for name in success_bytes} == success_bytes
    assert all_files(failed_attempt) == failed_manifest
    assert final['rows'][1]['attempt'] != first['rows'][1]['attempt']
    assert cx.run(cfg, flow.root, RunState(flow.root)) == 0
    assert len(flow.calls) == 3
    assert ledger(flow.root)['rows'] == final['rows']


@pytest.mark.parametrize('corruption', ['raw', 'display', 'row', 'receipt', 'native_extra', 'missing', 'symlink'])
def test_corrupt_success_refuses_before_any_prediction_preserving_ledger(native_flow, corruption):
    flow, cfg = native_flow, config()
    cx.run(cfg, flow.root, RunState(flow.root))
    row = ledger(flow.root)['rows'][0]
    if corruption in {'raw', 'display', 'missing', 'symlink'}:
        name = row['structure_file'] if corruption == 'display' else next(name for name in row['artifacts'] if name.endswith('native_model_0.pdb'))
        path = flow.root / name
        if corruption == 'missing': path.unlink()
        elif corruption == 'symlink':
            outside = flow.root.parent / 'external.pdb'; outside.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(outside)
        else: path.write_bytes(path.read_bytes() + b'changed')
    elif corruption == 'row':
        saved = ledger(flow.root); saved['rows'][0]['iptm'] = .99
        (flow.root / 'complex_results.json').write_text(json.dumps(saved))
    elif corruption == 'receipt':
        path = flow.root / row['prediction_receipt']['path']; path.write_text('{}')
    else:
        receipt = json.loads((flow.root / row['prediction_receipt']['path']).read_text())
        name = next(name for name in receipt['artifacts'] if name.endswith('boltz2.log'))
        (flow.root / name).write_text('tampered native log')
    before = (flow.root / 'complex_results.json').read_bytes()
    original_calls = list(flow.calls)
    with pytest.raises(ValueError, match='재개를 중단'):
        cx.run(cfg, flow.root, RunState(flow.root))
    assert flow.calls == original_calls
    assert (flow.root / 'complex_results.json').read_bytes() == before
    assert list((flow.root / '02_complex/resume_rejections').glob('*.json'))


@pytest.mark.parametrize('change', ['sequence', 'cycles', 'steps', 'seeds', 'models', 'runtime', 'code', 'restart'])
def test_changed_conditions_never_reuse_or_overwrite(native_flow, monkeypatch, change):
    flow, cfg = native_flow, config()
    cx.run(cfg, flow.root, RunState(flow.root))
    before = (flow.root / 'complex_results.json').read_bytes()
    modified = copy.deepcopy(cfg)
    if change == 'sequence': modified['complex']['proteins'][0]['sequence'] = 'ACDEFA'
    elif change == 'cycles': modified['complex']['recycles'] = 3
    elif change == 'steps': modified['complex']['diffusion_steps'] = 38
    elif change == 'seeds': modified['complex']['seeds'] = [42, 44]
    elif change == 'models': modified['complex']['models_per_seed'] = 2
    elif change == 'runtime': monkeypatch.setattr(provenance, 'runtime_identity', lambda *a, **k: {'fixture_runtime': 'changed'})
    elif change == 'code':
        original = provenance.file_digest
        monkeypatch.setattr(provenance, 'file_digest', lambda path: 'changed' if str(path).endswith('/complex.py') else original(path))
    else: modified['run']['resume'] = False
    with pytest.raises(ValueError, match='재개를 중단'):
        cx.run(modified, flow.root, RunState(flow.root))
    assert len(flow.calls) == 2
    assert (flow.root / 'complex_results.json').read_bytes() == before


def test_legacy_receipt_cannot_fabricate_current_identity(native_flow):
    flow, cfg = native_flow, config()
    flow.root.mkdir()
    (flow.root / 'complex_results.json').write_text(json.dumps({'rows': [{'candidate_id': 'old', 'evaluated': True}]}))
    before = (flow.root / 'complex_results.json').read_bytes()
    with pytest.raises(ValueError, match='identity'):
        cx.run(cfg, flow.root, RunState(flow.root))
    assert flow.calls == [] and (flow.root / 'complex_results.json').read_bytes() == before


def test_interruption_after_one_success_retains_it_and_reuses_native_inference(native_flow, monkeypatch):
    flow, cfg = native_flow, config(seeds=(42,), models=2)
    original = cx.structure_data
    counter = 0
    def interrupted(*args):
        nonlocal counter
        counter += 1
        if counter == 2: raise TimeoutError('cancelled CPU analysis')
        return original(*args)
    monkeypatch.setattr(cx, 'structure_data', interrupted)
    with pytest.raises(TimeoutError): cx.run(cfg, flow.root, RunState(flow.root))
    success = copy.deepcopy(ledger(flow.root)['rows'][0])
    artifacts = {name: (flow.root / name).read_bytes() for name in success['artifacts']}
    monkeypatch.setattr(cx, 'structure_data', original)
    cx.run(cfg, flow.root, RunState(flow.root))
    final = ledger(flow.root)['rows']
    assert flow.calls == [('boltz2', 42)]
    assert len(final) == 2 and all(row['evaluated'] for row in final)
    assert final[0] == success
    assert final[1]['elapsed_scope'] == 'analysis_only_native_reused'
    assert {name: (flow.root / name).read_bytes() for name in artifacts} == artifacts
    assert len(list((flow.root / '02_complex/attempts/boltz2__seed_42').glob('*/attempt.json'))) == 2


def test_evaluation_failure_can_be_retried_without_new_gpu_attempt(native_flow, monkeypatch):
    flow, cfg = native_flow, config(seeds=(42,), models=2)
    original = cx.structure_data
    def failure(pred, *args):
        if pred.sample_index == 1: raise ValueError('intentional evaluator failure')
        return original(pred, *args)
    monkeypatch.setattr(cx, 'structure_data', failure)
    cx.run(cfg, flow.root, RunState(flow.root))
    assert [row['evaluated'] for row in ledger(flow.root)['rows']] == [True, False]
    monkeypatch.setattr(cx, 'structure_data', original)
    cx.run(cfg, flow.root, RunState(flow.root))
    assert flow.calls == [('boltz2', 42)]
    assert all(row['evaluated'] for row in ledger(flow.root)['rows'])


@pytest.mark.parametrize('sequences', [('ACDEFG', 'LMNPQR', 'STVWYA'), ('STVWYA', 'ACDEFG', 'LMNPQR'),
                                       ('ACDEFG', 'LMNPQR', 'STVWYA', 'HIKLMN'), ('ACDEFG', 'ACDEFG', 'LMNPQR')])
def test_selected_pair_scope_is_exported_without_new_aggregation(native_flow, monkeypatch, sequences):
    flow, cfg = native_flow, config(seeds=(42,), sequences=sequences)
    pairs = [{'chain_a': a, 'chain_b': b, 'ipsae': value, 'n_clashes': i}
             for i, ((a, b), value) in enumerate(zip(itertools.combinations('ABCD'[:len(sequences)], 2), [.9, .2, .1, .4, .5, .6]))]
    original = cx.structure_data
    def data(*args):
        value = original(*args); value['pair_metrics'] = copy.deepcopy(pairs); return value
    monkeypatch.setattr(cx, 'structure_data', data)
    cx.run(cfg, flow.root, RunState(flow.root))
    row = ledger(flow.root)['rows'][0]
    assert row['ipsae'] == .9 and row['pair_metrics'] == pairs
    assert (row['ipsae_scope'], row['ipsae_chain_a'], row['ipsae_chain_b']) == ('selected_chain_pair', 'A', 'B')
    assert row['ipsae_aggregation'] == 'max_of_directional_pair_ipsae'
    assert row['iptm_scope'] == 'whole_complex' and row['n_clashes_scope'] == 'sum_over_all_chain_pairs'
    from nanocofold.platform.results import ResultReader
    (flow.root / 'run_config.yaml').write_text('run:\n  mode: complex\n')
    exported, _, _ = ResultReader(flow.root).export('csv')
    assert b'selected_chain_pair' in exported and b'max_of_directional_pair_ipsae' in exported
    assert (flow.root / row['structure_file']).is_file()


def test_comparison_opendde_nondefault_conditions_reach_native_argv(tmp_path, monkeypatch):
    cfg = config(engines=['opendde'], seeds=(73,), models=2)
    settings = cx.validate_settings(cfg['complex'])
    cfg['complex'].update(settings)
    cfg['run']['seed'] = 73
    cfg = comparison.configure(cfg, settings)
    records = {'A': 'ACDEFG', 'B': 'LMNPQR'}
    monomers = {}
    for letter, sequence in records.items():
        msa = tmp_path / (letter + '.a3m'); msa.write_text('>query\n' + sequence + '\n>homolog\n' + sequence[:-1] + 'A\n')
        monomers[letter] = {'a3m': str(msa), 'sha256': provenance.file_digest(msa)}
    monkeypatch.setattr(opendde, 'readiness', lambda *a: {'ok': True})
    monkeypatch.setattr(opendde, 'installation_identity', lambda *a: {'ok': True})
    monkeypatch.setattr(provenance, 'runtime_identity', lambda *a, **k: {'fixture': True})
    monkeypatch.setattr(opendde.shell, 'env_executable', lambda *a, **k: Path('/fixture/bin/python'))
    calls = []
    def native(env, argv, **kwargs):
        calls.append(argv)
        raw = Path(argv[argv.index('-o') + 1]); raw.mkdir(parents=True)
        (raw / 'native_receipt.txt').write_text('synthetic native output')
    monkeypatch.setattr(opendde.shell, 'run_in_env', native)
    def normalize(raw, out, chains, samples):
        out.mkdir(parents=True); (out / 'normalized.txt').write_text('synthetic normalized output')
    monkeypatch.setattr(opendde, 'normalize_outputs', normalize)
    _, receipt = comparison.predict(cfg, tmp_path / 'comparison', records, 'opendde', monomers)
    argv = calls[0]
    assert argv[argv.index('--cycle') + 1] == '2'
    assert argv[argv.index('--step') + 1] == '37'
    assert argv[argv.index('--sample') + 1] == '2'
    input_path = Path(argv[argv.index('-i') + 1])
    assert json.loads(input_path.read_text())[0]['modelSeeds'] == [73]
    assert receipt['engine_conditions']['cycles'] == 2 and receipt['engine_conditions']['steps'] == 37
    assert receipt['condition_receipt']['requested']['recycles'] == 2
    assert receipt['condition_receipt']['effective']['samples'] == 2
    single = copy.deepcopy(cfg); single['complex'].pop('comparison_engines'); single['complex']['engine'] = 'opendde'
    single['complex']['msa'] = 'empty'
    applied = comparison.effective_config(single, cx.validate_settings(single['complex']), 'opendde', 73)
    assert opendde.settings(applied) == opendde.settings(cfg)


@pytest.mark.parametrize('engine', ['colabfold', 'boltz2', 'chai1', 'opendde'])
def test_all_engine_condition_receipts_match_option_mapping(engine):
    cfg = config(engines=[engine], seeds=(73,))
    settings = cx.validate_settings(cfg['complex'])
    applied = comparison.effective_config(comparison.configure(cfg, settings), settings, engine, 73)
    receipt = comparison.condition_receipt(applied, settings, engine)
    effective = receipt['effective']
    assert effective['seed'] == 73
    assert effective['cycles' if engine == 'opendde' else 'num_recycles'] == 2
    if engine == 'opendde': assert effective['steps'] == 37 and effective['samples'] == 1
    elif engine == 'chai1': assert effective['diffusion_timesteps'] == 37 and effective['diffusion_samples'] == 1
    elif engine == 'boltz2': assert effective['extra_args'][-2:] == ['--sampling_steps', '37'] and effective['diffusion_samples'] == 1
    else: assert effective['num_models'] == 1 and receipt['unsupported_options']


def comparison_flow(monkeypatch, tmp_path):
    calls, failures = [], {('opendde', 42)}
    cfg = config(engines=['colabfold', 'boltz2', 'chai1', 'opendde'])
    records = {'A': 'ACDEFG', 'B': 'LMNPQR'}
    msa_paths = {}
    for letter, sequence in records.items():
        path = tmp_path / (letter + '.a3m'); path.write_text('>query\n' + sequence + '\n>homolog\n' + sequence[:-1] + 'A\n'); msa_paths[letter] = path
    def prepare(applied, *args):
        return applied, {letter: {'a3m': str(path), 'sha256': provenance.file_digest(path)} for letter, path in msa_paths.items()}
    monkeypatch.setattr(comparison, 'prepare', prepare)
    monkeypatch.setattr(provenance, 'runtime_identity', lambda *a, **k: {'engine': a[1], 'fixture': True})
    def predict(applied, folder, chains, engine, monomers):
        calls.append((engine, applied['run']['seed']))
        folder.mkdir(parents=True); (folder / 'native_attempt.txt').write_text('CPU-only attempt')
        if calls[-1] in failures: raise ValueError('intentional failed OpenDDE seed')
        directory = native_files(folder / 'native_out', chains)
        return directory, {'condition_receipt': comparison.condition_receipt(applied, cx.validate_settings(applied['complex']), engine)}
    monkeypatch.setattr(comparison, 'predict', predict)
    return SimpleNamespace(cfg=cfg, calls=calls, failures=failures, paths=msa_paths, root=tmp_path / 'run')


def test_four_engine_resume_keeps_every_success_and_retries_only_failed_group(tmp_path, monkeypatch):
    flow = comparison_flow(monkeypatch, tmp_path)
    cx.run(flow.cfg, flow.root, RunState(flow.root))
    before = ledger(flow.root)
    successful = {row['candidate_id']: copy.deepcopy(row) for row in before['rows'] if row['evaluated']}
    successful_artifacts = {name: (flow.root / name).read_bytes() for row in successful.values() for name in row['artifacts']}
    assert len(flow.calls) == 8 and len(successful) == 7
    flow.failures.clear()
    cx.run(flow.cfg, flow.root, RunState(flow.root))
    after = ledger(flow.root)
    assert len(flow.calls) == 9 and flow.calls[-1] == ('opendde', 42)
    assert len(after['rows']) == 8 and all(row['evaluated'] for row in after['rows'])
    assert all(row == successful[row['candidate_id']] for row in after['rows'] if row['candidate_id'] in successful)
    assert {name: (flow.root / name).read_bytes() for name in successful_artifacts} == successful_artifacts
    assert all(row['condition_receipt']['requested']['recycles'] == 2 for row in after['rows'])
    od = next(row for row in after['rows'] if row['engine'] == 'opendde')
    assert od['condition_receipt']['effective']['cycles'] == 2 and od['condition_receipt']['effective']['steps'] == 37


def test_changed_prepared_msa_refuses_resume_before_any_native_call(tmp_path, monkeypatch):
    flow = comparison_flow(monkeypatch, tmp_path)
    cx.run(flow.cfg, flow.root, RunState(flow.root))
    before = (flow.root / 'complex_results.json').read_bytes()
    flow.paths['A'].write_text('>query\nACDEFG\n>changed_homolog\nACDEFA\n')
    with pytest.raises(ValueError, match='지문이 변경'):
        cx.run(flow.cfg, flow.root, RunState(flow.root))
    assert len(flow.calls) == 8
    assert (flow.root / 'complex_results.json').read_bytes() == before
    assert list((flow.root / '02_complex/resume_rejections').glob('*.json'))


def test_explicit_copy_input_scope_keeps_reviewed_chain_names(native_flow):
    flow, cfg = native_flow, config(seeds=(42,))
    cfg['complex']['proteins'] = [{'name': 'copy', 'sequence': 'ACDEFG', 'copies': 3}, {'name': 'other', 'sequence': 'LMNPQR'}]
    cx.run(cfg, flow.root, RunState(flow.root))
    value = ledger(flow.root); row = value['rows'][0]
    assert [entry['name'] for entry in value['proteins']] == ['copy', 'copy', 'copy', 'other']
    assert row['ipsae_chain_a'] == 'A' and row['ipsae_chain_b'] == 'B'
    assert len(row['pair_metrics']) == 6
    assert row['ipsae'] == row['pair_metrics'][0]['ipsae']


def test_native_completion_before_first_observation_interrupt_avoids_reinference(native_flow, monkeypatch):
    flow, cfg = native_flow, config(seeds=(42,))
    original = cx.structure_data
    def interrupt(*args): raise TimeoutError('CPU cancelled before observation commit')
    monkeypatch.setattr(cx, 'structure_data', interrupt)
    with pytest.raises(TimeoutError): cx.run(cfg, flow.root, RunState(flow.root))
    assert not (flow.root / 'complex_results.json').exists()
    monkeypatch.setattr(cx, 'structure_data', original)
    cx.run(cfg, flow.root, RunState(flow.root))
    assert flow.calls == [('boltz2', 42)]
    assert ledger(flow.root)['rows'][0]['evaluated'] is True


def test_interrupt_between_observation_and_ledger_commit_recovers_exact_success(native_flow, monkeypatch):
    flow, cfg = native_flow, config(seeds=(42,))
    original = cx._save_observations
    def interrupt(*args): raise TimeoutError('after immutable observation write before active ledger')
    monkeypatch.setattr(cx, '_save_observations', interrupt)
    with pytest.raises(TimeoutError): cx.run(cfg, flow.root, RunState(flow.root))
    assert not (flow.root / 'complex_results.json').exists()
    path = next((flow.root / '02_complex/attempts').glob('*/*/models/model_0/observation.json'))
    success = json.loads(path.read_text())
    artifacts = {name: (flow.root / name).read_bytes() for name in success['artifacts']}
    monkeypatch.setattr(cx, '_save_observations', original)
    cx.run(cfg, flow.root, RunState(flow.root))
    assert flow.calls == [('boltz2', 42)]
    assert ledger(flow.root)['rows'] == [success]
    assert {name: (flow.root / name).read_bytes() for name in artifacts} == artifacts
