"""An installed ESM script must not add the core ABI's site-packages to engines."""
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest
import nanocofold


@pytest.mark.parametrize('kind', ['variants', 'sequences'])
def test_fresh_engine_process_keeps_own_dependencies_and_safe_csv(tmp_path, kind):
    core_site = tmp_path/'core-python312-site-packages'
    package = core_site/'nanocofold'
    shutil.copytree(Path(nanocofold.__file__).parent, package,
                    ignore=shutil.ignore_patterns('_data', '__pycache__'))
    # These stand in for CPython 3.12 extension distributions, which a 3.11
    # engine must never search. The old bootstrap fails in this fresh process.
    for dependency in ('numpy', 'pandas', 'torch', 'esm'):
        (core_site/(dependency+'.py')).write_text("raise RuntimeError('core ABI dependency leaked')\n")
    engine_stubs = tmp_path/'engine-test-stubs'
    engine_stubs.mkdir()
    for dependency in ('torch', 'esm'):
        (engine_stubs/(dependency+'.py')).write_text('# GPU libraries are not needed for this import/CSV regression.\n')
    source=tmp_path/'input.csv'
    with source.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['variant_id','sequence','position','wt_aa','mut_aa','note'])
        writer.writeheader()
        writer.writerow(dict(variant_id='Nb1',sequence='A',position=1,wt_aa='A',mut_aa='C',note='=1+1'))
    output=tmp_path/'output.csv'
    wrapper=tmp_path/'engine_entry.py'
    wrapper.write_text(textwrap.dedent('''\
        import importlib.util,json,sys
        from pathlib import Path
        from types import SimpleNamespace
        script,source,output,kind=sys.argv[1:]
        core_site=str(Path(script).parents[2])
        assert 'nanocofold' not in sys.modules
        spec=importlib.util.spec_from_file_location('fresh_engine_esm',script)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import numpy as np,pandas as pd,nanocofold
        assert core_site not in sys.path, 'Core site-packages contaminated engine path'
        assert not Path(np.__file__).is_relative_to(core_site)
        assert not Path(pd.__file__).is_relative_to(core_site)
        assert Path(nanocofold.__file__).parent==Path(script).parents[1]
        module._log_probs=lambda *_:np.array([[0.,0.],[-2.,-3.]])
        args=SimpleNamespace(variants=source,sequences=source,output=output,
                             wt_sequence='A',reference_sequence='A',model='unused')
        alphabet=SimpleNamespace(tok_to_idx={'A':0,'C':1})
        getattr(module,'run_'+kind)(args,None,alphabet,'cpu')
        print(json.dumps({'core_parent_in_sys_path':False,'real_engine_pandas':pd.__file__,
                          'real_engine_numpy':np.__file__}))
        '''))
    environment={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}}
    environment['PYTHONPATH']=str(engine_stubs)
    result=subprocess.run([sys.executable,str(wrapper),str(package/'scripts/esm_llr.py'),
                           str(source),str(output),kind],cwd=tmp_path,env=environment,
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert json.loads(result.stdout.splitlines()[-1])['core_parent_in_sys_path'] is False
    rows=list(csv.DictReader(output.open()))
    assert rows[0]['variant_id']=='Nb1' and rows[0]['note']=="'=1+1"
    metric='esm_llr' if kind=='variants' else 'esm_mean_logprob'
    assert float(rows[0][metric])==(-1. if kind=='variants' else -2.)
