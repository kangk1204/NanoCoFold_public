from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path
from string import Formatter

import pytest

from nanocofold import cli, handoff_validation, specificity
from nanocofold.handoff_i18n import MESSAGE_CATALOG, SPECIFICITY_CATALOG, localize_handoff
import hashlib

import pandas as pd
import yaml

from nanocofold.utils.seq import codon_optimize


def _valid_cfg():
    return {
        'expression': {'host': 'ecoli_periplasmic', 'vector': 'pET22b', 'vendor_constraints_reviewed': True,
                       'construct_reviewed': True, 'codon_optimize': True, 'order_format': 'dna'},
        'experiment': {'assay': 'ELISA', 'antigen_qc_confirmed': True, 'controls_confirmed': True},
    }


def _valid_row():
    sequence = 'MQVQLVESGGGHHHHHH'
    return {'design_id': 'd1', 'plate_position': 'A1', 'final_eligible': True, 'dev_cdr_evaluated': True,
            'sequence': sequence, 'sequence_sha256': hashlib.sha256(sequence.encode()).hexdigest(),
            'construct_aa': sequence, 'construct_dna': codon_optimize(sequence, 'ecoli')}


def _valid_specificity_cfg():
    cfg = _valid_cfg()
    cfg['target'] = {'sequence': 'ACDEFGHIK', 'hotspots': ['seq2'], 'aux_chains': []}
    cfg['rank'] = {'fallback_to_ineligible': False}
    cfg['specificity'] = {
        'enabled': True, 'species': 'human', 'isoform': 'x1', 'sequence_source': 'test', 'context': 'isolated',
        'hotspot_rationale': 'test', 'cleavage': {'after': 4, 'retain': 'N'}, 'epitope_mode': 'exposed',
        'hotspots_full': [2], 'antigen_constructs': {
            'full': {'n_tag': '', 'c_tag': ''}, 'cleaved': {'n_tag': '', 'c_tag': ''}},
    }
    return cfg


def _write_run(tmp_path, cfg, rows):
    run_dir = tmp_path / 'run'
    (run_dir / '05_final').mkdir(parents=True)
    (run_dir / 'run_config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    pd.DataFrame(rows).to_csv(run_dir / '05_final' / 'synthesis_plate_list.csv', index=False)
    return run_dir


def _source_template(node: ast.expr) -> str:
    if isinstance(node, ast.Constant):
        return node.value
    assert isinstance(node, ast.JoinedStr)
    values = []
    index = 0
    for value in node.values:
        if isinstance(value, ast.Constant):
            values.append(value.value)
        else:
            values.append('{' + str(index) + '}')
            index += 1
    return ''.join(values)


def _without_messages(result: dict) -> dict:
    result = copy.deepcopy(result)
    for category in ('errors', 'warnings'):
        for item in result[category]:
            del item['message']
    return result


def _snapshot(run_dir: Path) -> dict[str, bytes]:
    return {str(path.relative_to(run_dir)): path.read_bytes() for path in run_dir.rglob('*') if path.is_file()}


def test_every_handoff_diagnostic_has_both_language_presentations():
    """Future validation branches must not silently bypass CLI localization."""
    tree = ast.parse(Path(handoff_validation.__file__).read_text())
    emitted = {
        _source_template(node.args[2])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_add'
    }
    assert emitted == {source for _, source, _, _ in MESSAGE_CATALOG}
    for codes, source, ko, en in MESSAGE_CATALOG:
        assert codes and all(isinstance(code, str) for code in codes)
        placeholders = lambda value: [field for _, field, _, _ in Formatter().parse(value) if field is not None]
        assert sorted(placeholders(source)) == sorted(placeholders(ko)) == sorted(placeholders(en))
        assert re.search('[가-힣]', ko)
        assert not re.search('[가-힣]', en)


def test_known_nested_specificity_config_errors_have_english_presentations():
    tree = ast.parse(Path(specificity.__file__).read_text())
    emitted = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name not in {'_position', 'resolve_definition'}:
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                emitted.add(_source_template(node.exc.args[0]))
    assert emitted == {source for source, _, _ in SPECIFICITY_CATALOG}


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_cli_localizes_real_missing_run_without_creating_it(tmp_path, capsys, language):
    run_dir = tmp_path / '연구: Missing run_config.yaml: A B'
    core = handoff_validation.validate_handoff(run_dir)
    assert cli.main(['validate-handoff', '--run', str(run_dir), '--lang', language]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not run_dir.exists()
    assert _without_messages(result) == _without_messages(core)
    assert str(run_dir / 'run_config.yaml') in result['errors'][0]['message']
    assert ('없습니다' if language == 'ko' else 'Missing') in result['errors'][0]['message']


def test_default_cli_language_remains_english(tmp_path, capsys):
    assert cli.main(['validate-handoff', '--run', str(tmp_path / 'absent')]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['errors'][0]['message'].startswith('Missing run_config.yaml: ')


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_ready_handoff_stays_ready_and_read_only(tmp_path, capsys, language):
    run_dir = _write_run(tmp_path, _valid_cfg(), [_valid_row()])
    before = _snapshot(run_dir)
    assert cli.main(['validate-handoff', '--run', str(run_dir), '--lang', language]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is True
    assert _snapshot(run_dir) == before
    assert result['warnings'][0]['code'] == 'draft_codon_substitution'
    assert bool(re.search('[가-힣]', result['warnings'][0]['message'])) is (language == 'ko')


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_localized_failures_preserve_candidate_id_codes_rows_and_review_gate(tmp_path, capsys, language):
    cfg = _valid_specificity_cfg()
    cfg['specificity']['species'] = ''
    row = _valid_row()
    row['design_id'] = '후보: sequence must contain standard amino acids.: {x}/ A B'
    row['construct_aa'] = 'WRONG'
    row['final_eligible'] = False
    run_dir = _write_run(tmp_path, cfg, [row])
    before = _snapshot(run_dir)
    core = handoff_validation.validate_handoff(run_dir)
    assert cli.main(['validate-handoff', '--run', str(run_dir), '--lang', language]) == 1
    result = json.loads(capsys.readouterr().out)
    assert _without_messages(result) == _without_messages(core)
    assert _snapshot(run_dir) == before
    mismatch = next(error for error in result['errors'] if error['code'] == 'construct_aa_mismatch')
    assert mismatch['message'].startswith(row['design_id'] + ': ')
    assert mismatch['row'] == 0
    definition = next(error for error in result['errors'] if error['code'] == 'invalid_specificity_definition')
    assert definition['message'] == (
        '특이성 표적 정의를 확인하세요: specificity.species를 지정하세요.' if language == 'ko'
        else 'specificity target definition is invalid: Specify specificity.species.'
    )


def test_existing_korean_diagnostics_are_english_in_english_cli_view():
    source_messages = [(codes[0], source) for codes, source, _, _ in MESSAGE_CATALOG if re.search('[가-힣]', source)]
    result = {'ok': False, 'errors': [{'code': code, 'message': message.format('candidate')} for code, message in source_messages], 'warnings': []}
    localized = localize_handoff(result, 'en')
    assert all(not re.search('[가-힣]', error['message']) for error in localized['errors'])
    assert _without_messages(localized) == _without_messages(result)


def test_unrecognized_external_diagnostics_and_user_values_are_not_rewritten():
    result = {'ok': False, 'errors': [{'code': 'future_code', 'message': '후보/파일: free-form OS diagnostic {x}', 'row': 7}], 'warnings': []}
    assert localize_handoff(result, 'en') == result
    assert localize_handoff(result, 'ko') == result


def test_handoff_help_explains_language_option_in_both_languages(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(['validate-handoff', '--help'])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert '--lang {ko,en}' in output
    assert 'Diagnostic language' in output and '안내 언어' in output


def test_candidate_id_containing_diagnostic_delimiters_is_preserved(tmp_path):
    row = _valid_row()
    row['design_id'] = '후보: construct_dna contains non-ACGT bases: B.'
    row['construct_dna'] = 'BTA'
    run_dir = _write_run(tmp_path, _valid_cfg(), [row])
    core = handoff_validation.validate_handoff(run_dir)
    localized = localize_handoff(core, 'ko')
    message = next(error['message'] for error in localized['errors'] if error['code'] == 'invalid_construct_dna')
    assert message == row['design_id'] + ': construct_dna에 ACGT 이외의 염기가 있습니다: B.'
    assert _without_messages(localized) == _without_messages(core)


@pytest.mark.parametrize('prefix', [source.format('sample', 'column') for _, source, _, _ in MESSAGE_CATALOG])
def test_candidate_ids_matching_other_diagnostics_cannot_select_their_templates(prefix):
    candidate = prefix + ': 후보/ {x}'
    result = {'ok': False, 'errors': [{
        'code': 'construct_aa_mismatch', 'row': 2,
        'message': candidate + ': construct_aa does not equal signal_peptide + sequence + tag from run_config.',
    }], 'warnings': []}
    localized = localize_handoff(result, 'ko')
    assert localized['errors'][0]['message'] == candidate + ': construct_aa가 run_config의 signal_peptide + sequence + tag와 다릅니다.'
    assert _without_messages(localized) == _without_messages(result)


def test_every_static_diagnostic_code_is_bound_to_its_own_template():
    catalog = {source: codes for codes, source, _, _ in MESSAGE_CATALOG}
    tree = ast.parse(Path(handoff_validation.__file__).read_text())
    dynamic = {
        'Required handoff config must be an explicit string: {0}': {
            'missing_expression_host', 'missing_expression_vector', 'missing_experiment_assay'},
        'Required handoff confirmation must be true: {0}': {
            'vendor_constraints_not_reviewed', 'construct_not_reviewed', 'antigen_qc_not_confirmed', 'controls_not_confirmed'},
        'synthesis handoff requires {0}.': {'missing_final_eligible_column', 'missing_dev_cdr_evaluated_column'},
        '{0}: {1} must be true for handoff.': {'not_final_eligible', 'developability_unassessed'},
    }
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_add'):
            continue
        source = _source_template(node.args[2])
        if isinstance(node.args[1], ast.Constant):
            assert node.args[1].value in catalog[source]
        else:
            assert set(catalog[source]) == dynamic[source]
