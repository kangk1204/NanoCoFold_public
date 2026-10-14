"""Audit reproducers: input/output identity, public errors and portable exports."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nanocofold import handoff
from nanocofold.config import load_config
from nanocofold.platform.app import create_app
from nanocofold.platform.errors import public_error, public_log
from nanocofold.platform.inputs import parse_fasta, normalized_config
from nanocofold.platform.results import ResultReader
from nanocofold.utils.seq import validate_candidate_id, nucleotide_ambiguity_hint


@pytest.mark.parametrize('identifier', ['나노바디2', 'VHH(A3)', 'Nb#1', 'clone[2]', 'Nb+1', "it's", 'Nb_GFP_1'])
def test_allowed_candidate_id_exports_to_safe_pymol_object(tmp_path, identifier):
    assert validate_candidate_id(identifier) == identifier
    path = tmp_path / 'top.pml'
    handoff.write_pymol_session_script([(identifier, '/tmp/model.pdb', 'B')], path)
    text = path.read_text()
    mapping = json.loads(next(line.split(': ', 1)[1] for line in text.splitlines() if line.startswith('# candidate_id_map: ')))
    assert mapping['candidate_id'] == identifier
    assert re.fullmatch('[A-Za-z0-9_]+', mapping['object'])
    assert text.count('cmd.load(') == 1
    assert repr(mapping['object']) in text


def test_pymol_mapped_names_and_synthesis_bom(tmp_path):
    path = tmp_path / 'top.pml'
    handoff.write_pymol_session_script([('나노바디2', '/tmp/x.pdb', 'B'), ('나노바디2', '/tmp/y.pdb', 'B')], path)
    maps = [json.loads(line.split(': ',1)[1]) for line in path.read_text().splitlines() if line.startswith('# candidate_id_map: ')]
    assert len({row['object'] for row in maps}) == 2
    frame = pd.DataFrame([{'design_id':'나노바디2','sequence':'QVQLVESGGG','final_eligible':False}])
    output = handoff.write_synthesis_sheet(frame, load_config(), tmp_path)
    assert Path(output['csv']).read_bytes().startswith(b'\xef\xbb\xbf')
    assert pd.read_csv(output['csv']).iloc[0]['design_id'] == '나노바디2'


@pytest.mark.parametrize('message', ['PDB 또는 mmCIF 파일(.pdb/.cif)을 선택하세요.', 'run.mock 설정은 true/false 여야 합니다.'])
def test_normal_slash_prose_is_preserved(message):
    assert public_error(ValueError(message)) == message


@pytest.mark.parametrize('path', ['/home/lab member/data/x.pdb', 'C:\\Program Files\\Lab Data\\x.pdb', '\\\\labserver\\share\\x.pdb', '/other/account/file.pdb'])
def test_complete_private_path_removed_even_with_spaces(path):
    output = public_error(ValueError('파일 오류: ' + path))
    assert path not in output and '[서버 경로]' in output
    assert 'labserver' not in output and 'Lab Data' not in output and 'lab member' not in output


def test_internal_parser_context_and_msa_action():
    assert '전송 동의' in public_error(ValueError('Missing MSA consent: set structure.msa.allow_public_server=true'))
    assert 'atom (' not in public_error(ValueError("유효하지 않은 좌표(NaN/Inf)가 있습니다: atom ('uploaded', 0, 'A')"))
    assert 'invalid literal' not in public_error(ValueError("숫자가 잘못되었습니다 (invalid literal for int() with base 10: '1A')"))
    assert public_error(KeyError('작업이 없습니다.')) == '작업이 없습니다.'
    log = public_log('진행 2/4\nFile "/home/lab member/file.py", line 2\n완료')
    assert '/home/' not in log and '진행 2/4' in log and '완료' in log


def test_fasta_bom_no_phantom_header_and_residue_budgets():
    assert parse_fasta('\ufeff>나노바디2\nACDEFG') == [('나노바디2', 'ACDEFG')]
    assert parse_fasta('ACDEFG') == [('sequence', 'ACDEFG')]
    with pytest.raises(ValueError, match='헤더'):
        parse_fasta('ACDEFG\n>vhh\nACDEFG')
    with pytest.raises(ValueError, match='2,000잔기'):
        parse_fasta('>vhh\n'+'A'*100000)
    assert nucleotide_ambiguity_hint('ACGT'*10+'N')
    with pytest.raises(ValueError, match='120바이트'):
        validate_candidate_id('나노바디'*50)


def test_unregistered_subprocess_environment_override_rejected(tmp_path):
    with pytest.raises(ValueError, match='서버 관리자가 등록'):
        normalized_config(tmp_path, {'screen':{'colabfold':{'env_vars':{'LD_PRELOAD':'/tmp/custom.so'}}}})


@pytest.mark.parametrize('endpoint,payload', [
 ('/api/inputs', {'kind':'fasta','provider':['boltz2'],'content':'>target\nACDEFG'}),
 ('/api/inputs', {'kind':'fasta','mode':{},'content':'>target\nACDEFG'}),
 ('/api/inputs/complex', {'settings':[]}),
 ('/api/inputs/complex', {'reference':[]}),
 ('/api/inputs', {'kind':'fasta','mode':'mature','content':'>target\nACDEFG','wt_fasta':'>wt\nACDEFG','wt_structure':{'filename':[],'content':'x'}}),
 ('/api/inputs/complex', {'proteins':[{'sequence':'ACDEFG'},{'sequence':'LMNPQR'}],'reference':{'filename':[],'content':'x'}}),
])
def test_invalid_input_shapes_are_actionable_400(tmp_path, endpoint, payload):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token'] = app.state.token
        result = client.post(endpoint,json=payload)
        assert result.status_code == 400
        assert result.json()['error'] and 'Internal Server Error' not in result.text


def test_web_yaml_duplicate_keys_rejected(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token'] = app.state.token
        result = client.post('/api/configs/parse',json={'content':'run:\n  mode: screen\n  mode: design\n'})
        assert result.status_code == 400 and '중복' in result.json()['error']


@pytest.mark.parametrize('content', ['hello world', 'data_invalid\n_atom_site.invalid 1\n'])
def test_invalid_complex_reference_rejected_before_execution(tmp_path,content):
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.headers['x-platform-token']=app.state.token
        result=client.post('/api/inputs/complex',json={'proteins':[{'sequence':'ACDEFG'},{'sequence':'LMNPQR'}],
             'reference':{'filename':'ref.cif' if content.startswith('data_') else 'ref.pdb','content':content}})
        assert result.status_code == 400 and '경로' not in result.json()['error']
        assert client.get('/api/jobs').json()['jobs'] == []


def test_portable_csv_xlsx_html_and_population_counts(tmp_path):
    import io
    import zipfile
    (tmp_path/'03_score').mkdir()
    (tmp_path/'02_mature').mkdir()
    (tmp_path/'run_config.yaml').write_text('run:\n  mode: mature\nrank:\n  include_wt_control: false\n')
    (tmp_path/'run_state.json').write_text(json.dumps({'phases':{'mature':{'evaluated':True}}}))
    pd.DataFrame([{'design_id':'vhh1','sequence':'ACDEFG','cif_path':'/home/secret/model.cif','pred_dir':'/home/secret/pred','final_evaluated':True,'final_eligible':False}]).to_csv(tmp_path/'03_score/scored_candidates.csv',index=False)
    pd.DataFrame([{'variant_id':'vhh1','sequence':'ACDEFG','pass_llr':True},{'variant_id':'vhh2','sequence':'LMNPQR','pass_llr':False}]).to_csv(tmp_path/'02_mature/esm2_scored_library.csv',index=False)
    reader=ResultReader(tmp_path)
    summary=reader.summary()
    assert summary['candidate_record_total']==2 and summary['model_record_total']==1 and summary['preprediction_record_total']==1
    held=next(row for row in reader.all_rows() if row['candidate_id']=='vhh2')
    assert 'sequence_prior_prefilter_unmet' in held['exclusion_reasons']
    for fmt in ('csv','html'):
        data=reader.export(fmt)[0]
        assert b'/home/secret' not in data
    data=reader.export('xlsx')[0]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert not any(b'/home/secret' in z.read(name) for name in z.namelist())


def test_rejected_review_preserves_unpublished_copy_with_failure_receipt(tmp_path):
    from nanocofold.platform.inputs import InputManager
    manager = InputManager(tmp_path)
    try:
        good = manager.fasta({'content': '>target\nACDEFG', 'mode': 'screen', 'mock': True})
        original = (manager.root / good['id'] / 'target.fasta').read_bytes()
        with pytest.raises(ValueError, match='WT VHH'):
            manager.fasta({'content': '>target\nACDEFG', 'mode': 'mature', 'mock': True})
        assert [row['id'] for row in manager.list()] == [good['id']]
        failed = list((manager.root / 'failed_reviews').glob('*/failed_review.json'))
        assert len(failed) == 1
        receipt = json.loads(failed[0].read_text())
        assert receipt['state'] == 'review_failed' and receipt['ready_for_execution'] is False
        assert (failed[0].parent / 'target.fasta').read_text() == '>target\nACDEFG\n'
        assert (manager.root / good['id'] / 'target.fasta').read_bytes() == original
        assert sorted(p.name for p in manager.root.iterdir()) == sorted([good['id'], 'failed_reviews'])
        assert receipt['files'][0]['sha256']
    finally:
        manager.close()
