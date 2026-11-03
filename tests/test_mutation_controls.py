"""Mutation request/config contracts without inference or networking."""
from __future__ import annotations

import pytest

from nanocofold.platform.cloud_agent import Agent


class FakeCloud:
    def __init__(self):
        self.uploads = []

    def upload(self, key, path):
        self.uploads.append((key, path.read_bytes()))


@pytest.fixture
def agent(tmp_path):
    value = Agent(tmp_path, FakeCloud())
    try:
        yield value
    finally:
        value.manager.close()
        value.inputs.close()


@pytest.mark.parametrize('workflow,engine', [('direct','boltz2'), ('direct','chai1'), ('engine_comparison','boltz2')])
def test_random_mature_without_cascade_evaluates_the_exact_library_and_wt(agent, tmp_path, workflow, engine):
    from nanocofold import mature
    from nanocofold.platform.inputs import normalized_config
    settings = {'workflow': workflow, 'engine': engine, 'count': 101,
        'mutation_strategy': 'random', 'mutation_min': 1, 'mutation_max': 3,
        'msa_source': 'cache'}
    if workflow == 'engine_comparison':
        settings['comparison_engines'] = ['boltz2', 'chai1']
    overrides = agent.settings('mature', settings)
    cfg = normalized_config(agent.workspace, overrides)
    assert not cfg['screen']['cascade']['enabled']
    assert cfg['mature']['library']['size'] == 101
    assert cfg['mature']['prefilter']['mode'] == 'none'
    assert cfg['rank']['include_wt_control'] is True
    assert cfg['rank']['weights']['esm_score'] == cfg['rank']['weights']['antifold'] == 0
    assert cfg['score']['thresholds']['ipsae'] == 0.6
    assert cfg['score']['thresholds']['iptm'] == 0.7
    assert cfg['rank']['fallback_to_ineligible'] is False
    assert cfg['mature']['exclude']['wt_cys'] is True
    wt = 'ACDEFGHIKLMNPQRSTVWY' * 2
    library = mature.generate_library(wt, {'CDR1': (3, 6), 'CDR2': (10, 13), 'CDR3': (20, 23)}, cfg)
    selected, _ = mature.run_prefilter(library, wt, cfg, tmp_path / 'prefilter')
    assert len(selected) == selected.sequence.nunique() == 101
    assert selected.variant_id.eq('WT').sum() == 1
    assert selected.loc[selected.variant_id != 'WT', 'mutation_count'].between(1, 3).all()
    assert agent.manager.children == {} and agent.client.uploads == []


@pytest.mark.parametrize('mode', ['screen', 'design'])
def test_random_library_does_not_enable_an_unrelated_direct_workflow(agent, mode):
    with pytest.raises(ValueError, match='무작위 다중 치환'):
        agent.settings(mode, {'workflow': 'direct', 'count': 10, 'mutation_strategy': 'random'})
