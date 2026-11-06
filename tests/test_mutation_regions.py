"""Position masks use input-WT numbering and never alter CDR annotations."""
import copy
import itertools
import json

import pytest

from nanocofold import mature
from nanocofold.config import load_config, _validate
from nanocofold.platform.cloud_agent import Agent
from nanocofold.platform.inputs import InputManager

WT = 'ACDEFGHIKLMNPQRSTVWYACDEFGHIKLMNPQRSTVWY'
CDRS = {'CDR1': (3, 6), 'CDR2': (10, 13), 'CDR3': (20, 26)}


def config(scope='cdr3', ranges=None):
    cfg = copy.deepcopy(load_config())
    cfg['run'].update(mode='mature', mock=False, dry_run=False)
    cfg['mature'].update(wt_sequence=WT, wt_fasta=None, cdr={'source': 'config', 'regions': copy.deepcopy(CDRS)},
        library={'strategy': 'random', 'size': 101, 'min_mutations': 2, 'max_mutations': 2,
                 'mutation_region': scope, 'mutation_ranges': ranges or []}, prefilter={'mode': 'none'})
    return cfg


def test_cloud_review_seals_annotation_artifacts_after_preview_once(tmp_path, monkeypatch):
    """Real ANARCI logs include run timings and change if preview is repeated."""
    calls = []
    def preview(cfg, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        calls.append(work_dir)
        (work_dir / 'anarci_batch.log').write_text(f'annotation invocation {len(calls)}\n')
        return {'status': 'checked', 'mutable_positions': [23, 24],
                'min_mutations': 2, 'max_mutations': 2, 'max_variants': '324',
                'requested_variants': 100, 'wt_controls': 1}
    class Client:
        def upload(self, key, path):
            pass
    monkeypatch.setattr(mature, 'preview_random_library', preview)
    agent = Agent(tmp_path, Client())
    settings = {'workflow': 'direct', 'engine': 'boltz2', 'count': 101,
                'mutation_strategy': 'random', 'mutation_min': 2, 'mutation_max': 2,
                'mutation_region': 'custom', 'mutation_ranges': [[23, 24]]}
    try:
        response = agent.operation({'id': 'annotation-review', 'agent_id': agent.agent_id,
            'kind': 'prepare', 'payload': json.dumps({'mode': 'mature', 'settings': settings,
            'proteins': [{'name': 'fixture', 'sequence': WT}], 'wt_fasta': '>WT\n' + WT})})['response']
        payload = {'input_id': response['input_id'], 'mode': 'mature', 'settings': settings}
        cfg, receipt = agent.reviewed_diagnostics_config(payload)
        assert len(calls) == 1
        assert cfg == response['config'] and receipt == response['review_receipt']
        assert 'mutation_capacity/anarci_batch.log' in receipt['files']
        (calls[0] / 'anarci_batch.log').write_text('changed after review')
        with pytest.raises(ValueError, match='다시 검토'):
            agent.reviewed_diagnostics_config(payload)
    finally:
        agent.manager.close(); agent.inputs.close()


@pytest.mark.parametrize('scope,ranges,expected', [
    ('cdr3', [], {20, 21, 23, 24, 25, 26}),
    ('custom', [[22, 24], [26, 26]], {23, 24, 26}),
    ('custom', [[3, 3], [10, 10]], {3, 10}),
])
def test_100_unique_exact_double_variants_stay_in_selected_region(scope, ranges, expected, tmp_path):
    cfg = config(scope, ranges); before = copy.deepcopy(cfg)
    review = mature.preview_random_library(cfg, tmp_path)
    generated = mature.generate_library(WT, CDRS, cfg)
    assert cfg == before and cfg['mature']['cdr']['regions'] == CDRS
    assert len(generated) == generated.sequence.nunique() == 101
    assert generated.iloc[0].variant_id == 'WT' and generated.iloc[0].sequence == WT
    assert set(review['mutable_positions']) == expected
    assert int(review['max_variants']) == len(list(itertools.combinations(expected, 2))) * 18**2
    for row in generated.iloc[1:].itertuples():
        changed = {i for i, (a, b) in enumerate(zip(WT, row.sequence), 1) if a != b}
        assert len(row.sequence) == len(WT) and len(changed) == row.mutation_count == 2
        assert changed.issubset(expected) and changed == set(json.loads(row.mutation_positions))
        assert all(row.sequence[i-1] != 'C' for i in changed)
    info = generated.attrs['library_generation']
    assert info['mutation_region'] == scope and info['mutation_ranges'] == ranges
    assert info['position_numbering'] == 'one_based_input_wt_sequence'
    assert set(info['mutable_cdr_positions']) == expected
    again = mature.generate_library(WT, CDRS, cfg)
    assert generated.to_dict('records') == again.to_dict('records')


@pytest.mark.parametrize('scope,ranges,message', [
    ('wrong', [], '치환 범위'), ('custom', [], '번호를 입력'),
    ('custom', [[0, 3]], '1부터'), ('custom', [[4, 3]], '1부터'),
    ('custom', [[True, 3]], '1부터'), ('custom', [[3.0, 4]], '1부터'),
    ('custom', [[3, 5], [5, 6]], '겹칩니다'), ('cdr3', [[22, 24]], 'custom'),
    ('custom', '22-24', '목록'),
])
def test_invalid_masks_fail_config_validation(scope, ranges, message):
    cfg=config(); cfg['mature']['library'].update(mutation_region=scope, mutation_ranges=ranges)
    with pytest.raises(ValueError, match=message): _validate(cfg)


@pytest.mark.parametrize('ranges,message', [([[1, 2]], 'CDR 밖'), ([[19, 22]], 'CDR 밖'),
    ([[22, 999999999]], '서열 길이'), ([[22, 22]], '치환 가능한 CDR 잔기가 없습니다'),
    ([[23, 23]], '1개 이하')])
def test_unusable_mask_rejected_before_sampling(ranges, message, tmp_path):
    cfg=config('custom',ranges)
    with pytest.raises(ValueError, match=message): mature.preview_random_library(cfg,tmp_path)
    with pytest.raises(ValueError, match=message): mature.generate_library(WT,CDRS,cfg)


def test_cdr3_selection_requires_real_cdr3_annotation(tmp_path):
    cfg=config(); cfg['mature']['cdr']['regions']={'CDR1': [3,6]}
    with pytest.raises(ValueError,match='CDR3 구간'): mature.preview_random_library(cfg,tmp_path)


def test_capacity_uses_the_mask_and_preserves_oversized_request(tmp_path):
    cfg=config('custom',[[23,24]]); cfg['mature']['library']['size']=326
    with pytest.raises(ValueError,match='최대 324개인데 325개'): mature.preview_random_library(cfg,tmp_path)
    assert cfg['mature']['library']['size']==326


@pytest.mark.parametrize('engine', ['boltz2','chai1'])
def test_cloud_settings_keep_scope_without_changing_cdr_annotation(engine, tmp_path):
    agent=Agent(tmp_path, object())
    try:
        cfg=agent.settings('mature',{'workflow':'direct','engine':engine,'count':101,
            'mutation_strategy':'random','mutation_min':2,'mutation_max':2,
            'mutation_region':'custom','mutation_ranges':[[22,24]],'antifold':False})
        assert cfg['mature']['library']['mutation_ranges']==[[22,24]]
        assert cfg['mature']['library']['mutation_region']=='custom'
        assert 'cdr' not in cfg['mature']
    finally:
        agent.manager.close(); agent.inputs.close()


def test_input_review_records_scoped_positions_in_the_signed_review(tmp_path):
    manager=InputManager(tmp_path)
    try:
        result=manager.fasta({'mode':'mature','content':'>target\nACDEFGHIKLMNPQRSTVWY',
            'wt_fasta':'>WT\n'+WT,'mock':False,'config':config('custom',[[22,24]])})
        assert result['review']['mutation_capacity']['mutable_positions']==[23,24]
        assert any('23, 24' in text and '2–2' in text for text in result['review']['warnings'])
        manager.reviewed(result['id'])
    finally: manager.close()
