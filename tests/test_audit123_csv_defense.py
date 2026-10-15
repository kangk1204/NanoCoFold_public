"""All CSV producers share the safe publication boundary; no model execution."""
import ast
import csv
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from nanocofold.utils.atomic import write_csv


def test_wt_dash_marker_round_trips_but_minus_formulas_remain_text(tmp_path):
    from nanocofold.utils.spreadsheet import csv_cell
    array = np.array([1., -2.])
    assert csv_cell(array) is array and csv_cell(pd.NA) is pd.NA
    frame = pd.DataFrame({'wt_aa': ['-', 'A', '-'], 'mut_aa': ['-', 'C', '-'],
                          'note': ['-1+1', '-name', '+1+1'], 'score': [-2., 0., np.nan]})
    path = tmp_path/'library.csv'
    write_csv(path, frame)
    saved = pd.read_csv(path)
    pd.testing.assert_frame_equal(saved[['wt_aa', 'mut_aa', 'score']], frame[['wt_aa', 'mut_aa', 'score']])
    assert saved.note.tolist() == ["'-1+1", "'-name", "'+1+1"]


@pytest.mark.parametrize('dtype', ['object', 'string', 'category'])
def test_csv_text_defense_preserves_ids_numeric_values_and_source(tmp_path, dtype):
    frame = pd.DataFrame({'id': ['007', '나노바디2(A3)', 'sp|P01308|INS_HUMAN'],
                          'note': pd.Series(['=1+1', ' \t@note', "'=already-text"], dtype=dtype),
                          'score': [-1.234567890123, np.nan, np.inf], 'eligible': [True, False, False]})
    original = frame.copy(deep=True)
    path = tmp_path/'stage.csv'
    write_csv(path, frame)
    values = list(csv.DictReader(path.open()))
    assert [r['id'] for r in values] == original.id.tolist()
    assert [r['note'] for r in values] == ["'=1+1", "' \t@note", "'=already-text"]
    loaded = pd.read_csv(path, dtype={'id': str})
    assert loaded.id.tolist() == original.id.tolist()
    np.testing.assert_equal(loaded.score.to_numpy(), frame.score.to_numpy())
    assert loaded.eligible.tolist() == frame.eligible.tolist()
    pd.testing.assert_frame_equal(frame, original)


def test_csv_headers_and_failed_publication_are_safe(tmp_path, monkeypatch):
    path = tmp_path/'stage.csv'
    frame = pd.DataFrame({'=label': ['safe'], 'score': [-2.5]})
    write_csv(path, frame)
    assert next(csv.reader(path.open())) == ["'=label", 'score']
    before = path.read_bytes()
    def interrupted(self, handle, **kwargs):
        handle.write(b'partial')
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(pd.DataFrame, 'to_csv', interrupted)
    with pytest.raises(OSError, match='synthetic disk failure'):
        write_csv(path, frame)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.publish-*'))


def test_no_csv_producer_bypasses_safe_publication():
    import nanocofold
    root = Path(nanocofold.__file__).parent
    bypasses = []
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'to_csv':
                if path != root/'utils/atomic.py':
                    bypasses.append(str(path.relative_to(root))+':'+str(node.lineno))
    assert not bypasses, bypasses


@pytest.mark.parametrize('kind', ['variants', 'sequences'])
def test_standalone_esm_script_uses_safe_csv_without_model_execution(tmp_path, monkeypatch, kind):
    import nanocofold
    script = Path(nanocofold.__file__).parent/'scripts/esm_llr.py'
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'esm', SimpleNamespace())
    spec = importlib.util.spec_from_file_location('standalone_esm_csv_test', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_log_probs', lambda *_: np.array([[0., 0.], [-2., -3.]]))
    source = tmp_path/'input.csv'; output = tmp_path/'output.csv'
    frame = pd.DataFrame({'variant_id': ['Nb1'], 'design_id': ['Nb1'], 'sequence': ['A'],
                          'position': [1], 'wt_aa': ['A'], 'mut_aa': ['C'], 'note': ['=1+1']})
    frame.to_csv(source, index=False)
    args = SimpleNamespace(variants=str(source), sequences=str(source), output=str(output), wt_sequence='A',
                           reference_sequence='A', model='synthetic-unused-model')
    alphabet = SimpleNamespace(tok_to_idx={'A': 0, 'C': 1})
    getattr(module, 'run_'+kind)(args, None, alphabet, 'cpu')
    rows = list(csv.DictReader(output.open()))
    assert rows[0]['note'] == "'=1+1"
    assert rows[0]['design_id'] == 'Nb1'
    assert float(rows[0]['esm_llr' if kind == 'variants' else 'esm_mean_logprob']) == (-1. if kind == 'variants' else -2.)
