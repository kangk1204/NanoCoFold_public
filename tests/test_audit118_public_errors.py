"""Actual batch/public error boundaries; no network or inference."""
import warnings
import pytest
from fastapi.testclient import TestClient
from nanocofold.config import load_config
from nanocofold.platform.app import create_app
from nanocofold.platform.errors import public_error, public_log
from nanocofold.utils.presentation import portable_metadata
from nanocofold.platform.inputs import parse_fasta

@pytest.mark.parametrize('separator', ['\x85', '\x0b', '\x0c', '\u200b'])
def test_web_fasta_rejects_hidden_separators(separator):
    with pytest.raises(ValueError):
        parse_fasta('>Nb'+separator+'ACDEFG')

@pytest.mark.parametrize('message', ['버전 0.4.8rc1 과 https://example.org/a/b 문서를 확인하세요',
 '분자 C(/F)=C/F 비율 30%/70%', 'PDB 또는 mmCIF 파일(.pdb/.cif)을 선택하세요.'])
def test_preserve_nonpath_prose(message):
    assert public_error(ValueError(message)) == message

def test_actual_msa_error_is_actionable():
    with pytest.raises(ValueError) as error:
        load_config(overrides=['screen.colabfold.msa_source=public_api','screen.allow_public_msa_server=false'])
    assert '전송 동의' in public_error(error.value)

def test_default_mature_size_warning():
    with warnings.catch_warnings(record=True) as found:
        warnings.simplefilter('always');load_config(overrides=['run.mode=mature'])
    assert not any('dms에서는' in str(w.message) for w in found)
    with pytest.warns(UserWarning, match='dms에서는'):
        load_config(overrides=['run.mode=mature','mature.library.size=33'])

def test_spaced_paths_and_raw_coordinate_contract():
    data={'env_vars':{'TORCH_HOME':'/home/lab member/.cache/torch'},'warning':'파일 /home/lab member/.cache/torch 확인','raw_content':'CIF native /home/model/path unchanged'}
    output=portable_metadata(data)
    assert output['env_vars']['TORCH_HOME']=='[local path]'
    assert output['warning']=='파일 [local path] 확인'
    assert output['raw_content']==data['raw_content']
    assert 'ranked[' not in public_log('진행 2/4\n    ranked["composite_score"] = x\n완료')

BAD_PDB=('ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00 20.00           N\n'
 'ATOM      2  N   GLY A   1       1.000   0.000   0.000  1.00 20.00           N\nEND\n')

def payload(reference=None):
    entry={'id':'row1','proteins':[{'name':'target','sequence':'ACDEFG'},{'name':'partner','sequence':'LMNPQR'}]}
    if reference:entry['reference']=reference
    return {'version':1,'mode':'complex','entries':[entry],'config':{'complex':{'engine':'boltz2','seeds':[42],'models_per_seed':1,'msa':'empty'}}}

@pytest.mark.parametrize('reference',[{'filename':'bad.pdb','content':BAD_PDB},{'filename':'bad.cif','content':'data_invalid\n_atom_site.invalid 1\n'}])
def test_batch_reference_error_is_public(tmp_path,reference):
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token']=app.state.token
        result=client.post('/api/batches/review',json=payload(reference))
        assert result.status_code==200,result.text
        error=result.json()['rows'][0]['error']
        assert error and str(tmp_path) not in error and '_atom_site' not in error and '<Residue' not in error
        assert 'defined twice' not in error
        assert client.get('/api/jobs').json()['jobs']==[]

def test_submission_error_and_directory_config(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token']=app.state.token
        reviewed=client.post('/api/batches/review',json=payload()).json()
        assert not reviewed['rows'][0].get('error'),reviewed
        def failed_create(*args,**kwargs):raise OSError('출력 파일 오류: /home/private lab/run/model.pdb')
        monkeypatch.setattr(app.state.jobs,'create',failed_create)
        result=client.post('/api/batches/'+reviewed['id']+'/submit',json={'review_sha256':reviewed['review_sha256']})
        assert result.status_code==200,result.text
        error=result.json()['rows'][0]['submission_error']
        assert 'private lab' not in error and '/home/' not in error and '출력 파일 오류' in error
        result=client.post('/api/jobs',json={'config_path':str(tmp_path)})
        assert result.status_code==400 and '폴더가 아닌' in result.json()['error']

def test_report_options_preserve_recorded_defaults_and_apply_explicit_overrides(tmp_path, monkeypatch, capsys):
    from nanocofold import cli, config
    from test_audit117_scientific_receipts import _saved_run
    import copy
    saved = _saved_run(tmp_path, 'complete')
    saved['report']['top_n_table'] = 3
    saved['report']['title'] = '기록된 제목'
    config.dump_resolved(saved, tmp_path / 'run_config.yaml')
    protected = (tmp_path / 'run_config.yaml').read_bytes()
    seen = []
    monkeypatch.setattr(cli.report, 'generate_report', lambda *a, **k: seen.append(copy.deepcopy(a[1])) or tmp_path / 'report.html')
    args = cli.build_parser().parse_args(['report','--run-dir',str(tmp_path)])
    cli.cmd_report(args)
    assert seen[-1]['report']['top_n_table'] == 3
    assert seen[-1]['report']['title'] == '기록된 제목'
    args = cli.build_parser().parse_args(['report','--run-dir',str(tmp_path),'--set','report.top_n_table=17','--set','score.thresholds.ipsae=0.99'])
    cli.cmd_report(args)
    assert seen[-1]['report']['top_n_table'] == 17
    assert seen[-1]['score']['thresholds']['ipsae'] == .6
    assert '--set' in capsys.readouterr().err
    assert (tmp_path / 'run_config.yaml').read_bytes() == protected
