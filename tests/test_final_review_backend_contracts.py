"""Final review fixes with real local publication and no network/GPU inference."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest

from nanocofold import cascade, cli, config, mature, screen
from nanocofold.platform.batches import expand_manifest
from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform import cloud_results, inputs
from nanocofold.utils import atomic, shell, seq


def projection_config():
    cfg=config.load_config()
    cfg['run'].update(mode='mature',mock=False)
    cfg['mature'].update(wt_sequence='ACDEFGHIKLMNPQRSTVWY'*2,prefilter={'mode':'none'})
    cfg['mature']['library'].update(strategy='random',size=3,min_mutations=1,max_mutations=8)
    cfg['screen']['engine']='colabfold'
    cfg['screen']['colabfold']['msa_mode']='precomputed'
    return cfg


def test_projection_limit_rejected_by_config_and_library_before_generation(monkeypatch):
    cfg=projection_config()
    monkeypatch.setattr(mature,'generate_random_library',lambda *a,**k:pytest.fail('late library generation'))
    with pytest.raises(ValueError,match='최대 5개'):
        config._validate(cfg)
    with pytest.raises(ValueError,match='최대 5개'):
        mature.generate_library(cfg['mature']['wt_sequence'],{'CDR1':(1,10)},cfg)


def test_projection_input_review_gate_preserves_previous_valid_input(tmp_path):
    manager=inputs.InputManager(tmp_path)
    original=tmp_path/'prior_valid_input.json';original.write_bytes(b'{"preserved":true}')
    before=original.read_bytes()
    payload={'mode':'mature','provider':'boltz2','config':projection_config(),'content':'>target\nACDEFGHIKLMNPQRSTVWY\n','wt_fasta':'>WT\nACDEFGHIKLMNPQRSTVWY\n'}
    try:
        with pytest.raises(ValueError,match='최대 5개'):
            inputs.normalized_fasta_config(tmp_path,payload)
        assert original.read_bytes()==before
        assert not list(manager.root.glob('*/platform_input.json'))
    finally:manager.close()


def test_nonprojection_and_exact_query_comparison_keep_random_eight_supported():
    cfg=projection_config();cfg['screen']['colabfold']['msa_mode']='single_sequence'
    mature.validate_msa_mutation_limit(cfg)
    cfg['screen']['colabfold']['msa_mode']='precomputed';cfg['screen']['comparison']['enabled']=True
    mature.validate_msa_mutation_limit(cfg)
    cfg['screen']['comparison']['enabled']=False;cfg['mature']['library']['max_mutations']=5
    config._validate(cfg)


def test_invalid_programmatic_lineage_stops_before_MSA_acquisition(tmp_path,monkeypatch):
    cfg=projection_config();cfg['mature']['library']['max_mutations']=5
    baseline=cfg['mature']['wt_sequence'];mutant='Y'*6+baseline[6:]
    candidates=pd.DataFrame([{'design_id':'bad','sequence':mutant,'mutation_count':6}])
    monkeypatch.setattr(cascade,'_acquire_public_msas',lambda *a,**k:pytest.fail('invalid lineage was uploaded'))
    old=tmp_path/'successful_ledger.json';old.write_bytes(b'{"prior_success":true}')
    with pytest.raises(ValueError,match='후보 bad'):
        cascade.prepare_msa(cfg,tmp_path,SimpleNamespace(),{'sequence':'ACDEFG'},candidates,{'CDR1':(1,10)})
    assert old.read_bytes()==b'{"prior_success":true}'


@pytest.mark.parametrize('identifier',["sp|P01308|INS_HUMAN","나노바디2(A3);O'Neil"])
def test_batch_original_fasta_ID_preserved_with_safe_alias(identifier,tmp_path):
    fasta=f'>{identifier} description\nACDEFGHIKLMNPQRSTVWY\n'
    source=tmp_path/'input.fasta';source.write_text(fasta)
    assert list(seq.read_fasta(source))==[identifier]
    rows=expand_manifest({'version':1,'mode':'screen','targets_fasta':fasta,'vhh_fasta':'>vhh\nACDEFGHIKLMNPQRSTVWY\n'})
    row=rows[0]
    assert row['id']==row['display_id']==identifier
    assert row['alias']==seq.candidate_id_aliases([identifier])[identifier]
    assert row['alias'].startswith('ncf_') and row['input']['content'].startswith('>'+identifier+'\n')


@pytest.mark.parametrize('names',[['Ａ','A'],['e\u0301','é'],['case','CASE']])
def test_batch_unicode_equivalent_ids_rejected_before_review(names):
    value={'version':1,'mode':'screen','vhh_fasta':'>vhh\nACDEFG\n','entries':[{'id':name,'content':'>t\nACDEFG\n'} for name in names]}
    with pytest.raises(ValueError,match='정규화'):
        expand_manifest(value)


def test_CLI_effective_config_independent_and_identical_before_and_after_saved_run(tmp_path):
    cfg=config.load_config();cfg['run'].update(mode='design',mock=True)
    cfg['mature']['wt_sequence']='ACDEFGHIK'
    directory=tmp_path/'run';directory.mkdir()
    config.dump_resolved(cfg,directory/'run_config.yaml')
    from nanocofold.state import RunState
    RunState(directory).save()
    args=SimpleNamespace(command='mature',mode=None,mock=True,dry_run=False,wt_fasta='wt.fasta',vhh_fasta=None,run_dir=str(directory),run_id=None,config=None,set=[])
    original=copy.deepcopy(cfg);before=cli._normalized_cli_config(cfg,args)
    assert cfg==original
    returned,state=cli._open_run(cfg,args)
    assert returned==directory and cli._normalized_cli_config(cfg,args)==before
    assert before['run']['mode']=='mature' and before['mature']['wt_sequence']==''
    before['score']['thresholds']['iptm']=0.123
    assert cfg['score']['thresholds']['iptm']!=0.123


def test_capability_python_digest_tracks_actual_installed_sources():
    value=Agent.capabilities()
    from nanocofold import __version__,provenance
    import nanocofold.platform.cloud_agent as agent
    root=Path(agent.__file__).resolve().parents[1]
    expected={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py')) if '__pycache__' not in p.parts}
    assert value['package_version']==__version__
    assert value['application_python_sha256']==provenance.fingerprint(expected)
    assert value['application_python_files']==len(expected)


def chai_config(tmp_path):
    cfg={'screen':{'env':None,'colabfold':{'msa_max_sequences':20}}}
    source=tmp_path/'input.a3m';source.write_text('>query\nACDEFG\n>homolog\nACDEYG\n')
    candidate=tmp_path/'predictions/candidate';candidate.mkdir(parents=True)
    return cfg,source,candidate


def fake_conversion(calls):
    def run(env,command,**kwargs):
        calls.append(command)
        directory=Path(command[-1]).parent;manifest=json.loads(Path(command[-1]).read_bytes())
        receipt={'schema_version':1,'pairing':'unpaired','inputs_fingerprint':manifest['inputs_fingerprint'],'chains':{}}
        for chain,item in manifest['chains'].items():
            digest=hashlib.sha256(item['sequence'].encode()).hexdigest();name=digest+'.aligned.pqt'
            data=b'controlled-converter-output';(directory/name).write_bytes(data)
            receipt['chains'][chain]={'file':name,'sequence_sha256':digest,'sha256':hashlib.sha256(data).hexdigest(),'parsed_rows':2}
        (directory/'conversion_receipt.json').write_text(json.dumps(receipt))
    return run


def test_chai_shared_cache_receipt_reuse_and_corrupt_repair_preserve_old_bytes(tmp_path,monkeypatch):
    cfg,source,candidate=chai_config(tmp_path);calls=[]
    monkeypatch.setattr(shell,'run_in_env',fake_conversion(calls))
    directory=screen._prepare_chai_msa_directory(cfg,candidate,{'sequence':'ACDEFG'},str(source))
    assert screen._prepare_chai_msa_directory(cfg,candidate,{'sequence':'ACDEFG'},str(source))==directory and len(calls)==1
    artifact=next(directory.glob('*.aligned.pqt'));artifact.write_bytes(b'corrupt-old-bytes')
    repaired=screen._prepare_chai_msa_directory(cfg,candidate,{'sequence':'ACDEFG'},str(source))
    assert repaired==directory and len(calls)==2
    assert any(p.read_bytes()==b'corrupt-old-bytes' for p in directory.parent.rglob('*.aligned.pqt'))
    assert screen._validate_chai_precomputed_directory(directory,{'A':'ACDEFG'})


def test_chai_failed_conversion_leaves_original_shared_files_and_failed_attempt(tmp_path,monkeypatch):
    cfg,source,candidate=chai_config(tmp_path);cache=candidate.parent/'chai_msa_input';cache.mkdir()
    original=cache/'old.aligned.pqt';original.write_bytes(b'prior successful file')
    def fail(env,command,**kwargs):
        (Path(command[-1]).parent/'partial.aligned.pqt').write_bytes(b'failed new attempt')
        raise shell.ToolError('controlled conversion failure')
    monkeypatch.setattr(shell,'run_in_env',fail)
    with pytest.raises(shell.ToolError,match='controlled'):
        screen._prepare_chai_msa_directory(cfg,candidate,{'sequence':'ACDEFG'},str(source))
    assert original.read_bytes()==b'prior successful file'
    assert any(p.read_bytes()==b'failed new attempt' for p in cache.glob('.attempts/*/*.aligned.pqt'))
    assert not list(cache.glob('*/conversion_receipt.json'))


def test_concurrent_chai_candidates_publish_one_verified_cache(tmp_path,monkeypatch):
    cfg,source,candidate=chai_config(tmp_path);calls=[]
    monkeypatch.setattr(shell,'run_in_env',fake_conversion(calls))
    with ThreadPoolExecutor(max_workers=2) as pool:
        dirs=list(pool.map(lambda _:screen._prepare_chai_msa_directory(cfg,candidate,{'sequence':'ACDEFG'},str(source)),range(2)))
    assert dirs[0]==dirs[1] and len(calls)==1


@pytest.mark.parametrize('size',[199,801,2000])
def test_adaptive_PAE_tiles_reconstruct_full_array_and_preserve_UTF8_CRLF(size,tmp_path):
    row=[float(x%32)/2 for x in range(size)];values=[row[:] for _ in range(size)]
    content='REMARK 원본 UTF8\r\nEND\r\n'
    data={'format':'pdb','content':content,'raw_content':content,'pae':{'values':values,'labels':[str(x) for x in range(size)],'unit':'Å'}}
    entry=cloud_results.store_structure(data,tmp_path,'structures/0')
    saved=json.loads((tmp_path/entry['file']).read_bytes());pae=saved['pae']
    assert len(pae['tiles'])<=16 and pae['tile_size']>=200 and len(data['pae']['values'])==size
    reconstructed=np.empty((size,size))
    for tile in pae['tiles']:
        f=tmp_path/tile['file'];assert hashlib.sha256(f.read_bytes()).hexdigest()==tile['sha256']
        value=json.loads(f.read_bytes());a=np.asarray(value['values']);y,x=value['y'],value['x']
        reconstructed[y:y+a.shape[0],x:x+a.shape[1]]=a
    assert np.array_equal(reconstructed,np.asarray(values))
    assert (tmp_path/entry['model_file']).read_bytes()==content.encode('utf-8')
    assert saved['raw_content']==content


def test_cloud_JSON_atomic_failure_does_not_replace_prior_valid_bytes(tmp_path,monkeypatch):
    target=tmp_path/'index.json';target.write_bytes(b'{"prior_valid":true}')
    monkeypatch.setattr(atomic.os,'fsync',lambda *a:(_ for _ in ()).throw(OSError('controlled sync failure')))
    with pytest.raises(OSError,match='controlled'):
        cloud_results.write(target,{'new':True})
    assert target.read_bytes()==b'{"prior_valid":true}' and not list(tmp_path.glob('.publish-*'))


def test_cloud_structure_output_stays_inside_selected_directory(tmp_path):
    destination=tmp_path/'new';destination.mkdir()
    protected=tmp_path/'protected.pdb';protected.write_bytes(b'original')
    with pytest.raises(ValueError,match='상대경로'):
        cloud_results.store_structure({'format':'pdb','content':'END\n'},destination,'../protected')
    assert protected.read_bytes()==b'original' and not list(destination.iterdir())


def test_complex_CLI_config_preserves_pair_symmetry_and_copy_reference_mapping():
    cfg=config.load_config();cfg['run']['mode']='complex'
    cfg['complex']={'proteins':[{'name':'homomer','sequence':'ACDEFG','copies':2,'reference_chains':['X','Y']},{'name':'partner','sequence':'KLMNPQ'}],
                    'primary_pair':['A','C'],'reference_symmetry':False}
    original=copy.deepcopy(cfg['complex']);config._validate(cfg)
    assert cfg['complex']==original


@pytest.mark.parametrize('defect',['absent_chain','bad_symmetry','bad_copy_mapping','bad_recycles'])
def test_complex_CLI_settings_fail_before_compute_for_invalid_new_options(defect,tmp_path):
    cfg=config.load_config();cfg['run']['mode']='complex'
    cfg['complex']={'proteins':[{'sequence':'ACDEFG'},{'sequence':'KLMNPQ'}]}
    if defect=='absent_chain':cfg['complex']['primary_pair']=['A','C']
    elif defect=='bad_symmetry':cfg['complex']['reference_symmetry']='false'
    elif defect=='bad_copy_mapping':cfg['complex']['proteins'][0].update(copies=2,reference_chains=['X'])
    else:cfg['complex']['recycles']=0
    with pytest.raises(ValueError):config._validate(cfg)


def test_complex_API_pair_absent_from_actual_chains_creates_no_registered_input(tmp_path):
    manager=inputs.InputManager(tmp_path)
    try:
        with pytest.raises(ValueError,match='실제 분석 체인'):
            manager.complex({'proteins':[{'sequence':'ACDEFG'},{'sequence':'KLMNPQ'}],'settings':{'primary_pair':['A','C']}})
        assert not list(manager.root.glob('*/platform_input.json'))
    finally:manager.close()
