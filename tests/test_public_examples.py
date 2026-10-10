"""Public tutorial contracts; no engine invocation and no fabricated predictions."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from nanocofold.install import init_workspace
from nanocofold.platform.batches import BatchManager, expand_manifest
from nanocofold.platform.inputs import InputManager, parse_fasta
from nanocofold.platform.jobs import JobManager, TERMINAL

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'tutorial'
spec = importlib.util.spec_from_file_location('public_run_example', ROOT / 'scripts/run_example.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_public_sequences_have_source_and_exact_transform():
    records = json.loads((EXAMPLES / 'sources.json').read_text())['sequence_records']
    assert {r['id']:r['input_length'] for r in records} == {'barnase':110, 'barstar':89, 'human_il6':183, 'anti_il6_nb':132}
    for record in records:
        seq = record['input_sequence']
        assert len(seq) == record['input_length']
        assert hashlib.sha256(seq.encode()).hexdigest() == record['input_sha256']
        assert set(seq) <= set('ACDEFGHIKLMNPQRSTVWY')
        original = record['deposited_one_letter_code']
        expected = original[29:212] if record['id'] == 'human_il6' else original
        if record['id'] == 'human_il6':
            assert record['feature']['type'] == 'Chain'
            assert record['feature']['location']['start']['value'] == 30
            assert record['feature']['location']['end']['value'] == 212
        assert seq == expected.replace('\n', '')
    by_name = {r['id']:r['input_sequence'] for r in records}
    assert parse_fasta((EXAMPLES/'target.fasta').read_text(), single=True)[0][1] == by_name['human_il6']
    assert parse_fasta((EXAMPLES/'vhh_library.fasta').read_text(), single=True)[0][1] == by_name['anti_il6_nb']
    assert (EXAMPLES/'wt_vhh.fasta').read_bytes() == (EXAMPLES/'vhh_library.fasta').read_bytes()


@pytest.mark.parametrize('mode', ['complex','screen','mature','design'])
def test_real_batch_review_accepts_exact_public_manifest_without_starting_jobs(tmp_path, mode):
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        payload = runner.checked_payload(mode)
        assert payload['api_consent'] is False
        assert len(expand_manifest(payload)) == 1
        payload['api_consent'] = mode != 'complex'
        batch = BatchManager(inputs, jobs).review(payload)
        assert len(batch['rows']) == 1
        assert not any(row.get('error') for row in batch['rows']), batch['rows']
        assert batch['predictions_executed'] is False
        assert jobs.store.list() == []
        cfg = inputs.get(batch['rows'][0]['input_id'])['config']
        assert cfg['run']['mode'] == mode
        assert cfg['run']['mock'] is False
    finally:
        inputs.close(); jobs.close()


def test_hash_change_refused_before_review(tmp_path, monkeypatch):
    files = json.loads((EXAMPLES/'manifest.json').read_text())
    for name in files['files_sha256']:
        (tmp_path/name).write_bytes((EXAMPLES/name).read_bytes())
    (tmp_path/'manifest.json').write_bytes((EXAMPLES/'manifest.json').read_bytes())
    (tmp_path/'target.fasta').write_text('>changed\nAAA\n')
    monkeypatch.setattr(runner, 'EXAMPLES', tmp_path)
    with pytest.raises(ValueError, match='변경'):
        runner.checked_payload('screen')


def test_runtime_api_consent_is_explicit_and_does_not_change_example_file(tmp_path):
    before = (EXAMPLES/'screen_batch.json').read_bytes()
    with pytest.raises(SystemExit) as exc:
        runner.main(['screen', '--workspace', str(tmp_path)])
    assert exc.value.code == 2
    assert not (tmp_path/'.nanocofold').exists()
    assert (EXAMPLES/'screen_batch.json').read_bytes() == before


def test_model_plan_and_historical_output_absence():
    manifest = json.loads((EXAMPLES/'manifest.json').read_text())
    assert sum(x['maximum_representative_models'] for x in manifest['modes'].values()) == 25
    assert manifest['contains_precomputed_predictions'] is False
    assert runner.TERMINAL == TERMINAL
    for mode in ('screen','mature','design'):
        payload = runner.checked_payload(mode)
        entry = payload['entries'][0]
        assert entry['content'] == (EXAMPLES/'target.fasta').read_text()
        assert entry['hotspots'] == []
        cfg = entry['config']
        assert cfg['screen']['comparison']['engines'] == ['colabfold','boltz2','chai1','opendde']
        assert not cfg['rank']['fallback_to_ineligible']
        assert not cfg['screen']['restraint']['enabled']


def test_mature_has_real_esm_dms_and_wt_control():
    cfg = runner.checked_payload('mature')['entries'][0]['config']
    assert cfg['mature']['library']['strategy'] == 'dms'
    assert cfg['mature']['prefilter'] == {'mode':'esm2','enforce_threshold':True,'top_n':2}
    assert cfg['rank']['include_wt_control']
    assert not cfg['mature']['antifold']['enabled']


@pytest.fixture
def completed_core_workspace(tmp_path):
    init_workspace(tmp_path)
    assert not (tmp_path/'config/environment.yaml').exists()
    receipt = {'profile':'core', 'workspace':str(tmp_path), 'status':'complete', 'offline_ready':True}
    marker = tmp_path/'.platform/setup-core.json'
    marker.write_text(json.dumps(receipt))
    return tmp_path, marker, receipt


def test_core_only_install_allows_review_without_engine_config(completed_core_workspace, monkeypatch):
    workspace, _, _ = completed_core_workspace
    calls = []
    def cli(actual_workspace, args, **kwargs):
        assert actual_workspace == workspace
        calls.append(args[0])
        return 0, {'id':'b'*32,'review_sha256':'c'*64,'mode':'complex','rows':[{'id':'example','job_id':None}]}
    monkeypatch.setattr(runner, 'cli', cli)
    assert runner.main(['complex','--workspace',str(workspace),'--review-only']) == 0
    assert calls == ['review']
    assert not (workspace/'config/environment.yaml').exists()


def test_core_only_install_never_submits_prediction(completed_core_workspace, monkeypatch):
    workspace, _, _ = completed_core_workspace
    monkeypatch.setattr(runner, 'cli', lambda *a, **kw: pytest.fail('core-only must not submit'))
    assert runner.main(['complex','--workspace',str(workspace)]) == 1
    assert not (workspace/'.nanocofold/tutorial').exists()


@pytest.mark.parametrize('failure', ['missing','malformed','wrong_type','incomplete','not_ready','wrong_profile','wrong_workspace','missing_config'])
def test_core_review_rejects_unverified_install_before_cli(completed_core_workspace, monkeypatch, failure):
    workspace, marker, receipt = completed_core_workspace
    if failure == 'missing':
        marker.unlink()
    elif failure == 'malformed':
        marker.write_text('{')
    elif failure == 'wrong_type':
        marker.write_text('[]')
    elif failure == 'missing_config':
        (workspace/'config/default.yaml').unlink()
    else:
        key, value = {'incomplete':('status','failed'), 'not_ready':('offline_ready',False),
                      'wrong_profile':('profile','all'), 'wrong_workspace':('workspace',str(workspace/'other'))}[failure]
        receipt[key] = value
        marker.write_text(json.dumps(receipt))
    monkeypatch.setattr(runner, 'cli', lambda *a, **kw: pytest.fail('invalid install must not review'))
    assert runner.main(['complex','--workspace',str(workspace),'--review-only']) == 1
    assert not (workspace/'.nanocofold/tutorial').exists()


def test_completed_batch_is_not_resubmitted(tmp_path, monkeypatch, capsys):
    (tmp_path/'config').mkdir(); (tmp_path/'config/environment.yaml').write_text('{}')
    folder = tmp_path/'.nanocofold/tutorial/complex'; folder.mkdir(parents=True)
    (folder/'current.json').write_text(json.dumps({'batch_id':'a'*32}))
    calls = []
    def cli(workspace, args, **kwargs):
        calls.append(args)
        return 0, {'id':'a'*32,'mode':'complex','rows':[{'id':'example','job':{'state':'complete'}}]}
    monkeypatch.setattr(runner, 'cli', cli)
    assert runner.main(['complex','--workspace',str(tmp_path)]) == 0
    assert calls == [['status','--id','a'*32]]


def test_new_run_refuses_live_batch(tmp_path, monkeypatch):
    (tmp_path/'config').mkdir(); (tmp_path/'config/environment.yaml').write_text('{}')
    folder = tmp_path/'.nanocofold/tutorial/complex'; folder.mkdir(parents=True)
    (folder/'current.json').write_text(json.dumps({'batch_id':'a'*32}))
    calls = []
    def cli(workspace, args, **kwargs):
        calls.append(args)
        return 0, {'id':'a'*32,'mode':'complex','rows':[{'id':'example','job':{'state':'running'}}]}
    monkeypatch.setattr(runner, 'cli', cli)
    assert runner.main(['complex','--workspace',str(tmp_path),'--new-run']) == 1
    assert calls == [['status','--id','a'*32]]


@pytest.mark.parametrize('state', sorted(TERMINAL))
def test_explicit_new_run_preserves_terminal_batch_and_creates_only_review(tmp_path, monkeypatch, state):
    (tmp_path/'config').mkdir(); (tmp_path/'config/environment.yaml').write_text('{}')
    folder = tmp_path/'.nanocofold/tutorial/complex'; folder.mkdir(parents=True)
    old_attempt = folder/'old-attempt'; old_attempt.mkdir()
    old_receipt = old_attempt/'status.json'; old_receipt.write_text(json.dumps({'state':state}))
    before = old_receipt.read_bytes()
    (folder/'current.json').write_text(json.dumps({'batch_id':'a'*32, 'attempt':str(old_attempt)}))
    calls = []
    def cli(workspace, args, **kwargs):
        calls.append(args[0])
        if args[0] == 'status':
            return int(state!='complete'), {'id':'a'*32,'mode':'complex','rows':[{'id':'example','job':{'state':state}}]}
        assert args[0] == 'review'
        return 0, {'id':'b'*32,'review_sha256':'c'*64,'mode':'complex','rows':[{'id':'example','job_id':None}]}
    monkeypatch.setattr(runner, 'cli', cli)
    assert runner.main(['complex','--workspace',str(tmp_path),'--new-run','--review-only']) == 0
    assert calls == ['status','review']
    assert old_receipt.read_bytes() == before
    assert json.loads((folder/'current.json').read_text())['batch_id'] == 'b'*32
