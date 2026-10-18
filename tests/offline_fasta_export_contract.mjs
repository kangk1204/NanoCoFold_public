import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

for (const file of ['platform.js', 'platform_offline.html']) {
  const source = readFileSync(new URL('../nanocofold/templates/' + file, import.meta.url), 'utf8');
  const action = source.slice(source.indexOf('async function exportResult()'), source.indexOf('function saveBlob('));
  function fixture(data, rows, format = 'fasta') {
    const downloads = [], context = { offline: true, window: { NANOCOFOLD_OFFLINE: data },
      state: { rows, filtered: [] }, $: () => ({ value: format }), Blob, filters: () => ({ search: 'no match' }),
      saveBlob: (blob, name) => downloads.push({ blob, name }) };
    vm.createContext(context); vm.runInContext(action, context);
    return { context, downloads };
  }
  test(`${file}: complex FASTA contains analyzed chains and copies despite sequence-free model rows`, async () => {
    const fasta = '>A 단백질 copies original_positions=-2,-1,1,5\nCDEF\n>B 단백질 copies original_positions=-2,-1,1,5\nCDEF\n';
    const data = { summary: { mode: 'complex' }, sequences_fasta: fasta };
    const rows = [{ candidate_id: 'seed42' }, { candidate_id: 'seed123' }];
    const before = JSON.stringify({ data, rows }), f = fixture(data, rows);
    await f.context.exportResult();
    assert.equal(f.downloads.length, 1); assert.equal(f.downloads[0].name, 'candidates.fasta');
    assert.equal(await f.downloads[0].blob.text(), fasta); assert.equal(JSON.stringify({ data, rows }), before);
  });
  test(`${file}: missing complex sequence records produce an error instead of an empty FASTA`, async () => {
    const f = fixture({ summary: { mode: 'complex' } }, [{ candidate_id: 'seed42' }]);
    await assert.rejects(f.context.exportResult(), /sequences.fasta/); assert.equal(f.downloads.length, 0);
  });
  test(`${file}: legacy candidate payload exports all sequences despite an empty display filter`, async () => {
    const rows = [{ candidate_id: 'one', sequence: 'ACDE' }, { candidate_id: 'two', sequence: 'FGHI' }];
    const f = fixture({ summary: { mode: 'screen' } }, rows); await f.context.exportResult();
    assert.equal(await f.downloads[0].blob.text(), '>one\nACDE\n>two\nFGHI\n');
  });
  test(`${file}: JSON still retains complete payload and the current filtered identity`, async () => {
    const data = { summary: { mode: 'complex' }, sequences_fasta: '>A\nAC\n', provenance: { source: 'saved' } };
    const f = fixture(data, [{ candidate_id: 'seed42' }], 'json'); await f.context.exportResult();
    const saved = JSON.parse(await f.downloads[0].blob.text());
    assert.equal(saved.sequences_fasta, data.sequences_fasta); assert.deepEqual(saved.provenance.filtered_ids, []);
  });
}
