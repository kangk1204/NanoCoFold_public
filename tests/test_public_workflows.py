"""CPU-only synthetic execution contracts; these are not engine predictions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
import yaml

from nanocofold.install import init_workspace
from nanocofold.platform.batches import BatchManager
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.jobs import JobManager
from nanocofold.platform.results import ResultReader

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_WORKFLOW_TEST') != '1', reason='Enable CPU synthetic workflows explicitly')
@pytest.mark.parametrize('mode', ['complex', 'screen', 'mature', 'design'])
def test_cpu_workflow_preserves_mock_boundaries_and_delivers_report(tmp_path, mode):
    init_workspace(tmp_path)
    inputs, jobs = InputManager(tmp_path), JobManager(tmp_path)
    try:
        payload = json.loads((ROOT / 'examples/tutorial' / (mode + '_batch.json')).read_text())
        payload['mock'] = True
        payload['api_consent'] = False
        for entry in payload['entries']:
            entry['mock'] = True
            config = entry.setdefault('config', {})
            config.setdefault('run', {})['mock'] = True
            config.setdefault('screen', {})['allow_public_msa_server'] = False
            if mode != 'complex':
                config['screen'].setdefault('colabfold', {}).update(msa_source='cache', msa_mode='precomputed')
                config.setdefault('structure', {})['allow_public_msa_server'] = False
                config['structure']['msa'] = {'backend':'empty'}
        batch = BatchManager(inputs, jobs).review(payload)
        assert not any(row.get('error') for row in batch['rows']), batch['rows']
        config = inputs.get(batch['rows'][0]['input_id'])['config']
        config['run'].update(mock=True, outdir=str(tmp_path / 'runs'), name='synthetic-' + mode)
        path = tmp_path / 'synthetic.yaml'
        path.write_text(yaml.safe_dump(config, allow_unicode=True))
    finally:
        inputs.close(); jobs.close()
    environment = dict(os.environ, NANOCOFOLD_ROOT=str(tmp_path), PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, '-m', 'nanocofold', 'run', '--mode', mode,
                             '--config', str(path), '--run-id', 'synthetic-' + mode],
                            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=180)
    expected_code = 1 if mode == 'complex' else 0
    assert result.returncode == expected_code, result.stderr[-6000:] + result.stdout[-3000:]
    if mode == 'complex':
        assert '일반 복합체는 합성 좌표를 생성하지 않습니다' in result.stdout + result.stderr
    roots = list((tmp_path / 'runs').glob('*/run_state.json'))
    assert len(roots) == 1
    run = roots[0].parent
    state = json.loads(roots[0].read_text())
    assert state['execution']['state'] == ('failed' if mode == 'complex' else 'complete')
    if mode == 'complex':
        assert not list(run.rglob('complex_results.json'))
        assert not list(run.rglob('*.pdb')) and not list(run.rglob('*.cif'))
    summary = ResultReader(run).summary()
    # General complexes reject simulation, so they contain no mock model.
    assert summary['mock'] is (mode != 'complex')
    assert summary['complete'] is (mode != 'complex')
    assert summary['mode'] == mode
    archives = list(run.rglob('report_delivery.zip'))
    if mode == 'complex':
        # Unsupported mock computation records a failure; it cannot publish a
        # completed native ledger or a delivery archive implying model results.
        assert not archives
        return
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert archive.testzip() is None
        assert any(name.endswith('report.html') for name in archive.namelist())


def test_version_flag_matches_distribution_metadata():
    import tomllib
    from nanocofold import __version__
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    assert version == __version__
    result = subprocess.run([sys.executable, '-m', 'nanocofold', '--version'], cwd=ROOT,
                            text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'NanoCoFold ' + version
