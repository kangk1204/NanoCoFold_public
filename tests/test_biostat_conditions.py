"""Shared CPU contract: observed unanimity cannot manufacture approval."""
import hashlib

import pandas as pd
import pytest

from nanocofold import specificity
from nanocofold.config import load_config


@pytest.mark.parametrize('case,expected_complete', [
    ('approved', True), ('one_missing', False), ('all_empty', False),
    ('unanimous_foreign', False), ('no_approval', False),
])
def test_specificity_requires_independent_approved_conditions(case, expected_complete):
    cfg = load_config()
    cfg['run']['mock'] = False
    cfg['specificity'].update(engine='boltz2', context='isolated', epitope_mode='exposed')
    sequence = 'ACDEFG'
    sequence_hash = hashlib.sha256(sequence.encode()).hexdigest()
    candidates = pd.DataFrame([{'design_id': 'synthetic', 'sequence': sequence}])
    records = pd.DataFrame([
        {'design_id': 'synthetic', 'sequence_sha256': sequence_hash,
         'engine': 'boltz2', 'target_state': state, 'seed': seed,
         'status': 'complete', 'comparison_conditions': 'a' * 64,
         'context': 'isolated', 'evidence_type': 'real', 'restraint': 'none',
         'ipsae': .8 if state == 'cleaved' else .1,
         'iptm': .9, 'interface_plddt': 90., 'interface_pae': 2.,
         'hotspot_contact_frac': 1., 'n_clashes': 0}
        for state in ['cleaved', 'full'] for seed in [1, 2, 3]
    ])
    if case == 'one_missing':
        records.loc[0, 'comparison_conditions'] = None
    elif case == 'all_empty':
        records['comparison_conditions'] = ''
    elif case == 'unanimous_foreign':
        records['comparison_conditions'] = 'b' * 64
    before = records.copy(deep=True)
    result = specificity.aggregate(candidates, records, cfg, [1, 2, 3],
                                  expected_conditions=None if case == 'no_approval' else 'a' * 64).iloc[0]
    assert bool(result.specificity_complete) is expected_complete
    assert bool(result.positive_quality_pass) is expected_complete
    assert result.comparison_status == ('complete' if expected_complete else 'insufficient')
    assert (result.comparison_conditions_status == 'verified') is expected_complete
    assert bool(pd.isna(result.delta_ipsae)) is not expected_complete
    pd.testing.assert_frame_equal(records, before)
