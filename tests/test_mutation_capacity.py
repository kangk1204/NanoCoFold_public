"""Unique substitution capacity and pre-execution browser review contracts."""
import copy
import itertools
import json

import pytest

from nanocofold import mature
from nanocofold.config import load_config
from nanocofold.platform.inputs import InputManager


@pytest.mark.parametrize('exclude_cys,protect_wt_cys', list(itertools.product([False, True], repeat=2)))
@pytest.mark.parametrize('low,high', [(1, 1), (2, 2), (1, 2)])
def test_capacity_matches_exhaustive_sequences(exclude_cys, protect_wt_cys, low, high):
    wt = 'ACA'
    positions = [i for i, aa in enumerate(wt) if not (protect_wt_cys and aa == 'C')]
    actual = set()
    for count in range(low, high + 1):
        for changed in itertools.combinations(positions, count):
            choices = [[aa for aa in mature.AMINO_ACIDS if aa != wt[i] and not (exclude_cys and aa == 'C')] for i in changed]
            for aas in itertools.product(*choices):
                seq = list(wt)
                for i, aa in zip(changed, aas):
                    seq[i] = aa
                actual.add(''.join(seq))
    got = mature.random_library_capacity(wt, {'CDR1': (1, 3)}, min_mutations=low,
        max_mutations=high, exclude_cys=exclude_cys, protect_wt_cys=protect_wt_cys)
    assert got['max_variants'] == len(actual)
    assert got['mutable_residues'] == len(positions)
    assert wt not in actual


def config():
    cfg = copy.deepcopy(load_config())
    cfg['run'].update(mode='mature', mock=False, dry_run=False)
    cfg['mature'].update(wt_sequence='AAA', wt_fasta=None,
        cdr={'source': 'config', 'regions': {'CDR1': [2, 2]}},
        library={'strategy': 'random', 'size': 19, 'min_mutations': 1, 'max_mutations': 1},
        prefilter={'mode': 'none', 'top_n': 19})
    return cfg


def test_preview_boundary_excludes_wt_and_preserves_exact_count(tmp_path):
    cfg = config()
    preview = mature.preview_random_library(cfg, tmp_path)
    assert preview == {'status': 'checked', 'mutable_residues': 1, 'max_variants': '18',
        'requested_variants': 18, 'wt_controls': 1, 'min_mutations': 1, 'max_mutations': 1}
    frame = mature.generate_library('AAA', {'CDR1': (2, 2)}, cfg)
    assert len(frame) == frame.sequence.nunique() == 19
    assert frame.mutation_count.tolist() == [0] + [1] * 18
    cfg['mature']['library']['size'] = 20
    with pytest.raises(ValueError, match=r'최대 18개인데 19개.*18개 이하'):
        mature.preview_random_library(cfg, tmp_path)
    with pytest.raises(ValueError, match=r'최대 18개인데 19개.*18개 이하'):
        mature.generate_library('AAA', {'CDR1': (2, 2)}, cfg)
    assert cfg['mature']['library']['size'] == 20  # Never silently truncate the request.


def test_exact_double_substitution_capacity_is_324_not_361(tmp_path):
    cfg = config()
    cfg['mature'].update(wt_sequence='AA', cdr={'source': 'config', 'regions': {'CDR3': [1, 2]}})
    cfg['mature']['library'].update(size=325, min_mutations=2, max_mutations=2)
    info = mature.preview_random_library(cfg, tmp_path)
    assert info['max_variants'] == '324'
    frame = mature.generate_library('AA', {'CDR3': (1, 2)}, cfg)
    assert len(frame) == frame.sequence.nunique() == 325
    assert frame.iloc[1:].mutation_count.eq(2).all()
    cfg['mature']['library']['size'] = 326
    with pytest.raises(ValueError, match='324'):
        mature.preview_random_library(cfg, tmp_path)


def test_impossible_substitution_count_has_actionable_notice(tmp_path):
    cfg = config()
    cfg['mature']['library']['max_mutations'] = 2
    with pytest.raises(ValueError, match=r'위치는 1개.*1개 이하'):
        mature.preview_random_library(cfg, tmp_path)


def test_large_capacity_uses_decimal_string_without_javascript_rounding(tmp_path):
    cfg = config()
    cfg['mature'].update(wt_sequence='A'*30, cdr={'source':'config', 'regions':{'CDR1':[1,30]}})
    cfg['mature']['library'].update(size=2, min_mutations=15, max_mutations=15)
    info = mature.preview_random_library(cfg, tmp_path)
    assert isinstance(info['max_variants'], str)
    assert int(info['max_variants']) > 2**53
    assert json.loads(json.dumps(info))['max_variants'] == info['max_variants']


def test_unknown_cdrs_are_not_replaced_with_estimates_or_synthetic_ranges(tmp_path, monkeypatch):
    cfg = config()
    cfg['mature']['cdr']['source'] = 'auto'
    cfg['mature']['anarci']['env'] = str(tmp_path/'missing')
    monkeypatch.setattr(mature, 'random_library_capacity', lambda *a, **kw: pytest.fail('No CDR guessing'))
    assert mature.preview_random_library(cfg, tmp_path) == {'status':'not_checked'}


def test_available_automatic_annotation_checked_without_inference(tmp_path, monkeypatch):
    cfg = config(); cfg['mature']['cdr']['source'] = 'auto'
    env = tmp_path/'annotation-env'; (env/'bin').mkdir(parents=True); (env/'bin/python').touch()
    cfg['mature']['anarci']['env'] = str(env)
    calls=[]
    def annotate(cfg, records, folder):
        calls.append(records)
        return {'WT': {'CDR1': (2, 2)}}
    monkeypatch.setattr(mature,'resolve_cdr_ranges_batch',annotate)
    monkeypatch.setattr(mature,'generate_random_library',lambda *a,**kw:pytest.fail('No generation during review'))
    assert mature.preview_random_library(cfg,tmp_path)['max_variants']=='18'
    assert calls == [{'WT':'AAA'}]


def test_input_review_rejects_oversized_library_before_any_job(tmp_path, monkeypatch):
    manager=InputManager(tmp_path)
    try:
        cfg=config(); cfg['mature']['library']['size']=101
        monkeypatch.setattr(mature,'generate_random_library',lambda *a,**kw:pytest.fail('No generation during review'))
        with pytest.raises(ValueError,match=r'최대 18개인데 100개.*18개 이하'):
            manager.fasta({'mode':'mature','content':'>target\nACDEFGHIKLMNPQRSTVWY',
                'wt_fasta':'>WT\nAAA','mock':False,'config':cfg})
        if (tmp_path/'runs').exists():
            assert not any((tmp_path/'runs').iterdir())
        receipts=list((tmp_path/'inputs/failed_reviews').glob('*/failed_review.json'))
        assert len(receipts)==1
        assert json.loads(receipts[0].read_text())['ready_for_execution'] is False
    finally:
        manager.close()


def test_successful_input_review_records_capacity_without_modifying_requested_count(tmp_path):
    manager=InputManager(tmp_path)
    try:
        result=manager.fasta({'mode':'mature','content':'>target\nACDEFGHIKLMNPQRSTVWY',
            'wt_fasta':'>WT\nAAA','mock':False,'config':config()})
        assert result['config']['mature']['library']['size']==19
        assert result['review']['mutation_capacity']['max_variants']=='18'
        assert any('요청 18개' in msg and '최대 18개' in msg for msg in result['review']['warnings'])
        manager.reviewed(result['id'])  # Review hashes still bind the new notice.
    finally:
        manager.close()


def test_no_mutable_residue_does_not_recommend_an_invalid_zero_substitution_count(tmp_path):
    cfg = config(); cfg['mature']['wt_sequence'] = 'CCC'
    with pytest.raises(ValueError, match='치환 가능한 CDR 잔기가 없습니다'):
        mature.preview_random_library(cfg, tmp_path)
