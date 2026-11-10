import base64
import copy
import gzip
import hashlib
import json
import struct

import pytest

from nanocofold.platform.offline_assets import compact_payload
from nanocofold.platform.report_evidence import attach_evidence


def test_compact_assets_preserve_native_bytes_and_deduplicate_observations():
    raw = struct.pack('<4d', 0., .12345678912345678, 30., 1.23456789012345)
    digest = hashlib.sha256(raw).hexdigest()
    structure = {'candidate_id': '변이', 'observation_id': None, 'content': 'ATOM /home/native\n',
                 'raw_content': 'ATOM /home/native\n', 'format': 'pdb', 'seed': 42,
                 'pae': {'labels': ['A:1', 'B:1'], 'values_ref': digest}}
    payload = {'structures': [structure, {**structure, 'observation_id': 'seed42'}],
               'pae_arrays': {digest: {'encoding': 'gzip-base64-float64le', 'bytes': len(raw),
                   'dimension': 2, 'sha256': digest, 'data': base64.b64encode(gzip.compress(raw)).decode()}}}
    before = copy.deepcopy(payload)
    result = compact_payload(payload, min_bytes=0)
    assert payload == before
    assert len(result['structure_assets']) == 1
    for binding in result['structures']:
        record = result['structure_assets'][binding['structure_ref']]
        restored = gzip.decompress(base64.b64decode(record['data']))
        assert len(restored) == record['bytes'] and hashlib.sha256(restored).hexdigest() == record['sha256']
        body = json.loads(restored)
        assert body['content'] == structure['content'] and body['raw_content_ref'] == 'content'
        assert binding['seed'] == 42
    packed = result['pae_arrays'][digest]
    shuffled = gzip.decompress(base64.b64decode(packed['data']))
    restored = bytearray(len(raw))
    for lane in range(8):
        restored[lane::8] = shuffled[lane*4:(lane+1)*4]
    assert bytes(restored) == raw
    assert compact_payload(result, min_bytes=0) is result
    assert compact_payload(payload) is payload
    payload['pae_arrays'][digest]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash'):
        compact_payload(payload, min_bytes=0)


def test_evidence_binding_rejects_other_runs_sequences_and_modified_receipts(tmp_path):
    h = lambda text: hashlib.sha256(text.encode()).hexdigest()
    payload = {'summary': {'state': {'phases': {'target': {'sequence': 'ACD'}}}},
               'candidates': {'rows': [{'candidate_id': 'WT', 'sequence': 'DEF', 'selected': False}]},
               'structures': [], 'provenance': {}}
    evidence = {'target_sequence_sha256': h('ACD'), 'method': 'shared_parent_homologs',
                'candidates': [{'candidate_id': 'WT', 'sequence_sha256': h('DEF')}]}
    path = tmp_path / 'msa.json'; path.write_text(json.dumps(evidence))
    manifest = {'schema_version': 1, 'source_report_sha256': 'a'*64,
                'msa': {'path': 'msa.json', 'sha256': h(path.read_text())}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    original = copy.deepcopy(payload)
    result = attach_evidence(payload, tmp_path, 'a'*64)
    assert payload == original and result['candidates'] == original['candidates']
    assert result['summary']['msa_provenance'] == evidence
    with pytest.raises(ValueError, match='different source'):
        attach_evidence(payload, tmp_path, 'b'*64)
    payload['candidates']['rows'][0]['sequence'] = 'AAA'
    with pytest.raises(ValueError, match='identities'):
        attach_evidence(payload, tmp_path, 'a'*64)
    path.write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        attach_evidence(payload, tmp_path, 'a'*64)
