"""Real HTTP and saved-stage boundaries; isolated workspaces, no GPU/network."""
import hashlib
import json
import subprocess
import sys
import re
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nanocofold import cli
from nanocofold.config import load_config
from nanocofold.install import init_workspace
from nanocofold.platform.app import create_app
from nanocofold.state import RunState
from nanocofold.utils.predictions import prediction_attempt, record_manifest
from nanocofold.platform.lab import LabAccounts
from nanocofold import mock
from nanocofold.config import dump_resolved
from nanocofold.platform.result_catalog import build_identity
from nanocofold import mature
import copy
from nanocofold.platform.batches import BatchManager

VHH='QVQLVESGGGLVQPGESLRLSCAASGSIFGIYAVHWFRMAPGKEREFTAGFGSHGSTNYAASVKGRFTMSRDNAKNTTYLQMNSLKPADTAVYYCHALIKNELGFLDYWGPGTQVTVSS'
ROOT=Path(__file__).resolve().parents[1]


def manifest():
    return {'version':1,'mode':'screen','name':'consent-boundary','mock':False,
        'targets_fasta':'>target\n'+'ACDEFGHIKLMNPQRSTVWY'*2,'vhh_fasta':'>vhh\n'+VHH,
        'config':{'screen':{'engine':'boltz2','comparison':{'enabled':False},
            'allow_public_msa_server':True,'colabfold':{'msa_source':'public_api'}},
            'structure':{'provider':'boltz2','msa':{'backend':'empty'}}}}


@pytest.fixture
def api(tmp_path):
    init_workspace(tmp_path)
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1',headers={'x-platform-token':app.state.token}) as client:
        yield client,app,tmp_path
    app.state.inputs.close();app.state.jobs.close()


def approve(client, allowed=True):
    result=client.post('/api/msa-consent',json={'allowed':allowed})
    assert result.status_code==200,result.text


def reviewed(client):
    response=client.post('/api/batches/review',json=manifest())
    assert response.status_code==200,response.text
    batch=response.json()
    assert not any(row.get('error') for row in batch['rows']),batch
    return batch


@pytest.mark.parametrize(('saved','explicit','expected'),[(True,False,400),(True,None,200),(False,True,400)])
def test_http_manifest_approval_is_a_default_not_an_override(api,saved,explicit,expected):
    client,app,_=api;approve(client,saved)
    payload=manifest()
    if explicit is not None:payload['api_consent']=explicit
    response=client.post('/api/batches/review',json=payload)
    assert response.status_code==expected,response.text
    assert app.state.jobs.store.list()==[]


@pytest.mark.parametrize('destination',['batch','single'])
def test_revocation_blocks_previously_reviewed_submission_without_mutation(api,destination):
    client,app,root=api;approve(client)
    batch=reviewed(client)
    prepared=app.state.inputs.get(batch['rows'][0]['input_id'])
    original=Path(prepared['config_path']).read_bytes()
    approve(client,False)
    if destination=='batch':
        response=client.post('/api/batches/'+batch['id']+'/submit',json={'review_sha256':batch['review_sha256']})
    else:
        response=client.post('/api/jobs',json={'input_id':prepared['input_id']})
    assert response.status_code==400,response.text
    assert '해제' in response.text
    assert app.state.jobs.store.list()==[]
    assert Path(prepared['config_path']).read_bytes()==original
    approve(client)
    response=client.post('/api/jobs',json={'input_id':prepared['input_id']})
    assert response.status_code==200,response.text
    assert str(root) not in json.dumps(response.json(),ensure_ascii=False)


def test_revocation_blocks_resume_and_keeps_cancelled_state(api):
    client,app,root=api;approve(client)
    batch=reviewed(client)
    response=client.post('/api/jobs',json={'input_id':batch['rows'][0]['input_id']})
    assert response.status_code==200,response.text
    identifier=response.json()['id']
    stopped=client.post('/api/jobs/'+identifier+'/cancel',json={})
    assert stopped.status_code==200 and stopped.json()['state']=='cancelled'
    assert str(root) not in json.dumps(stopped.json())
    before=app.state.jobs.store.get(identifier)
    approve(client,False)
    resumed=client.post('/api/jobs/'+identifier+'/resume',json={})
    assert resumed.status_code==400 and '해제' in resumed.text
    assert app.state.jobs.store.get(identifier)==before
    approve(client)
    resumed=client.post('/api/jobs/'+identifier+'/resume',json={})
    assert resumed.status_code==200,resumed.text
    assert str(root) not in json.dumps(resumed.json())


@pytest.mark.parametrize('section',['screen','structure','complex'])
def test_imported_explicit_refusal_survives_saved_approval_http(api,monkeypatch,section):
    client,app,_=api;approve(client)
    monkeypatch.setattr(app.state.inputs,'review',lambda payload:payload)
    response=client.post('/api/inputs/review',json={'settings':{'allow_public_msa_server':False},'config':{section:{'allow_public_msa_server':False}}})
    assert response.status_code==200,response.text
    actual=response.json()
    assert actual['settings']['allow_public_msa_server'] is False
    assert actual['config'][section]['allow_public_msa_server'] is False


@pytest.mark.parametrize('endpoint',['/api/diagnostics','/api/environment/check'])
def test_write_job_responses_use_workspace_relative_paths(api,endpoint):
    client,_,root=api
    response=client.post(endpoint,json={'config':{'run':{'mode':'screen','mock':True}}})
    assert response.status_code==200,response.text
    assert str(root) not in json.dumps(response.json())
    assert str(root) not in client.get('/api/diagnostics').text


def stage_fixture(root):
    cfg=load_config();cfg['run'].update(mode='screen',seed=7)
    cfg['screen'].update(engine='chai1',boltz2_rescore_top=0)
    cfg['screen']['cascade']['enabled']=False;cfg['screen']['comparison']['enabled']=False
    for name,engine,seed,value in [('02_screen','chai1',7,.58),('02c_boltz2','boltz2',42,.80)]:
        folder=root/name;folder.mkdir()
        pd.DataFrame([{'design_id':'WT','pred_dir':str(folder/'WT'),'engine':engine,'seed':seed,'iptm':value}]).to_csv(folder/'predictions.csv',index=False)
    return cfg


@pytest.mark.parametrize('receipted',[False,True])
def test_score_flag_cannot_select_an_old_prediction_stage(tmp_path,receipted):
    cfg=stage_fixture(tmp_path)
    if receipted:
        state=RunState(tmp_path)
        state.data['attempts']=[{'command':'screen'}]
        with prediction_attempt(tmp_path,{'mode':'screen','cascade':False,'comparison':False}):
            record_manifest(tmp_path/'02_screen/predictions.csv')
            cli._persist_prediction_stages(state,cfg)
    original={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*.csv')}
    for value in [0,2,0]:
        cfg['screen']['boltz2_rescore_top']=value
        actual=cli._load_prediction_manifests(tmp_path,cfg).iloc[0]
        assert (actual['engine'],actual['seed'],actual['iptm'])==('chai1',7,.58)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in original.items())


def test_valid_recorded_rescore_is_kept_and_changed_manifests_are_rejected(tmp_path):
    cfg=stage_fixture(tmp_path);state=RunState(tmp_path)
    with prediction_attempt(tmp_path):
        for stage in ['02_screen','02c_boltz2']:record_manifest(tmp_path/stage/'predictions.csv')
        cli._persist_prediction_stages(state,cfg)
    assert cli._load_prediction_manifests(tmp_path,cfg).iloc[0]['engine']=='boltz2'
    with (tmp_path/'02c_boltz2/predictions.csv').open('a') as file:file.write('\n')
    with pytest.raises(ValueError,match='예측 목록'):
        cli._load_prediction_manifests(tmp_path,cfg)


def test_real_cpu_score_ignores_preserved_old_boltz_stage(tmp_path):
    target='ACDEFGHIKLMNPQRSTVWY'
    fasta=tmp_path/'candidates.fasta';fasta.write_text('>WT\n'+VHH+'\n')
    cfg=load_config();cfg['run'].update(mode='screen',seed=7,mock=True)
    cfg['target'].update(sequence=target,hotspots=[])
    cfg['truncate']['enabled']=False
    cfg['screen'].update(engine='chai1',boltz2_rescore_top=0,vhh_fasta=str(fasta))
    cfg['screen']['restraint']['enabled']=False
    cfg['screen']['comparison']['enabled']=False;cfg['screen']['cascade']['enabled']=False
    config=tmp_path/'config.yaml';dump_resolved(cfg,config)
    run=tmp_path/'run'
    assert cli.main(['screen','--config',str(config),'--run-dir',str(run),'--mock'])==0
    baseline=pd.read_csv(run/'03_score/scored_candidates.csv')
    old=run/'02c_boltz2';candidate=old/'WT';candidate.mkdir(parents=True)
    mock.make_mock_engine_output(candidate,'WT',{'A':target,'B':VHH},[],engine='boltz2',seed=42,samples=1)
    pd.DataFrame([{'design_id':'WT','pred_dir':'02c_boltz2/WT','engine':'boltz2','seed':42,'target_chain':'A','binder_chain':'B'}]).to_csv(old/'predictions.csv',index=False)
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in old.rglob('*') if p.is_file()}
    for flag in [0,2,0]:
        assert cli.main(['score','--run-dir',str(run),'--set','screen.boltz2_rescore_top='+str(flag)])==0
        actual=pd.read_csv(run/'03_score/scored_candidates.csv')
        pd.testing.assert_frame_equal(actual[['design_id','engine','iptm','ipsae']],baseline[['design_id','engine','iptm','ipsae']])
        state=json.loads((run/'run_state.json').read_text())
        assert [item['path'] for item in state['attempts'][-1]['stages_used']]==['02_screen/predictions.csv']
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==value for p,value in hashes.items())


def test_shipped_offline_template_is_generated_from_current_assets():
    subprocess.run([sys.executable,str(ROOT/'scripts/build_platform_offline.py'),'--check'],check=True,capture_output=True)


def test_admin_can_inspect_but_cannot_cancel_or_resume_another_researchers_job(api):
    _,app,root=api
    users=LabAccounts(root)
    users.set_password('admin','test-admin-private')
    users.promote_existing_admin()
    other=app.state.jobs.create({'researcher':'Alpha','config':{'run':{'mode':'design','mock':True},'target':{'sequence':'ACDEFGHIKLMNPQRSTVWY'}}})
    with TestClient(app,base_url='http://127.0.0.1',follow_redirects=False) as admin:
        assert admin.post('/lab/login',headers={'origin':'http://127.0.0.1'},data={'name':'admin','password':'test-admin-private'}).status_code==303
        token=re.search(r'window.PLATFORM_TOKEN = "([0-9a-f]+)"',admin.get('/').text)[1]
        admin.headers.update({'x-platform-token':token})
        assert other['id'] in admin.get('/api/jobs').text
        assert admin.get('/api/jobs/'+other['id']).status_code==200
        assert admin.post('/api/jobs/'+other['id']+'/cancel',json={}).status_code==403
        assert admin.post('/api/jobs/'+other['id']+'/resume',json={}).status_code==403
        assert app.state.jobs.store.get(other['id'])['state']=='queued'


def test_reuse_identity_ignores_names_but_includes_both_seeds_and_mutation_options(tmp_path):
    cfg=load_config();cfg['run'].update(mode='mature',mock=True,seed=42)
    cfg['target']['sequence']='ACDEFGHIKLMNPQRSTVWY'
    cfg['mature']['wt_sequence']=VHH
    cfg['mature']['library'].update(strategy='random',size=101,min_mutations=2,max_mutations=2,seed=42)
    original=build_identity(tmp_path,cfg,'0')['digest']
    renamed=copy.deepcopy(cfg);renamed['run']['name']='another-analysis'
    assert build_identity(tmp_path,renamed,'0')['digest']==original
    for section,key,value in [('run','seed',43),('mature','library',{'strategy':'random','size':101,'seed':43}),('screen','engine','boltz2')]:
        changed=copy.deepcopy(cfg);changed[section][key]=value
        assert build_identity(tmp_path,changed,'0')['digest']!=original


def test_random_variant_library_repeats_with_same_seed_and_changes_with_new_seed():
    options={'size':101,'min_mutations':2,'max_mutations':2,'mutation_region':'cdr3'}
    regions={'CDR1':(27,33),'CDR2':(56,58),'CDR3':(99,108)}
    first=mature.generate_random_library(VHH,regions,seed=42,**options)
    again=mature.generate_random_library(VHH,regions,seed=42,**options)
    changed=mature.generate_random_library(VHH,regions,seed=43,**options)
    assert first.sequence.tolist()==again.sequence.tolist()
    assert first.sequence.tolist()!=changed.sequence.tolist()
    assert first.iloc[1:].mutation_count.eq(2).all() and changed.iloc[1:].mutation_count.eq(2).all()
    assert first.iloc[0].sequence==changed.iloc[0].sequence==VHH


def test_legacy_mixed_batch_cancels_only_its_submitters_active_jobs(api):
    _,app,_=api
    batches=BatchManager(app.state.inputs,app.state.jobs)
    batch=batches.review({'version':1,'mode':'design','mock':True,'targets_fasta':'>one\nACDEFGHIKL\n>two\nLMNPQRSTVWY\n'},researcher='Alpha')
    jobs=[]
    for row,owner in zip(batch['rows'],['Alpha','Beta']):
        prepared=app.state.inputs.get(row['input_id'])
        job=app.state.jobs.create({'researcher':owner,'config':prepared['config'],'reuse_results':False})
        row['job_id']=job['id'];jobs.append(job)
    batches._save(batch)
    expected={'batch_id':batch['id'],'jobs':[{'job_id':job['id'],'state':job['state'],'pid':job.get('pid'),
        'process_stamp':job.get('process_stamp'),'started':job.get('started'),'resume':job.get('resume') is True,
        'history_length':len(job.get('history',[]))} for job in jobs]}
    batches.cancel(batch['id'],expected_cancel=expected,researcher='Alpha')
    assert app.state.jobs.store.get(jobs[0]['id'])['state']=='cancelled'
    assert app.state.jobs.store.get(jobs[1]['id'])['state']=='queued'
