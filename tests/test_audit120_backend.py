"""Third-reassessment backend regressions, without network or model inference."""
from __future__ import annotations

import copy
import json
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from nanocofold import cascade, colabfold_msa as msa, complex as cx, engine_comparison, mature, rank_select, target_prep
from nanocofold.config import load_config
from nanocofold.platform import eta
from nanocofold.platform.app import create_app
from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform.errors import public_error, public_error_data, public_log
from nanocofold.platform.inputs import parse_fasta
from nanocofold.platform.jobs import JobStore
from nanocofold.platform.results import ResultReader, TABLES
from nanocofold.utils import seq


@pytest.mark.parametrize('header', ['sp|P42212|GFP_AEQVI/1-238', '-reference'])
def test_reference_headers_do_not_use_candidate_path_rules(tmp_path, header):
    path = tmp_path/'reference.fasta'; path.write_text(f'>{header}\nACDEFG\n')
    cfg = load_config(); cfg['target'].update(sequence='', fasta=str(path))
    cfg['mature'].update(wt_sequence='', wt_fasta=str(path))
    assert target_prep.resolve_target_sequence(cfg) == 'ACDEFG'
    assert mature.resolve_wt_sequence(cfg) == 'ACDEFG'
    assert parse_fasta(path.read_text(), single=True, candidate_ids=False) == [(header, 'ACDEFG')]
    with pytest.raises(ValueError):
        seq.read_fasta(path)
    with pytest.raises(ValueError):
        parse_fasta(path.read_text())


@pytest.mark.parametrize('separator', ['\u2028', '\u2029'])
def test_hidden_unicode_line_separator_is_rejected_before_splitting(tmp_path, separator):
    text = '>Nb'+separator+'ACDEFG\nLMNPQR\n'
    path=tmp_path/'hidden.fasta'; path.write_text(text)
    with pytest.raises(ValueError):seq.read_fasta(path)
    with pytest.raises(ValueError):seq.read_fasta(path, candidate_ids=False)
    with pytest.raises(ValueError):parse_fasta(text)
    with pytest.raises(ValueError):seq.validate_candidate_id('Nb'+separator+'2')


def test_engine_prefix_length_rejected_before_public_upload(tmp_path, monkeypatch):
    cfg=load_config();cfg['screen']['colabfold']['msa_source']='public_api'
    cfg['screen']['comparison'].update(enabled=True, engines=['colabfold'])
    cfg['screen']['allow_public_msa_server']=True
    calls=[]
    monkeypatch.setattr(engine_comparison, '_acquire_reviewed_api', lambda *a,**k:calls.append(a) or {})
    identifier='가'*37  # Valid 111-byte source ID; invalid after colabfold__.
    assert seq.validate_candidate_id(identifier)==identifier
    with pytest.raises(ValueError,match='접두어'):
        engine_comparison.prepare_msa(cfg,tmp_path/'run',None,{'sequence':'ACDEFG'},pd.DataFrame([{'design_id':identifier,'sequence':'LMNPQR'}]))
    assert calls==[] and not (tmp_path/'run').exists()


def test_multimer_reserialization_keeps_frozen_input_bytes(tmp_path):
    alignments=[]
    for sequence, homolog in [('ACDEFG','YCDEFG'),('LMNPQR','YMNPQR')]:
        path=tmp_path/(sequence+'.a3m');path.write_text(f'>query\n{sequence}\n>db|label original\n{homolog}\n')
        alignments.append(msa.read_monomer(path,sequence))
    original=tmp_path/'first.a3m'
    original.write_text(''.join(msa._multimer_lines(['ACDEFG','LMNPQR'],[1,1],alignments)))
    reread=msa.read_multimer(original,{'A':'ACDEFG','B':'LMNPQR'})
    restored=reread.write(tmp_path/'again.a3m')
    assert original.read_bytes()==restored.read_bytes()
    assert b'chain_0_1 chain_0_1' not in restored.read_bytes()


@pytest.mark.parametrize('path', ['/연구실/데이터/x.pdb','/My Drive/lab/x.pdb','/x.pdb','/home/lab/model complex.pdb'])
def test_public_errors_redact_absolute_paths_and_keep_action(path):
    message=f'[오류] 입력 파일 {path}: 원본 파일을 확인하고 다시 시도하세요.'
    error=public_error(ValueError(message)); log=public_log(message)
    for result in [error,log]:
        assert path not in result and '[서버 경로]' in result
        assert '원본 파일을 확인하고 다시 시도하세요.' in result


@pytest.mark.parametrize('source', ['    df.loc[mask] = value','    pd.concat([a, b])','    warnings.warn(message)'])
def test_public_log_hides_python_source_but_keeps_progress(source):
    output=public_log('진행 1/2\n'+source+'\n완료')
    assert source.strip() not in output and '진행 1/2' in output and '완료' in output


def test_result_and_queue_error_fields_are_public_without_altering_originals(tmp_path):
    run=tmp_path/'run';run.mkdir()
    (run/'run_config.yaml').write_text('run:\n  mode: screen\n')
    (run/'run_state.json').write_text(json.dumps({'execution':{'state':'failed','error':'입력 오류 /연구실/자료/x.pdb: 파일 확인'}}))
    table=run/TABLES[0];table.parent.mkdir()
    pd.DataFrame([{'design_id':'a','sequence':'ACDEFG','prediction_status':'failed','prediction_error':'실행 실패 /My Drive/lab/x.pdb: 파일 확인'}]).to_csv(table,index=False)
    original=table.read_bytes()
    reader=ResultReader(run)
    assert '/My Drive' not in reader.all_rows()[0]['prediction_error']
    assert '/연구실' not in reader.state['execution']['error']
    assert table.read_bytes()==original
    store=JobStore(tmp_path)
    job={'id':'a','kind':'setup','state':'failed','created':1.,'updated':1.,'error':'입력 오류 /연구실/자료/x.pdb: 파일 확인'}
    store.insert(job)
    assert '/연구실' not in store.get('a')['error']
    with store.connect() as db:
        assert '/연구실' in json.loads(db.execute('SELECT payload FROM jobs').fetchone()[0])['error']
    assert public_error_data({'error':'recorded failure'})=={'error':'recorded failure'}


def test_bad_query_argument_has_actionable_public_422(tmp_path):
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token']=app.state.token
        response=client.get('/api/jobs?limit=bad')
        assert response.status_code==422 and '요청 인자' in response.json()['error']
        assert 'detail' not in response.json()


def test_heartbeat_counts_sql_without_reading_any_results(tmp_path,monkeypatch):
    store=JobStore(tmp_path)
    store.insert({'id':'a','kind':'setup','state':'complete','created':1.,'updated':1.})
    monkeypatch.setattr(store,'list',lambda *a,**k:pytest.fail('heartbeat must not reconcile all jobs'))
    calls=[]
    agent=SimpleNamespace(client=SimpleNamespace(request=lambda path,payload:calls.append((path,payload))),agent_id='agent',capabilities=lambda:{},installation_identity=lambda:{},diagnostics={},sync_errors={},export_errors={},manager=SimpleNamespace(store=store))
    Agent.heartbeat(agent)
    assert calls[0][1]['queue_jobs']==1


def test_cache_retains_reported_800_complex_jobs(monkeypatch,tmp_path):
    calls=[]
    def refresh(job,**kwargs):
        calls.append(job['id']);job.update(progress={'completed':1,'total':1},eta={'status':'complete','total':1})
        return job
    monkeypatch.setattr(eta,'refresh_complex_job',refresh)
    cache=eta.ComplexJobReadCache()
    jobs=[{'id':str(i),'kind':'analysis','mode':'complex','state':'complete','run_dir':str(tmp_path/str(i))} for i in range(800)]
    for _ in range(2):
        for job in jobs:cache.read(copy.deepcopy(job),record_key=job['id'],terminal_states={'complete'})
    assert len(calls)==800


@pytest.mark.parametrize('metric',['ipsae','iptm','esm_score'])
def test_missing_weighted_measurement_holds_ranking_and_fallback(metric):
    cfg=load_config();cfg['rank'].update(weights={metric:1},fallback_to_ineligible=True)
    frame=pd.DataFrame([{'design_id':name,'sequence':'ACDEFG','n_contacts':4,metric:value,'final_eligible':True} for name,value in [('a',.9),('missing',np.nan),('b',.2)]])
    ranked=rank_select.rank_candidates(frame,cfg).set_index('design_id')
    assert pd.isna(ranked.loc['missing','composite_score']) and pd.isna(ranked.loc['missing','rank'])
    assert ranked.loc['missing','ranking_unavailable_reason']=='weighted_metrics_unavailable'
    assert 'missing' not in rank_select.select_final(ranked.reset_index(),cfg,pre_ranked=True).design_id.tolist()
    assert 'ipsae' not in cfg['rank']['weights'] or cfg['score']['thresholds']['ipsae']==.6


def test_primary_no_contact_warning_preserves_selected_pair_and_scores():
    pairs=[{'chain_a':'A','chain_b':'B','n_contacts':0,'ipsae':0.}, {'chain_a':'A','chain_b':'C','n_contacts':5,'ipsae':.6}]
    result=cx.pair_summary(pairs,{'primary_pair':['A','B']})
    assert result['primary_pair']==['A','B'] and result['ipsae']==0.
    assert '다른 체인쌍' in result['primary_pair_warning']
    assert cx.pair_summary(pairs,{'primary_pair':['A','C']})['primary_pair_warning'] is None


def test_legacy_config_can_be_read_for_report_but_not_accepted_for_prediction(tmp_path):
    path=tmp_path/'run_config.yaml'
    path.write_text(yaml.safe_dump({'run':{'mode':'complex'},'complex':{'engine':'boltz2','proteins':[{'sequence':'ACDEFG','copies':2,'reference_chain':'X'}]}}))
    original=path.read_bytes()
    with pytest.raises(ValueError,match='reference_chains'):load_config(path)
    cfg=load_config(path,historical=True)
    assert cfg['complex']['proteins'][0]['reference_chain']=='X'
    assert path.read_bytes()==original
    with pytest.raises(ValueError,match='reference_chains'):cx.proteins(cfg['complex']['proteins'])
    from nanocofold import cli
    args=cli.build_parser().parse_args(['report','--config',str(path),'--run-dir',str(tmp_path)])
    loaded=cli._load(args)
    actual_root,_=cli._open_run(loaded,args,new=False)
    assert actual_root==tmp_path and path.read_bytes()==original


@pytest.mark.parametrize('metric,first,direction',[('ipsae','high','descending'),('interface_pae','low','ascending'),('n_clashes','low','ascending')])
def test_cascade_records_explicit_metric_direction(metric,first,direction):
    cfg=load_config();cfg['screen']['cascade'].update(ranking_metric=metric,top_n=1,audit_n=0,diversity_n=0)
    frame=pd.DataFrame([{'design_id':'low','sequence':'ACDEFG'},{'design_id':'high','sequence':'LMNPQR'}])
    metrics=pd.DataFrame([{'design_id':'low',metric:.1},{'design_id':'high',metric:.9}])
    result=cascade.select_candidates(frame,metrics,cfg).set_index('design_id')
    assert result.loc[first,'colabfold_rank']==1
    assert set(result.cascade_ranking_direction)=={direction}


@pytest.mark.parametrize('overrides,phrase', [(['run.mode=screen','design.refinement.enabled=true'],'design 모드'),(['screen.comparison.enabled=true','screen.cascade.enabled=true'],'함께 켤 수'),(['screen.comparison.engines=[unknown]'],'중복 없는')])
def test_config_errors_remain_actionable_at_public_boundary(overrides,phrase):
    with pytest.raises(ValueError) as caught:load_config(overrides=overrides)
    assert phrase in public_error(caught.value)


@pytest.mark.parametrize("explicit_config", [False, True])
def test_legacy_cli_and_actual_fasta_delivery_exports_preserve_original_source(tmp_path, explicit_config):
    root = tmp_path / "legacy"; root.mkdir()
    config_path = root / "run_config.yaml"
    config_path.write_text(yaml.safe_dump({"run": {"mode": "complex"}, "complex": {"engine": "boltz2", "proteins": [
        {"name": "historical copies", "sequence": "ACDEFG", "copies": 2, "reference_chain": "X"}]}}))
    state_path = root / "run_state.json"
    state_path.write_text(json.dumps({"phases": {"prediction": {"completed_models": 1, "total_models": 1}, "final": {"total_models": 1}},
                                      "execution": {"state": "complete", "command": "complex"}}))
    (root / "complex_results.json").write_text(json.dumps({"rows": [{"candidate_id": "seed_42_model_0", "evaluated": True, "ipsae": .7}]}))
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    reader = ResultReader(root)
    fasta = reader.export("fasta")[0]
    assert fasta.count(b"ACDEFG") == 2 and b">A historical copies" in fasta and b">B historical copies" in fasta
    archive = zipfile.ZipFile(io.BytesIO(reader.export("report_zip")[0]))
    assert archive.read("sequences.fasta") == fasta
    assert {name: (root/name).read_bytes() for name in before} == before
    command = [sys.executable, "-m", "nanocofold", "report", "--run-dir", str(root)]
    if explicit_config: command += ["--config", str(config_path)]
    source = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(source), "NANOCOFOLD_PROGRESS": "off"}
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert (root/"report_delivery.zip").is_file()
    with zipfile.ZipFile(root/"report_delivery.zip") as delivered:
        assert delivered.read("sequences.fasta") == fasta
    assert {name: (root/name).read_bytes() for name in before} == before
    with pytest.raises(ValueError, match="reference_chains"):load_config(config_path)
    with pytest.raises(ValueError, match="reference_chains"):cx.proteins(reader.config["complex"]["proteins"])


@pytest.mark.parametrize('quote', ['"', "'"])
def test_complete_quoted_extensionless_path_is_hidden_before_token_masking(quote):
    message = f'입력 파일 오류: {quote}/home/lab member{quote}; 파일을 확인하세요'
    expected = f'입력 파일 오류: {quote}[서버 경로]{quote}; 파일을 확인하세요'
    assert public_error(ValueError(message)) == expected
    assert public_log(message) == expected
    assert 'member' not in public_error(ValueError(message))


def test_extensionless_unquoted_root_withholds_ambiguous_tail_without_leaking_fragments():
    message = '입력 파일 오류: /home/lab member then text'
    assert public_error(ValueError(message)) == '입력 파일 오류: [서버 경로]'
    assert public_log(message) == '입력 파일 오류: [서버 경로]'
    assert public_log('input error: /home/lab member then text') == 'input error: [서버 경로]'


def test_finite_spaced_filename_retains_following_action():
    message = '입력 파일 오류: /home/lab member/source model.pdb; 파일을 확인하세요'
    expected = '입력 파일 오류: [서버 경로]; 파일을 확인하세요'
    assert public_error(ValueError(message)) == expected
    assert public_log(message) == expected
