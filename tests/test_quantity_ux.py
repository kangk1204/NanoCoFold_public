"""Display settings only; no analysis, generation, network or subprocess runs."""
import socket
import subprocess
import json
import os
from pathlib import Path

import pytest

from nanocofold.platform.app import planning_defaults


@pytest.fixture(autouse=True)
def software_only_tripwire(monkeypatch):
    """The only child process allowed is the installed Node VM comparison."""
    original = subprocess.Popen
    def blocked(*args, **kwargs):
        raise AssertionError('Unexpected network or native inference operation')
    def node_only(args, *values, **options):
        if not isinstance(args, (list, tuple)) or list(args[:2]) != ['node', '-e']:
            return blocked()
        return original(args, *values, **options)
    monkeypatch.setattr(subprocess, 'Popen', node_only)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')


def test_planning_defaults_exposes_wt_control_from_effective_config(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Unexpected network or process call during settings preview')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    assert planning_defaults(tmp_path)['rank']['include_wt_control'] is False
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'default.yaml').write_text('rank:\n  include_wt_control: true\n')
    assert planning_defaults(tmp_path)['rank']['include_wt_control'] is True


def test_planning_defaults_keeps_both_distinct_design_limits(tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'default.yaml').write_text('design:\n  num_designs: 1000\n  filters:\n    top_n_to_score: 1000\nscreen:\n  tier2_n: 500\n')
    values = planning_defaults(tmp_path)
    assert values['design']['num_designs'] == 1000
    assert values['design']['filters']['top_n_to_score'] == 1000
    assert values['screen']['tier2_n'] == 500


@pytest.mark.parametrize('boltz_seed', [None, 17])
def test_preview_conditions_match_real_backend_and_workspace_defaults(tmp_path, boltz_seed):
    from nanocofold.config import cfg_get
    from nanocofold.platform.inputs import normalized_config
    from nanocofold.engine_comparison import _conditions
    values = {'run': {'mode': 'screen', 'seed': 42},
              'structure': {'boltz2': {'seed': 99}, 'chai1': {'seed': 88}},
              'score': {'boltz2': {'seed': boltz_seed}, 'boltz2_diffusion_samples': 3},
              'screen': {'engine': 'boltz2', 'comparison': {'enabled': True, 'engines': ['boltz2', 'chai1', 'colabfold', 'opendde']},
                         'num_diffn_samples': 4, 'colabfold': {'num_models': 2, 'num_seeds': 3}, 'opendde': {'enabled': True, 'samples': 2}}}
    config = tmp_path / 'config'; config.mkdir()
    (config / 'default.yaml').write_text(json.dumps(values))
    final = normalized_config(tmp_path)
    defaults = planning_defaults(tmp_path)
    assert cfg_get(defaults, 'score.boltz2.seed') == boltz_seed
    assert cfg_get(defaults, 'screen.opendde.samples') == 2
    root = Path(__file__).resolve().parents[1]
    ui = os.environ.get('QUANTITY_TEST_UI_SOURCE', str(root / 'nanocofold/templates/platform.js'))
    script = """const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const block=source.split('// QUANTITY_PLAN_START\\n')[1].split('// QUANTITY_PLAN_END')[0];
const c={};vm.createContext(c);vm.runInContext(block,c);
const configs=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(configs.map(cfg=>c.analysisQuantityPlan(cfg,'screen',1).conditions)));
"""
    response = subprocess.run(['node', '-e', script, ui], input=json.dumps([final, defaults]),
                              capture_output=True, text=True, check=True)
    for conditions in json.loads(response.stdout):
        for engine in ['boltz2', 'chai1', 'colabfold', 'opendde']:
            assert conditions[engine]['seed'] == _conditions(final, engine)['seed']
        assert conditions['boltz2']['samples'] == _conditions(final, 'boltz2')['diffusion_samples']
        assert conditions['chai1']['samples'] == _conditions(final, 'chai1')['diffusion_samples']
        assert conditions['colabfold']['models'] == _conditions(final, 'colabfold')['model_weights']
        assert conditions['colabfold']['seeds'] == _conditions(final, 'colabfold')['num_seeds']
        assert conditions['opendde']['samples'] == _conditions(final, 'opendde')['samples']
