"""CPU contracts only: ordered chains/MSA, native output axes and old requests."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanocofold.complex import proteins, validate_settings
from nanocofold.complex_comparison import configure, prepare
from nanocofold.colabfold_msa import Collection, promote_monomer
from nanocofold.platform.app import create_app
from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform.inputs import InputManager
from nanocofold.diagnostics import _mode_engines
from nanocofold.mature import generate_library

ENGINES = ['colabfold','boltz2','chai1','opendde']


def test_old_request_and_mutually_exclusive_comparison():
    assert validate_settings({})['engine']=='boltz2'
    assert validate_settings({})['msa']=='empty'
    new=validate_settings({'comparison_engines':ENGINES,'seeds':[42]})
    assert 'engine' not in new and new['msa']=='precomputed'
    assert _mode_engines({'complex':new},'complex')==ENGINES
    for value in ({'comparison_engines':ENGINES,'engine':'boltz2'},
                  {'comparison_engines':[]},{'comparison_engines':['boltz2','boltz2']},
                  {'comparison_engines':ENGINES,'msa_source':'public_api'},
                  {'comparison_engines':['chai1'],'models_per_seed':2}):
        with pytest.raises(ValueError): validate_settings(value)


@pytest.mark.parametrize('sequences',[['ACDEFG','LMNPQR'],['ACDEFG','LMNPQR','STVWYA'],['ACDEFG','ACDEFG']])
def test_exact_cached_msa_order_and_homomer_cardinality(tmp_path,sequences):
    cache=tmp_path/'cache'
    for sequence in set(sequences):
        source=tmp_path/(sequence+'.a3m')
        source.write_text('>query\n'+sequence+'\n>homolog\n'+('A' if sequence[0]!='A' else 'G')+sequence[1:]+'\n')
        promote_monomer(cache,sequence,source)
    entries=proteins([{'sequence':s} for s in sequences])
    cfg={'run':{'mode':'complex','mock':False},'screen':{'colabfold':{'msa_cache_dir':str(cache)}}}
    settings=validate_settings({'comparison_engines':ENGINES,'seeds':[42]})
    cfg['complex']=settings
    applied,monomers=prepare(configure(cfg,settings),tmp_path/'run',None,entries)
    records={p['id']:p['sequence'] for p in entries}
    query=Collection(applied['screen']['colabfold']['msa_dir']).for_query('complex_input',{'parent_id':'complex_input'},records,entries[-1]['id'],applied)
    assert sum(query.cardinality)==len(entries)
    assert list(monomers)==list(records)
    assert all(monomers[p['id']]['sequence']==p['sequence'] for p in entries)
    if len(set(sequences))==1:
        assert query.cardinality==[2] or query.cardinality==(2,)
        assert monomers['A']['sha256']==monomers['B']['sha256']


def test_review_and_drafts_are_authenticated_and_run_no_prediction(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('review must not infer/acquire')
    monkeypatch.setattr('nanocofold.colabfold.predict',forbidden)
    monkeypatch.setattr('nanocofold.engine_comparison._acquire_reviewed_api',forbidden)
    app=create_app(tmp_path,start_workers=False)
    with TestClient(app,base_url='http://localhost') as client:
        assert client.get('/api/draft').status_code==403
        client.headers['x-platform-token']=app.state.token
        saved={'version':1,'fields':{'mode':'screen','advanced':'{"old":true}','seed':'123'}}
        assert client.post('/api/draft',json=saved).json()['saved']
        assert client.get('/api/draft').json()==saved
        spec=client.get('/api/starter-presets').json()
        assert spec['modes']['design']['cloud_settings']['count']==1
        result=client.post('/api/inputs/complex',json={'proteins':[{'sequence':'ACDEFG'},{'sequence':'LMNPQR'}],
            'settings':{'comparison_engines':ENGINES,'seeds':[42],'msa_source':'public_api','allow_public_msa_server':True}})
        assert result.status_code==200,result.text
        assert result.json()['config']['complex']['comparison_engines']==ENGINES
        assert 'engine' not in result.json()['config']['complex']
        assert client.get('/api/jobs').json()['jobs']==[]
        assert client.get('/api/capabilities').json()['complex_comparison'] is True


def test_bounded_variants_only_when_explicitly_requested():
    wt='ACDEFGHIKLMNPQRSTVWY';cdr={'CDR1':(2,3),'CDR2':(7,8),'CDR3':(13,14)}
    old=generate_library(wt,cdr,{})
    cfg={'run':{'seed':42},'mature':{'library':{'max_variants':3}}}
    new=generate_library(wt,cdr,cfg)
    assert len(old)>4 and len(new)==4 and 'WT' in set(new.variant_id)
    assert new.sequence.tolist()==generate_library(wt,cdr,cfg).sequence.tolist()
    assert new.attrs['library_generation']['prepared_variants']==3


def test_shared_starter_spec_bytes():
    public=Path(__file__).parents[1]/'nanocofold/_data/starter-presets.json'
    site=Path(__file__).parents[2]/'nanocofold-site/public/starter-presets.json'
    # The distributable repository is self-contained. Cross-repository parity
    # is an integration check only when that sibling checkout is available.
    spec=json.loads(public.read_text())
    assert spec['schema_version']==1
    assert spec['id']=='small-four-v1'
    assert set(spec['engines'])==set(ENGINES)
    if site.is_file():
        assert public.read_bytes()==site.read_bytes()


def test_parser_error_is_actionable_and_hides_private_paths():
    from nanocofold.platform.errors import public_error
    error=ValueError('PDB 파싱 실패: 입력 파일을 정리하세요: /var/folders/private/uploaded.pdb (Atom N defined twice in residue <Residue GLY>)')
    message=public_error(error)
    assert '구조 파일' in message and '/var/' not in message and 'Residue' not in message
    assert '/mnt/private' not in public_error(ValueError('파일 오류: /mnt/private/input.pdb'))


def test_native_report_tables_have_accessible_labels():
    text=(Path(__file__).parents[1]/'nanocofold/templates/report.html.j2').read_text()
    assert text.count('<caption>')==text.count('<table')
    assert 'aria-live="polite"' in text and 'scope="col"' in text and 'scope="row"' in text


def test_complex_dry_run_cannot_acquire_msa_or_predict(tmp_path,monkeypatch):
    from nanocofold.complex import run
    monkeypatch.setattr('nanocofold.complex_comparison.prepare',lambda *a:pytest.fail('dry run acquired MSA'))
    with pytest.raises(ValueError,match='dry-run'):
        run({'run':{'dry_run':True}},tmp_path,None)


@pytest.mark.parametrize('sequences',[['ACDEFG','LMN'],['ACDEFG','LMN','PQRST'],['ACDEFG','ACDEFG']])
def test_general_colabfold_cli_consumes_ordered_prepared_query_and_full_axes(tmp_path,monkeypatch,sequences):
    import numpy as np
    import pandas as pd
    from types import SimpleNamespace
    from Bio.PDB import PDBIO
    from Bio.PDB.Structure import Structure
    from Bio.PDB.Model import Model
    from Bio.PDB.Chain import Chain
    from Bio.PDB.Residue import Residue
    from Bio.PDB.Atom import Atom
    from nanocofold import colabfold,provenance
    from nanocofold.utils.seq import AA1TO3
    from nanocofold.score_complex import find_prediction_files,load_pae
    cache=tmp_path/'cache'
    for sequence in set(sequences):
        source=tmp_path/(sequence+'.a3m');source.write_text('>query\n'+sequence+'\n>homolog\n'+('G' if sequence[0]=='A' else 'A')+sequence[1:]+'\n');promote_monomer(cache,sequence,source)
    entries=proteins([{'sequence':s} for s in sequences]);chains={p['id']:p['sequence'] for p in entries}
    options=validate_settings({'comparison_engines':ENGINES,'seeds':[42]})
    cfg={'run':{'mode':'complex','seed':42},'complex':options,'screen':{'colabfold':{'msa_cache_dir':str(cache)}}}
    cfg,_=prepare(configure(cfg,options),tmp_path/'run',None,entries)
    observed=[]
    def native_fixture(env,argv,**kwargs):
        query=pd.read_csv(argv[1]).iloc[0];observed.append(query.to_dict())
        assert query.sequence==':'.join(sequences)
        assert Path(query.a3mpath).is_file()
        raw=Path(argv[2]);raw.mkdir(parents=True,exist_ok=True)
        structure=Structure('fixture');model=Model(0);structure.add(model);serial=0
        for axis,(letter,sequence) in enumerate(chains.items()):
            chain=Chain(letter);model.add(chain)
            for n,aa in enumerate(sequence,1):
                serial+=1;residue=Residue((' ',n,' '),AA1TO3[aa],' ');chain.add(residue);residue.add(Atom('CA',np.array([n*3.6,axis*8.0,0.0]),80.,1.,' ',' CA ',serial,element='C'))
        suffix='rank_001_alphafold2_multimer_v3_model_1_seed_042'
        writer=PDBIO();writer.set_structure(structure);writer.save(str(raw/('complex_input_unrelaxed_'+suffix+'.pdb')))
        size=sum(map(len,sequences));(raw/('complex_input_scores_'+suffix+'.json')).write_text(json.dumps({'pae':np.ones((size,size)).tolist(),'plddt':[80.]*size,'iptm':.7,'ptm':.6}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('nanocofold.utils.shell.env_executable',lambda *a,**k:Path('/fixture/colabfold_batch'))
    monkeypatch.setattr('nanocofold.utils.shell.run_in_env',native_fixture)
    monkeypatch.setattr(provenance,'runtime_identity',lambda *a,**k:{'fixture':True})
    frame=colabfold.predict(cfg,tmp_path/'models',{'sequence':sequences[0]},pd.DataFrame([{'design_id':'complex_input','sequence':sequences[-1],'parent_id':'complex_input'}]),chain_records=chains)
    assert len(observed)==1 and 'error' not in frame
    pred=find_prediction_files(Path(frame.iloc[0].pred_dir))
    assert load_pae(pred).shape==(sum(map(len,sequences)),)*2
    assert [s for s in observed[0]['sequence'].split(':')]==sequences
