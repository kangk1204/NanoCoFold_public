import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const context = {window:{}};
vm.runInNewContext(readFileSync(new URL('../nanocofold/templates/result-molstar.js', import.meta.url), 'utf8'), context);
const Viewer = context.window.NanoCoFoldResultViewer;
const data = {binder_chain:'Y', chains:[{id:'Y', role:'binder', residues:[
  {name:'ALA',label:'Y:-2'}, {name:'CYS',label:'Y:10'}, {name:'ASP',label:'Y:10A'}, {name:'GLU',label:'Y:35'},
]}]};
const mapped = (sequence, structure=data) => Array.from(Viewer.sequenceResidues(sequence, structure));
test('sequence positions use saved labels, including numbering gaps and insertion codes', () => {
  const before = JSON.stringify(data);
  assert.deepEqual(mapped('ACDE'), ['Y:-2','Y:10','Y:10A','Y:35']);
  assert.equal(JSON.stringify(data), before);
});
test('an unambiguous missing residue does not shift the following labels', () => {
  assert.deepEqual(mapped('ACFDE'), ['Y:-2','Y:10',null,'Y:10A','Y:35']);
});
test('ambiguous repeated residues are not guessed', () => {
  assert.deepEqual(mapped('ACCDE'), ['Y:-2',null,null,'Y:10A','Y:35']);
});
test('wrong chain role, missing metadata and sequence mismatch disable linking', () => {
  for (const structure of [null, {chains:{}}, {...data,binder_chain:'A'}, {binder_chain:'Y',chains:[{...data.chains[0],role:'target'}]}, {binder_chain:'Y',chains:[{...data.chains[0],residues:[null]}]}])
    assert.deepEqual(mapped('ACDE', structure), [null,null,null,null]);
  assert.deepEqual(mapped('ACDF'), [null,null,null,null]);
});
test('a selected range reveals the binder and applies a moderate camera radius', async () => {
  const calls=[], owner=Object.create(Viewer.prototype);
  Object.assign(owner,{data,activeLayers:{binder:false,target:true},text:(_ko,en)=>en,
    layers:async value=>calls.push(['layers',value]), focus:async (...args)=>calls.push(['focus',...args])});
  assert.deepEqual(Array.from(await owner.focusSequence('ACDE',1,3)), ['Y:10','Y:10A']);
  assert.equal(calls[0][1].binder,true);
  assert.deepEqual(Array.from(calls[1][1]), ['Y:10','Y:10A']);
  assert.equal(calls[1][2].minRadius,10);
  await assert.rejects(owner.focusSequence('ACFDE',1,4), /cannot be mapped/);
  assert.equal(calls.length,2);
});
for (const interruption of ['disposed','newer selection']) test('pending sequence focus cannot overwrite ' + interruption, async () => {
  let release; const calls=[], owner=Object.create(Viewer.prototype);
  Object.assign(owner,{data,activeLayers:{},text:(_ko,en)=>en,layers:()=>new Promise(resolve=>{release=resolve;}),focus:async labels=>calls.push(labels)});
  const pending=owner.focusSequence('ACDE',0,1);
  if (interruption==='disposed') owner.disposed=true; else owner.sequenceFocusRequest++;
  release(); assert.equal(await pending,null); assert.deepEqual(calls,[]);
});
