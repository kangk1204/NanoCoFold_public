"""The prediction manifest describes resolved engine input, including cache misses."""
import pandas as pd
import pytest
from nanocofold import screen

@pytest.mark.parametrize('target,binder,overrides,expected',[
 ('empty','empty',None,'empty'),
 ('target.a3m','empty',None,'partial_precomputed'),
 ('target.a3m','binder.a3m',None,'precomputed'),
 ('empty','empty',{'A':'target.a3m','B':'binder.a3m'},'precomputed'),
 ('target.a3m','binder.a3m',{'A':'empty','B':'empty'},'empty'),
])
def test_boltz_manifest_uses_resolved_msa_not_config(tmp_path,monkeypatch,target,binder,overrides,expected):
 captured=[]
 def predict(cfg,directory,chains,target_msa,binder_letter,hotspots,dry_run,binder_msa,chain_msas=None):
  captured.extend(screen._boltz_chain_inputs(chains,target_msa,binder_letter,binder_msa,chain_msas))
 monkeypatch.setattr(screen,'_predict_boltz2',predict)
 cfg={'run':{'seed':42},'screen':{'engine':'boltz2','restraint':{'enabled':False}},'structure':{'msa':{'backend':'cache'}}}
 rows=screen.predict_complexes(cfg,tmp_path/'models',{'sequence':'ACDE','target_msa':target,'binder_msa':binder,'chain_msas':overrides},pd.DataFrame([{'design_id':'WT','sequence':'FGHI'}]),engine='boltz2')
 assert rows.iloc[0]['msa_used']==expected
 saved=pd.read_csv(tmp_path/'models/predictions.csv')
 assert saved.iloc[0]['msa_used']==expected
 actual=[item['msa']!='empty' for item in captured]
 assert ('precomputed' if all(actual) else 'partial_precomputed' if any(actual) else 'empty')==expected

def test_auxiliary_chain_without_msa_is_not_recorded_as_full():
 assert screen._boltz_msa_mode({'A':'AC','B':'DE','C':'FG'},'target.a3m','C','binder.a3m')=='partial_precomputed'
