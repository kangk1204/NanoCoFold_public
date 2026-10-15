"""Spreadsheet exports must never alter scientific correspondence labels."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from nanocofold import mock
from nanocofold.platform.inputs import InputManager
from nanocofold.platform.results import ResultReader
from nanocofold.utils import pdb_io
from nanocofold.utils.residue_mapping import EXACT_TEXT_COLUMN, read_residue_mapping, write_residue_mapping
from nanocofold.wizard import WizardSession


def _source(tmp_path, chain_id):
    source = mock.make_mock_target_only('ACDEFGHIKLMNPQRSTVWY', tmp_path / 'original.pdb')
    structure = pdb_io.load_structure(source)
    chain = structure[0]['A']
    chain.id = chain_id
    for number, residue in enumerate(list(chain), -5):
        residue.id = (' ', number, ' ')
    mock.write_pdb(structure, source)
    return source


def _csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('chain_id', ['=', "'", '@'])
def test_real_wizard_export_preserves_signed_labels_and_literal_apostrophe(tmp_path, chain_id):
    source = _source(tmp_path, chain_id)
    original = source.read_bytes()
    wizard = WizardSession(tmp_path / 'exports')
    try:
        loaded = wizard.load({'filename': 'original.pdb', 'content': source.read_text()})
        analyzed = wizard.analyze({'input_id': loaded['input_id'], 'target': 'A', 'context': []})
        exported = wizard.export({'selection_id': analyzed['selection_id'], 'acknowledge': True,
                                  'hotspot_mode': 'none', 'hotspots': []})
        root = Path(exported['export_dir'])
        raw = _csv_rows(root / 'residue_mapping.csv')
        assert raw[0][EXACT_TEXT_COLUMN].startswith('{')
        assert raw[0]['source_residue'] == "'-5"
        assert all(not value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@'))
                   for row in raw for value in row.values())
        rows = ResultReader(root).residue_mapping()
        assert rows[0]['author_chain'] == chain_id
        assert rows[0]['source_chain'] == chain_id
        assert rows[0]['source_residue'] == '-5'
        assert rows[0]['output_position'] == 1
        assert EXACT_TEXT_COLUMN not in rows[0]
        assert (root / 'source.pdb').read_bytes() == original
        assert source.read_bytes() == original
    finally:
        wizard.close()


@pytest.mark.parametrize('chain_id', ['=', "'"])
def test_real_platform_region_rewrite_is_reversible(tmp_path, chain_id):
    source = _source(tmp_path, chain_id)
    original = source.read_bytes()
    manager = InputManager(tmp_path / 'workspace')
    try:
        loaded = manager.load({'filename': 'original.pdb', 'content': source.read_text()})
        analyzed = manager.analyze({'input_id': loaded['input_id'], 'target': 'A', 'context': []})
        reviewed = manager.review({'selection_id': analyzed['selection_id'], 'acknowledge': True,
                                   'hotspot_mode': 'none', 'hotspots': [], 'mode': 'screen', 'mock': True,
                                   'target_region': {'start': 2, 'end': 10}})
        root = Path(reviewed['config_path']).parent
        rows = ResultReader(root).residue_mapping()
        assert rows[0]['source_residue'] == '-4'
        assert rows[0]['author_chain'] == chain_id
        assert rows[0]['source_chain'] == chain_id
        assert rows[0]['output_position'] == 1
        assert rows[0]['reference_position'] == 2
        assert _csv_rows(root / 'residue_mapping.csv')[0]['source_residue'] == "'-4"
        manager.reviewed(reviewed['input_id'])
        assert source.read_bytes() == original
    finally:
        manager.close()


@pytest.mark.parametrize('text', ['=formula()', '-5', "'=literal", '0010', 'nan', 'false', "'"])
def test_exact_text_receipt_distinguishes_formula_escape_from_original_text(tmp_path, text):
    original = {'author_chain': text, 'source_chain': text, 'source_residue': text, 'output_chain': 'A', 'output_position': 1}
    path = tmp_path / 'residue_mapping.csv'
    write_residue_mapping(path, [original])
    exact = read_residue_mapping(path)
    assert exact == [{**original, 'output_position': '1'}]
    decoded = ResultReader(tmp_path).residue_mapping()[0]
    assert decoded['author_chain'] == decoded['source_chain'] == decoded['source_residue'] == text
    assert decoded['output_position'] == 1
    # The metadata is fully contained in the copied CSV; no sidecar is needed.
    run = tmp_path / 'copied/01_target'
    run.mkdir(parents=True)
    (run / path.name).write_bytes(path.read_bytes())
    assert ResultReader(run.parent).residue_mapping() == [decoded]


@pytest.mark.parametrize('metadata', ['{broken', '{}', '{"source_residue":"different"}'])
def test_corrupt_exact_text_receipt_fails_closed(tmp_path, metadata):
    path = tmp_path / 'residue_mapping.csv'
    write_residue_mapping(path, [{'source_residue': '-5', 'output_position': 1}])
    rows = _csv_rows(path)
    rows[0][EXACT_TEXT_COLUMN] = metadata
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        read_residue_mapping(path)
    reader = ResultReader(tmp_path)
    assert reader.residue_mapping() == []
    assert any('CSV' in issue for issue in reader.issues)


def test_legacy_mapping_keeps_original_apostrophes_without_decoding(tmp_path):
    path = tmp_path / 'residue_mapping.csv'
    original = {'author_chain': "'=literal", 'source_residue': '-5', 'output_position': '1'}
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(original))
        writer.writeheader()
        writer.writerow(original)
    assert read_residue_mapping(path) == [original]
    assert ResultReader(tmp_path).residue_mapping()[0]['author_chain'] == "'=literal"
    assert ResultReader(tmp_path).residue_mapping()[0]['source_residue'] == '-5'
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')


def test_unsupported_mapping_encoding_is_not_guessed(tmp_path):
    path = tmp_path / 'residue_mapping.csv'
    path.write_text('author_chain,_nanocofold_exact_text_v2\nA,{}\n')
    with pytest.raises(ValueError, match='Unsupported'):
        read_residue_mapping(path)


def test_sequence_prediction_mapping_writer_preserves_original_author_labels(tmp_path):
    source = _source(tmp_path, '=')
    original = source.read_bytes()
    manager = InputManager(tmp_path / 'workspace')
    try:
        loaded = manager.load({'filename': 'original.pdb', 'content': source.read_text()})
        analyzed = manager.analyze({'input_id': loaded['input_id'], 'target': 'A', 'context': []})
        reviewed = manager.review({'selection_id': analyzed['selection_id'], 'acknowledge': True,
                                   'mode': 'screen', 'mock': True, 'preparation': 'sequence_prediction',
                                   'provider': 'boltz2', 'prediction_sequences': '>A\nACDEFGHIKLMNPQRSTVWY',
                                   'prediction_hotspots': 'auto'})
        root = Path(reviewed['config_path']).parent
        rows = ResultReader(root).residue_mapping()
        assert rows[0]['source_chain'] == rows[0]['author_chain'] == '='
        assert rows[0]['source_residue'] == '-5'
        assert rows[0]['source_standard_observed'] is True
        assert _csv_rows(root / 'residue_mapping.csv')[0]['source_chain'] == "'="
        manager.reviewed(reviewed['input_id'])
        assert source.read_bytes() == original
    finally:
        manager.close()
