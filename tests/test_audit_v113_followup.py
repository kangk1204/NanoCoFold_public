"""CPU-only regressions for the v113 review; no existing runs or GPU are accessed."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from nanocofold import cli, screen
from nanocofold.config import load_config, dump_resolved
from nanocofold.platform.jobs import JobManager
from nanocofold.state import RunState


def settings():
    cfg = load_config()
    cfg['run'].update(mode='screen', mock=True)
    cfg['target']['sequence'] = 'ACDEFGHIKLMNPQRSTVWY'
    cfg['screen'].update(engine='chai1', boltz2_rescore_top=0, vhh_fasta=None)
    cfg['screen']['restraint']['enabled'] = False
    return cfg


def test_partial_resume_config_is_validated_against_saved_mode(tmp_path):
    cfg = settings()
    dump_resolved(cfg, tmp_path/'run_config.yaml')
    partial = tmp_path/'partial.yaml'
    partial.write_text('screen:\n  cascade:\n    enabled: true\n  engine: colabfold\n  restraint:\n    enabled: false\n')
    args = SimpleNamespace(set=[],mode=None,command='run',config=str(partial),run_dir=str(tmp_path),mock=False,dry_run=False)
    actual = cli._load(args)
    assert actual['run']['mode'] == 'screen'
    assert actual['target']['sequence'] == cfg['target']['sequence']
    assert actual['screen']['cascade']['enabled']


def test_comparison_rejection_does_not_mutate_saved_files(tmp_path):
    cfg = settings()
    cfg['screen']['comparison']={'enabled':True,'engines':['boltz2','chai1'],'continue_on_error':True}
    dump_resolved(cfg, tmp_path/'run_config.yaml')
    state = RunState(tmp_path)
    state.data.update(mode='screen',execution={'state':'complete'})
    state.save()
    (tmp_path/'02_comparison').mkdir()
    (tmp_path/'02_comparison/comparison_identity.json').write_text(json.dumps({'engines':['boltz2','chai1'],'runtime':{}}))
    (tmp_path/'01_target').mkdir()
    (tmp_path/'01_target/preserved.txt').write_text('original')
    before={p.relative_to(tmp_path).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    assert cli.main(['run','--run-dir',str(tmp_path),'--set','run.seed=7']) == 1
    after={p.relative_to(tmp_path).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file() and p.name!='.run.lock'}
    assert before==after


@pytest.mark.parametrize(('mode','active'), [('screen','02_screen'),('mature','02b_mature_complex'),('design','02b_tier2')])
def test_rescore_reads_only_current_mode_and_enabled_rescore(tmp_path,mode,active):
    cfg=settings();cfg['run']['mode']=mode
    for stage in ['02_screen','02b_mature_complex','02b_tier2','02c_boltz2','02_comparison']:
        folder=tmp_path/stage;folder.mkdir()
        pd.DataFrame([{'design_id':'v','pred_dir':str(folder/'v'),'engine':'boltz2' if stage=='02c_boltz2' else 'chai1','seed':7 if stage==active else 42}]).to_csv(folder/'predictions.csv',index=False)
    original={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*.csv')}
    row=cli._load_prediction_manifests(tmp_path,cfg).iloc[0]
    assert row['seed']==7 and row['pred_dir']==str(tmp_path/active/'v')
    if mode in {'design','screen'}:
        cfg['screen']['boltz2_rescore_top']=1
        assert cli._load_prediction_manifests(tmp_path,cfg).iloc[0]['engine']=='boltz2'
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in original.items())


def test_prediction_failure_does_not_starve_later_candidates_or_reuse_old_manifest(tmp_path,monkeypatch):
    cfg=settings();cfg['run']['mock']=False
    frame=pd.DataFrame([{'design_id':name,'sequence':'ACDE'} for name in ['good1','bad','good2']])
    calls=[]
    def predict(cfg,directory,*args,**kwargs):
        calls.append(directory.name)
        if directory.name=='bad':raise RuntimeError('deterministic engine failure')
        return 'chai1_single'
    monkeypatch.setattr(screen,'_predict_chai1',predict)
    folder=tmp_path/'02_screen'
    with pytest.raises(RuntimeError,match='1건 실패'):
        screen.predict_complexes(cfg,folder,{'sequence':'ACDEFG','hotspot_seq_positions':[]},frame)
    assert calls==['good1','bad','good2']
    saved=pd.read_csv(folder/'predictions.csv')
    assert saved.design_id.tolist()==['good1','good2']
    assert saved.seed.tolist()==[42,42] and set(saved.seed_source)=={'prediction'}
    failure=json.loads((folder/'prediction_failures.json').read_text())
    assert [x['candidate_id'] for x in failure]==['bad']
    monkeypatch.setattr(screen,'_predict_chai1',lambda *a,**k:'chai1_single')
    screen.predict_complexes(cfg,folder,{'sequence':'ACDEFG','hotspot_seq_positions':[]},frame)
    assert json.loads((folder/'prediction_failures.json').read_text())==[]
    assert len(pd.read_csv(folder/'predictions.csv'))==3
    assert list(folder.glob('.history/**/prediction_failures.json'))


def test_one_account_cannot_join_another_accounts_identical_active_job(tmp_path):
    manager=JobManager(tmp_path)
    try:
        first=manager.create({'name':'private Kim name','researcher':'Kim','reuse_results':False,'config':settings()})
        identity={'digest':'same-input','reusable':True}
        manager.store.update(first['id'],execution_identity=identity)
        second=copy.deepcopy(first)
        second.update(id='other-request',name='Song name',researcher='Song',reuse_results=True,execution_identity=identity,run_dir=str(tmp_path/'runs/other-request'))
        accepted=manager.store.insert(second)
        assert accepted['id']=='other-request'
        assert [j['id'] for j in manager.store.list(researcher='Song')]==['other-request']
        assert [j['id'] for j in manager.store.list(researcher='Kim')]==[first['id']]
    finally:manager.close()


def test_unreadable_gpu_status_fails_boundedly_but_capacity_wait_does_not(tmp_path,monkeypatch):
    import nanocofold.platform.jobs as jobs
    manager=JobManager(tmp_path)
    try:
        job=manager.create({'config':settings()})
        now=1000.0;monkeypatch.setattr(jobs.time,'time',lambda:now)
        manager._gpu_wait(job,{'reason':'telemetry'})
        waiting=manager.store.get(job['id']);assert waiting['state']=='queued'
        now+=121;manager._gpu_wait(waiting,{'reason':'telemetry'})
        failed=manager.store.get(job['id']);assert failed['state']=='failed' and failed['can_resume']
        second=manager.create({'name':'wait','reuse_results':False,'config':settings()})
        manager._gpu_wait(second,{'reason':'capacity'});now+=10000
        manager._gpu_wait(manager.store.get(second['id']),{'reason':'capacity'})
        assert manager.store.get(second['id'])['state']=='queued'
    finally:manager.close()


def test_cascade_validation_reports_both_required_settings():
    with pytest.raises(ValueError) as caught:
        load_config(overrides=['run.mode=screen','screen.cascade.enabled=true'])
    assert 'screen.engine=colabfold' in str(caught.value) and 'screen.restraint.enabled=false' in str(caught.value)


@pytest.mark.parametrize('mode',['complex','design','screen','mature'])
def test_unbound_environment_check_does_not_require_public_msa_consent(tmp_path,monkeypatch,mode):
    from nanocofold.platform.cloud_agent import Agent
    from nanocofold import diagnostics
    seen=[]
    monkeypatch.setattr(diagnostics,'doctor',lambda cfg,**kwargs:(seen.append((cfg,kwargs)) or {'ok':True,'checks':[]}))
    agent=Agent(tmp_path,SimpleNamespace(),agent_id='local-test')
    try:
        cfg={'complex_comparison':mode=='complex','comparison_engines':['boltz2','chai1'], 'workflow':'direct' if mode=='complex' else 'design_engine_comparison' if mode=='design' else 'engine_comparison','msa_source':'public_api','allow_public_msa_server':False}
        result=agent.operation({'id':'environment','agent_id':'local-test','kind':'diagnostics','payload':json.dumps({'mode':mode,'settings':cfg})})
        assert result['response']['ok']
        assert result['response']['review_context']['bound'] is False
        assert seen[0][1]['environment_only'] is True
    finally:
        agent.inputs.close();agent.manager.close()
