"""CPU correctness/operation-count contracts; these fixtures are not inference."""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd
import pytest
from Bio.PDB import PDBIO
from Bio.PDB.Atom import Atom
from Bio.PDB.Chain import Chain
from Bio.PDB.Model import Model
from Bio.PDB.Residue import Residue
from Bio.PDB.Structure import Structure

from nanocofold import complex as cx, rank_select as rs, score_complex as sc
from nanocofold.config import load_config
from nanocofold.utils import pdb_io


def fixture_prediction(tmp_path, offsets=(0., 2., 4.)):
    structure = Structure('fixture'); model = Model(0); structure.add(model)
    serial = 0
    for letter, offset in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', offsets):
        chain = Chain(letter); model.add(chain)
        for n in range(1, 5):
            residue = Residue((' ', n, ' '), 'ALA', ' '); chain.add(residue)
            for atom_name, delta in [('CA', 0.), ('CB', .5)]:
                serial += 1
                residue.add(Atom(atom_name, np.array([n*3.8, offset+delta, .2*n*n]), 80., 1., ' ', atom_name, serial, element='C'))
    path = tmp_path/'native.pdb'; writer=PDBIO(); writer.set_structure(structure); writer.save(str(path))
    pae=tmp_path/'pae.npz'; np.savez(pae, pae=np.full((4*len(offsets), 4*len(offsets)),4.))
    confidence=tmp_path/'confidence.json'; confidence.write_text(json.dumps({'iptm':.8}))
    return sc.PredictionFiles(path, pae_npz=pae, confidence_json=confidence), structure


def cfg():
    result=load_config(); result['score']['ipsae']['use_official']=False
    return result


def test_multichain_parses_inputs_once_and_reuses_single_chain_sasa(tmp_path, monkeypatch):
    pred,_=fixture_prediction(tmp_path); original=pred.cif.read_bytes(); config=cfg()
    entries=cx.proteins([{'name':str(i),'sequence':'AAAA'} for i in range(3)])
    counts={}
    for owner,key in [(pdb_io,'load_structure'),(pdb_io,'parse_plddt'),(pdb_io,'parse_atom_plddt'),
                      (sc,'load_pae'),(sc,'load_confidence'),(pdb_io,'compute_residue_sasa')]:
        method=getattr(owner,key)
        def wrapped(*args,_method=method,_key=key,**kwargs):
            counts[_key]=counts.get(_key,0)+1; return _method(*args,**kwargs)
        monkeypatch.setattr(owner,key,wrapped)
    result=cx.structure_data(pred,entries,config)
    assert len(result['pair_metrics'])==3
    assert counts=={'load_structure':1,'parse_plddt':1,'parse_atom_plddt':2,'load_pae':1,
                    'load_confidence':1,'compute_residue_sasa':6}
    assert pred.cif.read_bytes()==original


def test_cached_pair_metrics_equal_independent_pair_calculations(tmp_path):
    pred,_=fixture_prediction(tmp_path); config=cfg(); context=sc.prepare_scoring_context(pred)
    for a,b in itertools.combinations('ABC',2):
        cached=sc.score_complex(pred.cif,a,b,[],config,pred=pred,context=context)
        fresh=sc.score_complex(pred.cif,a,b,[],config,pred=pred)
        for key in cached:
            if isinstance(cached[key],float) and np.isnan(cached[key]):
                assert np.isnan(fresh[key])
            else: assert cached[key]==fresh[key],key


def test_no_contact_sasa_is_skipped_without_inventing_zero_area(tmp_path,monkeypatch):
    pred,structure=fixture_prediction(tmp_path,offsets=(0.,3.))
    # Contact cutoff1A excludes this pair, while solvent spheres still overlap.
    only_a=structure.copy(); only_a[0].detach_child('B')
    only_b=structure.copy(); only_b[0].detach_child('A')
    area=sum(pdb_io.compute_residue_sasa(only_a).values())+sum(pdb_io.compute_residue_sasa(only_b).values())-sum(pdb_io.compute_residue_sasa(structure).values())
    assert area>0
    monkeypatch.setattr(pdb_io,'compute_residue_sasa',lambda *a,**k:pytest.fail('No-contact SASA must not run'))
    config=cfg();config['score']['contact_cutoff']=1.
    metrics=sc.score_complex(pred.cif,'A','B',[],config,pred=pred)
    assert metrics['n_contacts']==0 and metrics['interface_assessment_status']=='no_contacts'
    assert np.isnan(metrics['buried_sasa']) and np.isnan(metrics['buried_sasa_per_contact'])
    assert metrics['buried_sasa_status']=='not_assessed_no_contacts'


def ranking_frame():
    return pd.DataFrame({'design_id':['good','measured_lower','no_contact'],'sequence':['CAAAAAWGQG']*3,
                         'n_contacts':[2,2,0],'interface_pae':[2.,10.,np.nan], 'ipsae':[.8]*3,
                         'final_eligible':[False]*3})


def test_no_contact_missing_interface_never_beats_measured_rows_or_fallback():
    frame=ranking_frame(); original=frame.copy(deep=True)
    config={'rank':{'weights':{'interface_pae':-1.,'ipsae':.1},'fallback_to_ineligible':True,
                    'final_n':3,'cdr3':{'per_cluster':3}}}
    ranked=rs.rank_candidates(frame,config);held=ranked.set_index('design_id').loc['no_contact']
    assert pd.isna(held['rank']) and pd.isna(held['composite_score'])
    assert held['ranking_status']=='held' and held['ranking_unavailable_reason']=='interface_metrics_unavailable'
    assert ranked.dropna(subset=['rank'])['design_id'].tolist()==['good','measured_lower']
    selected=rs.select_final(ranked,config,pre_ranked=True)
    assert selected['design_id'].tolist()==['good','measured_lower']
    pd.testing.assert_frame_equal(frame,original)


def test_contacting_but_weighted_interface_unavailable_is_held():
    frame=ranking_frame();frame['n_contacts']=2
    result=rs.rank_candidates(frame,{'rank':{'weights':{'interface_pae':-1.}}}).set_index('design_id')
    assert pd.isna(result.loc['no_contact','composite_score'])
    assert result.loc['no_contact','ranking_unavailable_reason']=='interface_metrics_unavailable'


def test_old_preranked_no_contact_score_cannot_be_selected_even_under_fallback():
    frame=ranking_frame();frame['rank']=[2,3,1];frame['composite_score']=[.2,.1,.9];frame['cluster_id']='cluster_01'
    selected=rs.select_final(frame,{'rank':{'weights':{},'fallback_to_ineligible':True,'final_n':3,
                                          'cdr3':{'per_cluster':3}}},pre_ranked=True)
    assert set(selected['design_id'])=={'good','measured_lower'}


def test_nullable_missing_weighted_interface_is_held_without_boolean_ambiguity():
    frame=pd.DataFrame({'design_id':['known','unassessed'],'sequence':['CAAAAAWGQG']*2,
                        'n_contacts':pd.Series([2,pd.NA],dtype='Int64'),
                        'interface_pae':pd.Series([4.,pd.NA],dtype='Float64')})
    ranked=rs.rank_candidates(frame,{'rank':{'weights':{'interface_pae':-1.}}}).set_index('design_id')
    assert ranked.loc['known','ranking_status']=='ranked'
    assert pd.isna(ranked.loc['unassessed','rank'])
    assert ranked.loc['unassessed','ranking_unavailable_reason']=='interface_metrics_unavailable'


@pytest.mark.parametrize('value,expected',[(1.,100.),(1.2,1.2),(1.5,1.5),(.8,80.)])
def test_confidence_scaling_unit_endpoint_and_low_100_scale(tmp_path,value,expected):
    _,structure=fixture_prediction(tmp_path)
    for atom in structure.get_atoms():atom.bfactor=value
    for values in [pdb_io.parse_plddt(structure),pdb_io.parse_atom_plddt(structure,'ca'),pdb_io.parse_atom_plddt(structure,'cb')]:
        assert set(values.values())=={expected}


def test_modal_cdr3_is_greedy_representative_with_deterministic_ties():
    loops=['AAAAA','AAAAC','AAAAC','AAAAC','AAACC']
    frame=pd.DataFrame({'design_id':list('abcde'),'sequence':['C'+x+'WGQG' for x in loops],'cdr3_sequence':loops})
    assert rs.cluster_cdr3(frame,identity=.8).nunique()==1
    first=dict(zip(frame['design_id'],rs.cluster_cdr3(frame,identity=.8)))
    shuffled=frame.sample(frac=1.,random_state=7)
    assert dict(zip(shuffled['design_id'],rs.cluster_cdr3(shuffled,identity=.8)))==first


def test_primary_pair_and_extrema_disclose_partial_assessment():
    pairs=[{'chain_a':'A','chain_b':'B','ipsae':0.,'interface_plddt':None,'interface_pae':None},
           {'chain_a':'A','chain_b':'C','ipsae':.9,'interface_plddt':80.,'interface_pae':3.},
           {'chain_a':'B','chain_b':'C','ipsae':None}]
    default=cx.pair_summary(pairs,{})
    assert default['ipsae']==0 and default['ipsae_selection']=='first_pair_in_reviewed_chain_order'
    selected=cx.pair_summary(pairs,{'primary_pair':['C','A']})
    assert selected['ipsae']==.9 and selected['interface_pae']==3 and selected['primary_pair']==['A','C']
    assert selected['max_pair_ipsae']==.9 and selected['min_pair_ipsae']==0
    assert selected['pair_ipsae_assessed_count']==2 and selected['pair_ipsae_total_count']==3
    assert not selected['pair_ipsae_all_assessed']
    with pytest.raises(ValueError):cx.pair_summary(pairs,{'primary_pair':['A','D']})


@pytest.mark.parametrize('primary',[['A','C'],['B','C']])
def test_primary_pair_payload_selectors_roles_and_record_scope_agree(tmp_path,primary):
    pred,_=fixture_prediction(tmp_path);original=pred.cif.read_bytes()
    pae=np.full((12,12),4.);pae[:4,8:]=pae[8:,:4]=1.;pae[4:8,8:]=pae[8:,4:8]=8.
    np.savez(pred.pae_npz,pae=pae)
    entries=cx.proteins([{'name':str(i),'sequence':'AAAA'} for i in range(3)])
    config=cfg();base=cx.structure_data(pred,entries,config)
    assert (base['target_chain'],base['binder_chain'])==('A','B')
    config['complex']={'primary_pair':primary};data=cx.structure_data(pred,entries,config)
    record=cx.pair_summary(data['pair_metrics'],config['complex'])
    assert [data['target_chain'],data['binder_chain']]==primary==[record['ipsae_chain_a'],record['ipsae_chain_b']]
    assert data['ipsae']==record['ipsae'] and data['ipsae']!=base['ipsae']
    roles={chain['id']:chain['role'] for chain in data['chains']}
    assert roles[primary[0]]=='target' and roles[primary[1]]=='binder'
    assert all(role=='context' for chain,role in roles.items() if chain not in primary)
    assert data['content']==base['content'] and data['pae']==base['pae'] and data['pair_metrics']==base['pair_metrics']
    assert pred.cif.read_bytes()==original


@pytest.mark.parametrize('mapping',[['X'],['X','X'],'XY',None])
def test_copies_reference_mapping_is_explicit_and_unambiguous(mapping):
    entry={'name':'copies','sequence':'AAAA','copies':2}
    if mapping is None:entry['reference_chain']='X'
    else:entry['reference_chains']=mapping
    with pytest.raises(ValueError,match='reference_chain'):cx.proteins([entry])


def test_reference_chains_expand_in_copy_order():
    entries=cx.proteins([{'name':'copies','sequence':'AAAA','copies':2,'reference_chains':['X','Y']}])
    assert [(p['id'],p['reference_chain'],p['copy_index']) for p in entries]==[('A','X',0),('B','Y',1)]


def test_exact_homomer_permutation_and_anchor_scope_preservation(tmp_path):
    pred,reference=fixture_prediction(tmp_path,offsets=(0.,9.))
    swapped=reference.copy()
    for a,b in zip(swapped[0]['A'].get_atoms(),reference[0]['B'].get_atoms()):a.set_coord(b.coord.copy())
    for a,b in zip(swapped[0]['B'].get_atoms(),reference[0]['A'].get_atoms()):a.set_coord(b.coord.copy())
    entries=cx.proteins([{'name':'homomer','sequence':'AAAA','copies':2}])
    original=pred.cif.read_bytes()
    result,_=cx.reference_comparison(swapped,entries,pred.cif,{})
    assert result['available'] and result['symmetry_search_complete']
    assert result['chain_permutation_map']=={'A':'B','B':'A'}
    assert result['all_chain_ca_rmsd_angstrom']<1e-5 and result['rmsd']<1e-5
    assert result['rmsd_scope']=='anchor_chain_CA'
    assert pred.cif.read_bytes()==original
    fixed=reference.copy()
    for a,b in zip(fixed[0]['A'].get_atoms(),reference[0]['B'].get_atoms()):a.set_coord(b.coord.copy())
    for a,b in zip(fixed[0]['B'].get_atoms(),reference[0]['A'].get_atoms()):a.set_coord(b.coord.copy())
    result,_=cx.reference_comparison(fixed,entries,pred.cif,{'reference_symmetry':False})
    assert result['available'] and result['chain_permutation_map']=={'A':'A','B':'B'}
    assert result['symmetry_search_status']=='explicit_fixed_mapping'
    assert result['all_chain_ca_rmsd_angstrom']>1e-3


def test_permuted_reference_contacts_use_selected_mapping(tmp_path):
    pred,reference=fixture_prediction(tmp_path,offsets=(0.,3.,9.))
    reference_path=tmp_path/'reference.pdb';writer=PDBIO();writer.set_structure(reference);writer.save(str(reference_path))
    swapped=reference.copy()
    for a,b in zip(swapped[0]['A'].get_atoms(),reference[0]['C'].get_atoms()):a.set_coord(b.coord.copy())
    for a,b in zip(swapped[0]['C'].get_atoms(),reference[0]['A'].get_atoms()):a.set_coord(b.coord.copy())
    writer.set_structure(swapped);writer.save(str(pred.cif));original=pred.cif.read_bytes()
    entries=cx.proteins([{'name':'homomer','sequence':'AAAA','copies':3}])
    config=cfg();config['complex']={'reference_path':str(reference_path)}
    data=cx.structure_data(pred,entries,config)
    assert data['reference']['chain_permutation_map']=={'A':'C','B':'B','C':'A'}
    assert data['reference']['reference_contacts_5A']>0 and data['reference']['fnat_5A']==1
    assert pred.cif.read_bytes()==original


def test_large_exact_permutation_search_is_truthfully_unavailable(tmp_path):
    pred,structure=fixture_prediction(tmp_path,offsets=tuple(3.*i for i in range(7)))
    before=[a.coord.copy() for a in structure.get_atoms()]
    entries=cx.proteins([{'name':'copies','sequence':'AAAA','copies':7}])
    result,returned=cx.reference_comparison(structure,entries,pred.cif,{})
    assert returned is structure and not result['available'] and not result['symmetry_search_complete']
    assert result['symmetry_search_status']=='exact_search_budget_exceeded'
    assert result['permutations_required']=='5040'
    assert all(np.array_equal(a.coord,b) for a,b in zip(structure.get_atoms(),before))


def test_protein_msa_preparation_has_no_fake_vhh_parent(tmp_path,monkeypatch):
    from nanocofold import complex_comparison as cc
    from nanocofold.colabfold_msa import promote_monomer,Collection
    from nanocofold.state import RunState
    cache=tmp_path/'cache'
    for sequence in ['AAAA','GGGG']:
        path=tmp_path/(sequence+'.a3m');path.write_text('>query\n'+sequence+'\n>homolog\n'+sequence[:-1]+'Q\n')
        promote_monomer(cache,sequence,path,provenance={'provider':'fixture'})
    config=cfg();config['run']['mode']='complex';config['screen']['colabfold'].update(msa_source='cache',msa_cache_dir=str(cache))
    entries=cx.proteins([{'name':'copies','sequence':'AAAA','copies':2},{'name':'other','sequence':'GGGG'}])
    monkeypatch.setattr(cc.engine_comparison,'prepare_msa',lambda *a,**k:pytest.fail('VHH preparation must not be used'))
    root=tmp_path/'run';applied,monomers=cc.prepare(config,root,RunState(root),entries)
    assert list(monomers)==['A','B','C'] and monomers['A']['sha256']==monomers['B']['sha256']
    manifest=json.loads((root/'01_msa/comparison/manifest.json').read_text())
    assert manifest['parents']=={} and not manifest['candidate_selection_applicable']
    assert list(manifest['protein_chains'])==['A','B','C']
    collection=Collection(root/'01_msa/comparison')
    query=collection.for_query('complex_input',{},dict((p['id'],p['sequence']) for p in entries),'C',applied)
    assert query.sequences==('AAAA','GGGG') and query.cardinality==(2,1)
