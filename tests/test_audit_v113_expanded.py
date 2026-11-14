"""New findings added to the independent report at 13:23; CPU-only regressions."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from nanocofold.config import load_config, dump_resolved


@pytest.mark.parametrize('setting', [
    'screen.msa_mode=bogus', 'structure.chai1.msa_mode=local', 'structure.msa.backend=nope',
    'mature.cdr.source=configg', 'mature.cdr.scheme=wrong', 'mature.prefilter.mode=bogus',
    'expression.order_format=wrong', 'screen.restraint.connection_type=contact',
    'screen.restraint.max_distance=0', 'screen.boltz2_pocket_max_distance=0',
    'specificity.stage1_n=0', 'specificity.stage2_n=0', 'specificity.final_n=0', 'specificity.budget_hours=0',
    'hotspots.auto.residues_per_set=[6,3]',
    'structure.provider=mock', 'mature.prefilter.mode=mock', 'score.boltz2_cpu_fp32=true',
])
def test_invalid_settings_fail_before_execution(setting):
    with pytest.raises(ValueError): load_config(overrides=[setting])


def test_cdr_and_provider_case_cannot_bypass_their_validation():
    cfg=load_config(overrides=['mature.cdr.source=Config', 'structure.provider=CHAI1'])
    assert cfg['mature']['cdr']['source']=='config' and cfg['structure']['provider']=='chai1'
    with pytest.raises(ValueError): load_config(overrides=['structure.provider=CHAI1','structure.chai1.num_trunk_recycles=50'])


def test_yaml_errors_name_the_source_and_unknown_options_survive_in_state(tmp_path,capsys):
    from nanocofold import cli
    source=tmp_path/'broken.yaml';source.write_text('run: [\n')
    with pytest.raises(ValueError,match='broken.yaml'): load_config(source)
    args=SimpleNamespace(set=['screen.typo=5'],mode='screen',command='run',config=None,run_dir=str(tmp_path/'run'),run_id=None,mock=True,dry_run=False)
    cfg=cli._load(args);_,state=cli._open_run(cfg,args)
    assert any('screen.typo' in w for w in state.warnings)
    assert '[경고]' in capsys.readouterr().out


def test_replacing_weight_mapping_warns_without_changing_requested_weights():
    with pytest.warns(UserWarning,match='가중치 목록 전체를 교체'):
        cfg=load_config(overrides=['rank.weights={ipsae: 1.0}'])
    assert cfg['rank']['weights']=={'ipsae':1.0}


def test_missing_retention_metric_cannot_delete_successful_models():
    from nanocofold.compact_screen import _retain
    ids=['WT','a','b','c']
    state={'completed':ids,'metrics':{key:{'iptm':None} for key in ids},'predictions':{key:{'pred_dir':key} for key in ids}}
    assert _retain(state,ids,'iptm',1,'design_id')==set(ids)
    assert state['retention_status']=='metric_unassessed_cohort_wide'
    state['metrics']['a']['iptm']=.9
    assert _retain(state,ids,'iptm',1,'design_id')==set(ids)


def test_cascade_does_not_silently_reduce_budget_when_all_metrics_are_missing():
    from nanocofold.cascade import select_candidates
    cfg=load_config();base=pd.DataFrame([{'design_id':x,'sequence':'ACDE'} for x in ['WT','a','b']])
    scores=base.assign(iptm=np.nan,ipsae=.7,error=None)
    with pytest.raises(ValueError,match='모든 성공 후보에서 미평가'): select_candidates(base,scores,cfg)
    cfg['screen']['cascade']['ranking_metric']='ipsae'
    assert select_candidates(base,scores,cfg).selected_for_boltz.all()


def test_native_confidence_cannot_fill_missing_pair_metrics_or_change_units(tmp_path):
    from nanocofold.mock import make_mock_boltz_output
    from nanocofold.platform.results import ResultReader
    cfg=load_config();cfg['run']['mode']='screen';dump_resolved(cfg,tmp_path/'run_config.yaml')
    directory=tmp_path/'02_screen/c';directory.mkdir(parents=True)
    for i in range(12): make_mock_boltz_output(directory,'c','ACDE','ACDE',[],model_idx=i)
    pd.DataFrame([{'design_id':'c','sequence':'ACDE','engine':'boltz2','pred_dir':'02_screen/c'}]).to_csv(tmp_path/'02_screen/predictions.csv',index=False)
    (tmp_path/'03_score').mkdir()
    pd.DataFrame([{'design_id':'c','sequence':'ACDE','engine':'boltz2','cif_path':'02_screen/c/c_model_0.cif','iptm':np.nan,'complex_plddt':88.,'iptm_scope':'selected_target_binder_pair'}]).to_csv(tmp_path/'03_score/scored_candidates.csv',index=False)
    rows=ResultReader(tmp_path).observations()['rows']
    assert len(rows)==12
    assert [row['model_index'] for row in rows]==list(range(12))
    assert all(row['iptm'] is None for row in rows)
    assert rows[0]['complex_plddt']==88. and all(row['complex_plddt'] is None for row in rows[1:])
    assert all(row['complex_iptm']>0 and row['native_confidence']['complex_plddt']<1 for row in rows)
    assert rows[1]['iptm_scope'] is None


@pytest.mark.parametrize('engine',['chai1','boltz2','colabfold','opendde'])
def test_engine_specific_mock_preserves_multichain_axes_and_all_samples(tmp_path,engine):
    from nanocofold.mock import make_mock_engine_output
    from nanocofold import score_complex,colabfold
    from nanocofold.utils import pdb_io
    chains={'A':'ACDEFG','B':'KLMN','C':'QRSTV'}
    make_mock_engine_output(tmp_path,'v',chains,[],engine=engine,samples=12)
    files=sorted([*tmp_path.glob('*.cif'),*tmp_path.glob('*.pdb')],key=score_complex.model_order)
    assert len(files)==12
    for index in range(12):
        pred=score_complex.find_prediction_files(tmp_path,model_index=index)
        assert pred.source==engine and pred.sample_index==index
        assert score_complex.load_pae(pred).shape==(15,15)
        assert {c.id:pdb_io.chain_sequence(c) for c in next(pdb_io.load_structure(pred.cif).get_models())}==chains
        if engine=='chai1': assert score_complex.chai_chain_axis(pred)[1] is None
    if engine=='colabfold': assert colabfold._valid_outputs(tmp_path,chains,12)


def test_complex_mapping_keeps_cpu_flags_and_named_sampling_steps():
    from nanocofold.complex_comparison import effective_config
    from nanocofold.complex import validate_settings
    from nanocofold.utils.tool_args import boltz_extra_args
    cfg=load_config(overrides=['score.boltz2_cpu_fp32=true','score.boltz2_extra_args=[--accelerator,cpu]'])
    applied=effective_config(cfg,validate_settings({'engine':'boltz2','diffusion_steps':123}),'boltz2',7)
    assert boltz_extra_args(applied)==['--accelerator','cpu','--sampling_steps','123']
    with pytest.raises(ValueError,match='managed option'):
        load_config(overrides=['score.boltz2_sampling_steps=50','score.boltz2_extra_args=[--sampling_steps,"100"]'])


def test_spreadsheet_xml_retains_float_roundtrip_precision():
    import io,zipfile
    from openpyxl import Workbook,load_workbook
    from nanocofold.utils.spreadsheet import deterministic_xlsx
    book=Workbook();value=0.12345678901234568;book.active.append([value])
    payload=deterministic_xlsx(book)
    assert repr(value).encode() in zipfile.ZipFile(io.BytesIO(payload)).read('xl/worksheets/sheet1.xml')
    assert load_workbook(io.BytesIO(payload)).active['A1'].value==value


@pytest.mark.parametrize('setting', [
    'screen.colabfold.num_models=0','screen.colabfold.num_models=1000000',
    'screen.colabfold.num_seeds=0','screen.colabfold.num_recycles=0',
    'screen.colabfold.msa_max_sequences=10001','design.boltzgen.sampling_steps=1000000',
    'design.boltzgen.inverse_fold_num_sequences=0','mature.library.size=0',
    'mature.library.min_mutations=9','structure.chai1.num_trunk_recycles=50',
])
def test_inactive_engine_options_are_also_checked(setting):
    with pytest.raises(ValueError): load_config(overrides=[setting])


def test_native_complex_review_has_actual_seed_and_engine_receipts(tmp_path):
    from nanocofold.platform.inputs import InputManager
    result=InputManager(tmp_path).complex({
        'proteins':[{'id':'A','name':'target','sequence':'ACDEFGHIKLMN'},
                    {'id':'B','name':'partner','sequence':'KLMNPQRSTVWY'}],
        'settings':{'comparison_engines':['colabfold','boltz2','chai1','opendde'],
                    'seeds':[777], 'msa':'precomputed','msa_source':'cache',
                    'models_per_seed':1,'recycles':3,'diffusion_steps':200},
        'config':{'report':{'top_n_table':7},'score':{'contact_cutoff':9.0},'rank':{'final_n':5}},
    })
    cfg=result['config']
    assert cfg['run']['seed']==777 and cfg['report']['top_n_table']==7
    assert cfg['score']['contact_cutoff']==9. and cfg['rank']['final_n']==5
    rows={row['engine']:row for row in result['review']['engine_conditions']}
    assert set(rows)=={'colabfold','boltz2','chai1','opendde'}
    assert all(row['seed']==row['effective']['seed']==777 for row in rows.values())
    assert rows['chai1']['effective']['num_recycles']==3
    assert rows['chai1']['effective']['diffusion_timesteps']==200
    assert rows['boltz2']['effective']['sampling_steps']==200
    assert rows['opendde']['effective']['cycles']==3


def test_starter_presets_match_documented_recycling_and_diffusion_steps():
    data=json.loads((Path(__file__).parents[1]/'nanocofold/_data/starter-presets.json').read_text())
    for value in data['modes'].values():
        cfg=value['native_override']
        assert cfg['screen']['opendde']['cycles']==3
        assert cfg['screen']['opendde']['steps']==200
        assert cfg['score']['boltz2_sampling_steps']==200


def test_design_dry_run_keeps_preview_alive_and_original_files_intact(tmp_path):
    from nanocofold import cli
    run=tmp_path/'run';target=run/'01_target';target.mkdir(parents=True)
    protected=target/'existing.txt';protected.write_bytes(b'keep-original')
    cfg=load_config(overrides=['run.mode=design','target.sequence=ACDEFGHIKLMNPQRSTVWYACDEFGHIK','target.hotspots=[5]','design.num_designs=1'])
    dump_resolved(cfg,run/'run_config.yaml')
    before=(run/'run_config.yaml').read_bytes()
    code=cli.main(['design','--dry-run','--run-dir',str(run),
                   '--set','target.sequence=ACDEFGHIKLMNPQRSTVWYACDEFGHIK',
                   '--set','target.hotspots=[5]','--set','design.num_designs=1'])
    assert code==0
    assert protected.read_bytes()==b'keep-original'
    assert (run/'run_config.yaml').read_bytes()==before


def test_explicit_empty_motif_list_keeps_legacy_behavior_and_warns():
    with pytest.warns(UserWarning,match='이 모티프 검사가 꺼집니다'):
        cfg=load_config(overrides=['developability.deamidation=[]'])
    assert cfg['developability']['deamidation']==[]


def test_boltz_named_sampling_steps_invalidate_reuse_and_preserve_prior_attempt(tmp_path,monkeypatch):
    from nanocofold import screen,mock
    cfg=load_config(); candidate=tmp_path/'candidate'; calls=[]
    def predict_stub(env,argv,**kwargs):
        calls.append(argv)
        output=Path(argv[argv.index('--out_dir')+1])/'predictions/candidate'
        mock.make_mock_boltz_output(output,'candidate','ACDEFG','ACD',[1])
    monkeypatch.setattr(screen.shell,'env_executable',lambda env,name,dry_run=False:name)
    monkeypatch.setattr(screen.shell,'run_in_env',predict_stub)
    cfg['score']['boltz2_sampling_steps']=50
    def predict():
        screen._predict_boltz2(cfg,candidate,{'A':'ACDEFG','B':'ACD'},'empty','B',[1],False)
        return json.loads((candidate/'boltz_input.json').read_text())
    first=predict(); assert len(calls)==1
    assert predict()==first and len(calls)==1
    cfg['score']['boltz2_sampling_steps']=200
    second=predict(); assert len(calls)==2 and second['fingerprint']!=first['fingerprint']
    assert second['inputs']['extra_args'][-2:]==['--sampling_steps','200']
    assert (candidate/'.history').is_dir()


def test_direct_target_boltz_arguments_keep_the_managed_option_guard(tmp_path):
    from nanocofold import target_prep
    for args in [['--out_dir=other'],['--use_msa_server']]:
        with pytest.raises(ValueError,match='managed'):
            target_prep._fold_boltz2(load_config(),tmp_path,'ACD',None,'empty',args,True)
