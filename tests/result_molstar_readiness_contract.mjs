import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../nanocofold/templates/result-molstar.js', import.meta.url), 'utf8');

function fixture({blocked = null, failed = null} = {}) {
  const calls = [], visibility = new Map();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const plugin = {
    behaviors: {interaction: {click: {}}},
    layout: {state: {regionState: {}}, setProps() {}},
    state: {data: {updateCellState(ref, value) { visibility.set(ref, value.isHidden); }}},
    managers: {camera: {reset() {}}},
  };
  let disposals = 0;
  const window = {}, phases = [];
  const molstar = {Viewer: {create: async () => ({plugin, subscribe() {}, handleResize() {}, dispose() { disposals++; }})}};
  vm.runInNewContext(source, {window, molstar, cancelAnimationFrame() {}});
  const Viewer = window.NanoCoFoldResultViewer;
  Viewer.prototype.load = async function(content) { calls.push({kind: 'coordinates', content}); return {}; };
  Viewer.prototype.addCoordinateChains = function() {};
  Viewer.prototype.schema = labels => ({labels});
  Viewer.prototype.component = async function(structure, schema, key, label, color, type, alpha = 1) {
    calls.push({kind: 'component', key, schema, color, type, alpha});
    if (key === blocked) await gate;
    if (key === failed || (typeof failed === 'function' && failed(key, label))) throw Error('synthetic representation failure');
    const list = this.representations.get(key) || [];
    list.push({ref: key + '-' + list.length}); this.representations.set(key, list);
  };
  const data = {content: 'synthetic coordinate token', format: 'pdb',
    chains: [{id: 'A', role: 'target', residues: [{}, {}, {}, {}]}, {id: 'B', role: 'binder', residues: [{}, {}, {}, {}]}],
    hotspots: ['A:1'], contacts: [{a: 'A:1', b: 'B:1'}], clashes: [{a: 'A:2', b: 'B:2'}],
    cdr: {ranges: {CDR1: {labels: ['B:1']}, CDR2: {labels: ['B:2']}}},
    reference: {available: true, content: 'synthetic reference token', format: 'pdb'}};
  return {calls, visibility, data, release, disposals: () => disposals,
    create: () => Viewer.create({}, data, () => {}, phase => phases.push(phase)), phases};
}

test('inactive optional layers cannot hold the base viewer readiness', async () => {
  const f = fixture({blocked: 'hotspots'}), before = JSON.stringify(f.data);
  let viewer;
  const pending = f.create().then(value => { viewer = value; });
  try {
    await new Promise(setImmediate);
    assert.ok(viewer, 'Base readiness must not await an inactive overlay');
    assert.deepEqual(f.calls.filter(c => c.kind === 'component').map(c => c.key), ['target', 'binder']);
    assert.equal(f.calls.filter(c => c.kind === 'coordinates').length, 1);
    assert.equal(JSON.stringify(f.data), before);
  } finally { f.release(); await pending; viewer?.dispose(); }
});

test('visible optional layers load once, preserve their styles and obey atom visibility', async () => {
  const f = fixture(), viewer = await f.create(), before = JSON.stringify(f.data);
  await viewer.layers({target: true, binder: true, contacts: true});
  assert.equal(f.calls.some(c => c.key === 'contacts'), false);
  await viewer.display({sequence: false, residues: true});
  assert.equal(f.calls.filter(c => c.key === 'contacts').length, 1);
  await viewer.layers({target: true, binder: true, contacts: true, cdr: true, reference: true});
  assert.equal(f.calls.filter(c => c.key === 'cdr').length, 2);
  assert.equal(f.calls.filter(c => c.key === 'reference').length, 1);
  assert.equal(f.calls.find(c => c.key === 'reference').alpha, 0.35);
  assert.equal(f.calls.filter(c => c.kind === 'coordinates').length, 2);
  const count = f.calls.length;
  await viewer.layers({target: true, binder: true});
  await viewer.layers({target: true, binder: true, contacts: true, cdr: true, reference: true});
  assert.equal(f.calls.length, count);
  assert.equal(f.visibility.get('reference-0'), false);
  await viewer.display({sequence: false, residues: false});
  assert.equal(f.visibility.get('contacts-0'), true);
  assert.equal(f.visibility.get('reference-0'), false);
  assert.equal(JSON.stringify(f.data), before);
  viewer.dispose();
});

test('an optional rendering failure remains observable when that layer is requested', async () => {
  const f = fixture({failed: 'reference'}), viewer = await f.create();
  await assert.rejects(viewer.layers({target: true, reference: true}), /synthetic representation failure/);
  assert.equal(viewer.representations.has('reference'), false);
  viewer.dispose();
});

test('base rendering failures reject readiness and release the viewer', async () => {
  const f = fixture({failed: 'binder'});
  await assert.rejects(f.create(), /synthetic representation failure/);
  assert.equal(f.disposals(), 1);
});

test('retry after a partial optional failure does not duplicate finished geometry', async () => {
  let fail = true;
  const f = fixture({failed: (key, label) => key === 'cdr' && label === 'CDR2' && fail});
  const viewer = await f.create();
  await assert.rejects(viewer.layers({target: true, cdr: true}), /synthetic representation failure/);
  assert.equal(viewer.representations.get('cdr').length, 1);
  fail = false;
  await viewer.layers({target: true, cdr: true});
  assert.equal(viewer.representations.get('cdr').length, 2);
  assert.equal(f.calls.filter(c => c.key === 'cdr').length, 3);
  viewer.dispose();
});

test('queued toggles reuse a pending optional layer and finish at the last request', async () => {
  const f = fixture({blocked: 'reference'}), viewer = await f.create();
  const showing = viewer.layers({target: true, reference: true});
  await new Promise(setImmediate);
  const hiding = viewer.layers({target: true, reference: false});
  f.release();
  await Promise.all([showing, hiding]);
  assert.equal(f.calls.filter(c => c.key === 'reference').length, 1);
  assert.equal(f.visibility.get('reference-0'), true);
  viewer.dispose();
});
