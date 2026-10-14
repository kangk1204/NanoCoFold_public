"""Conservative complex reuse: config options and consumed MSA bytes are distinct."""
import copy,hashlib,json
from pathlib import Path
import pytest
from nanocofold import colabfold_msa
from nanocofold.platform import result_catalog as catalog

GPU='GPU-12345678-1234-1234-1234-123456789abc'


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(catalog,'_package_identity',lambda:{'source_sha256':'a'*64,'version':'fixture'})
    monkeypatch.setattr(catalog,'_runtime_identity',lambda *a:{'sha256':'b'*64,'weights':[{'sha256':'c'*64}]})
    monkeypatch.setattr(catalog,'_target_msa_identity',lambda *a:pytest.fail('complex consumed VHH target preparation MSA'))


def cfg(cache,source='cache',comparison=True):
    settings={'comparison_engines':['boltz2'],'msa':'precomputed','msa_source':source,'allow_public_msa_server':source=='public_api'} if comparison else {'engine':'boltz2','msa':'empty'}
    settings['proteins']=[{'name':'one','sequence':'ACDEFG'},{'name':'two','sequence':'KLMNPQ'}]
    return {'run':{'mode':'complex','mock':False,'seed':42},'complex':settings,
            'screen':{'colabfold':{'msa_cache_dir':str(cache)}},'structure':{'provider':'none'}}


def cache_entry(tmp_path,cache,sequence,homolog):
    source=tmp_path/(sequence+'.a3m');source.write_text(f'>query\n{sequence}\n>homolog\n{homolog}\n')
    return colabfold_msa.promote_monomer(cache,sequence,source)


def populate(tmp_path):
    cache=tmp_path/'monomer_cache'
    first=cache_entry(tmp_path,cache,'ACDEFG','ACDEYG')
    cache_entry(tmp_path,cache,'KLMNPQ','KLMNAQ')
    return cache,first


def test_complex_verified_consumed_cache_bytes_change_digest_with_same_config(tmp_path,pinned):
    cache,entry=populate(tmp_path);value=cfg(cache);original=copy.deepcopy(value)
    before=catalog.build_identity(tmp_path,value,GPU)
    assert before['reusable'] and before['document']['msa']['mode']=='frozen_monomer_cache'
    path=Path(entry['a3m']);path.write_text(path.read_text()+'>extra\nACDEWG\n')
    receipt=path.parent/'receipt.json';record=json.loads(receipt.read_bytes())
    record.update(a3m_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),homolog_count=2)
    receipt.write_text(json.dumps(record))
    colabfold_msa.cached_monomer(cache,'ACDEFG')
    after=catalog.build_identity(tmp_path,value,GPU)
    assert after['reusable'] and after['digest']!=before['digest'] and value==original


@pytest.mark.parametrize('source',['public_api','local_mmseqs2'])
def test_complex_online_MSA_remains_unpinned_even_with_valid_cache_and_supplied_collection(tmp_path,pinned,source):
    cache,_=populate(tmp_path);value=cfg(cache,source)
    value['screen']['colabfold'].update(msa_dir=str(tmp_path/'unused'),msa_source='cache')
    before=catalog.build_identity(tmp_path,value,GPU)
    assert not before['reusable'] and before['document']['msa']['mode']==source
    assert before['document']['msa']['alignments_pinned'] is False
    assert any('mutable/unpinned' in reason for reason in before['reasons'])


def test_complex_empty_MSA_reusable_without_target_or_predictor_cache(tmp_path,pinned):
    value=cfg(tmp_path/'missing',comparison=False)
    value['structure']={'provider':'chai1','msa':{'backend':'mmseqs2_remote'}}
    identity=catalog.build_identity(tmp_path,value,GPU)
    assert identity['reusable'] and identity['document']['msa']=={'mode':'empty'}


@pytest.mark.parametrize('corruption',['alignment','receipt','missing_actual_chain'])
def test_complex_cache_corruption_or_missing_consumed_chain_never_reusable(tmp_path,pinned,corruption):
    cache,entry=populate(tmp_path)
    if corruption=='alignment':Path(entry['a3m']).write_text('>query\nACDEFG\n>changed\nACDEWG\n')
    elif corruption=='receipt':(Path(entry['a3m']).parent/'receipt.json').write_text('{}')
    value=cfg(cache)
    if corruption=='missing_actual_chain':value['complex']['proteins'][1]['sequence']='KLMNPR'
    identity=catalog.build_identity(tmp_path,value,GPU)
    assert not identity['reusable'] and any('MSA' in reason for reason in identity['reasons'])


def test_complex_full_config_options_already_part_of_identity(tmp_path,pinned):
    value=cfg(tmp_path/'absent',comparison=False)
    before=catalog.build_identity(tmp_path,value,GPU)
    altered=copy.deepcopy(value);altered['complex']['future_scientific_option']={'threshold':0.123}
    assert catalog.build_identity(tmp_path,altered,GPU)['digest']!=before['digest']
    altered=copy.deepcopy(value);altered['complex']['msa_source']='cache'
    assert catalog.build_identity(tmp_path,altered,GPU)['digest']!=before['digest']
    assert before['document']['config']['complex']==value['complex']


def test_complex_cache_digest_covers_receipt_provenance_even_when_alignment_unchanged(tmp_path,pinned):
    cache,entry=populate(tmp_path);value=cfg(cache)
    before=catalog.build_identity(tmp_path,value,GPU)
    path=Path(entry['a3m']).parent/'receipt.json';record=json.loads(path.read_bytes());record['source_provenance']={'sealed_source_version':'new-fixture'}
    path.write_text(json.dumps(record))
    after=catalog.build_identity(tmp_path,value,GPU)
    assert before['reusable'] and after['reusable'] and before['digest']!=after['digest']
