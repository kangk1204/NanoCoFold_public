"""Cleanup completing before CLI lock acquisition must not overwrite its state."""
import copy
import json

import pytest

from nanocofold import cli, state
from nanocofold.config import dump_resolved, load_config


@pytest.mark.parametrize('command', ['score', 'report'])
def test_cleanup_finishing_before_lock_prevents_progress_and_state_writes(tmp_path, monkeypatch, command):
    cfg=load_config()
    cfg['run'].update(mode='screen', mock=True)
    cfg['structure']['provider']='mock'
    cfg['target'].update(sequence='ACDEFGHIKLMNPQRSTVWY'*2, pdb=None, fasta=None)
    root=tmp_path/'owned-run'
    root.mkdir()
    dump_resolved(cfg,root/'run_config.yaml')
    (root/'run_state.json').write_text(json.dumps({'execution':{'state':'complete'},'phases':{}}))
    original_lock=state.RunLock
    with original_lock(root):
        pass
    before={p.name:p.read_bytes() for p in root.iterdir()}

    class CleanupWindow:
        def __init__(self,path):self.lock=original_lock(path)
        def __enter__(self):
            # Simulate cleanup completing after preflight, before this lock.
            (root/'retained').mkdir()
            (root/'retained'/'record.json').write_text('original compact snapshot')
            return self.lock.__enter__()
        def __exit__(self,*args):return self.lock.__exit__(*args)

    monkeypatch.setattr(state,'RunLock',CleanupWindow)
    monkeypatch.setattr(cli,'_load',lambda args:copy.deepcopy(cfg))
    monkeypatch.setattr(cli,'_open_run',lambda *args:pytest.fail('state access preceded retained check'))
    monkeypatch.setattr(cli,'ConsoleProgress',lambda *args,**kwargs:pytest.fail('progress preceded retained check'))
    args=cli.build_parser().parse_args([command,'--run-dir',str(root),'--mock'])
    with pytest.raises(ValueError,match='정리 보관본은 읽기 전용'):
        cli._execute_command(args)
    assert {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}==before
    assert (root/'retained'/'record.json').read_text()=='original compact snapshot'
