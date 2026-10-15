import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source = readFileSync(process.env.NANOCOFOLD_UI_SOURCE || new URL('../nanocofold/templates/platform.js', import.meta.url), 'utf8');
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
function fixture({offline = false} = {}) {
  const nodes = new Map(['input', 'jobs', 'results'].map(name => ['view-' + name, {id: 'view-' + name, hidden: name !== 'jobs'}]));
  const location = {href: 'http://localhost/?view=results&job=a&result=saved#candidate'};
  Object.defineProperty(location, 'search', {get: () => new URL(location.href).search});
  const writes = [], pending = defer();
  const context = {offline, URL, URLSearchParams,
    state: {navigationReady: true, navigationRequest: 0, currentView: 'jobs', job: 'b', jobs: [{id:'a'}, {id:'b'}]},
    $: id => nodes.get(id), document: {querySelectorAll: selector => selector === 'main>section' ? [...nodes.values()] : []},
    window: {location, history: Object.fromEntries(['pushState', 'replaceState'].map(method => [method, (_state, _title, url) => {
      writes.push({method, url: String(url)}); location.href = String(url);
    }]))},
    refreshJobs() {}, refreshResults() {},
    selectJob: async id => { context.state.job = id; await pending.promise; },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function rememberLocation('), source.indexOf('const bytesLabel')), context);
  return {context, pending, location, writes, nodes};
}

test('later history restoration wins over an older delayed job selection', async () => {
  const f = fixture(), old = f.context.restoreNavigation();
  f.location.href = 'http://localhost/?view=jobs&job=a&result=saved#candidate';
  await f.context.restoreNavigation();
  f.pending.resolve(); await old;
  assert.equal(f.context.state.currentView, 'jobs');
  assert.equal(f.context.state.job, 'a');
  assert.equal(f.writes.length, 0);
});

test('a URL change rejects the pending snapshot even before its popstate handler runs', async () => {
  const f = fixture(), old = f.context.restoreNavigation();
  f.location.href = 'http://localhost/?view=input&job=a#new';
  f.pending.resolve(); await old;
  assert.equal(f.context.state.currentView, 'jobs');
});

test('explicit tab selection supersedes a pending restoration and keeps result/hash', async () => {
  const f = fixture(), old = f.context.restoreNavigation();
  f.context.view('input');
  f.pending.resolve(); await old;
  assert.equal(f.context.state.currentView, 'input');
  assert.equal(new URL(f.location.href).searchParams.get('view'), 'input');
  assert.equal(new URL(f.location.href).searchParams.get('result'), 'saved');
  assert.equal(new URL(f.location.href).hash, '#candidate');
  assert.equal(f.context.state.job, 'a');
});

test('reselecting the displayed tab invalidates the old restore and synchronizes its URL', async () => {
  const f = fixture(), old = f.context.restoreNavigation();
  f.context.view('jobs');
  f.pending.resolve(); await old;
  assert.equal(f.context.state.currentView, 'jobs');
  assert.equal(new URL(f.location.href).searchParams.get('view'), 'jobs');
  assert.equal(f.writes.length, 1);
});

test('generation protects a same-URL restore superseded by explicit job selection', async () => {
  const f = fixture();
  vm.runInContext(source.slice(source.indexOf('async function selectJob('), source.indexOf('function jobProgressCaption(')), f.context);
  Object.assign(f.context, {I18N: {text() {}, term: x => x}, el: () => ({}), refreshJobs: () => f.pending.promise});
  for (const id of ['job-title', 'job-detail', 'job-actions', 'job-log']) f.nodes.set(id, {replaceChildren() {}});
  f.context.rememberLocation = () => {}; // Keep the URL identical to isolate generation invalidation.
  const old = f.context.restoreNavigation();
  const selected = f.context.selectJob('a');
  f.pending.resolve(); await Promise.all([old, selected]);
  assert.equal(f.context.state.currentView, 'jobs');
  assert.equal(f.writes.length, 0);
});

test('offline view selection never writes browser history', () => {
  const f = fixture({offline: true});
  f.context.view('results');
  assert.equal(f.context.state.currentView, 'results');
  assert.equal(f.writes.length, 0);
});

test('result deep links still select results when no explicit view is present', async () => {
  const f = fixture(); f.context.state.job = 'a';
  f.location.href = 'http://localhost/?result=saved#candidate';
  await f.context.restoreNavigation();
  assert.equal(f.context.state.currentView, 'results');
  assert.equal(f.location.href, 'http://localhost/?result=saved#candidate');
});
