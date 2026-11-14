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

for (const surface of ['platform.js','platform_offline.html']) test(surface + ': observation loading retires old coordinates before sequence input',async()=>{
 const source=readFileSync(new URL('../nanocofold/templates/'+surface,import.meta.url),'utf8'),nodes=new Map();
 const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',selectionStart:0,selectionEnd:1,textContent:'',replaceChildren(){}});return nodes.get(id);};
 const row={candidate_id:'candidate',sequence:'AC'},old={candidate_id:'candidate',observation_id:'old'},next={candidate_id:'candidate',observation_id:'new'},focused=[];
 let resolve;const pending=new Promise(done=>{resolve=done;});
 const c={state:{candidate:row,result:{id:'saved',mode:'screen'},structure:old,structureRequest:0,
  viewer:{data:old,focusSequence:async()=>{focused.push('old');return ['B:10'];}}},$:get,
  I18N:{text:(node,value)=>node.textContent=value,term:x=>x,value:x=>x,format:(s,args)=>s.replace(/\{(\d+)\}/g,(_,i)=>args[Number(i)])},
  document:{querySelector:()=>null},offline:surface.endsWith('.html'),api:()=>pending,offlineStructure:()=>pending,setPaeErrorStatus(){},
  disposeResultViewer(){c.state.viewer=null;},renderStructure:async()=>{c.state.viewer={data:c.state.structure,focusSequence:async()=>{focused.push('new');return ['B:90'];}};},
  window:{NANOCOFOLD_OFFLINE:{structures:[next]}},encodeURIComponent,Blob,navigator:{},saveBlob(){}};
 vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('function showVhhSequence('),source.indexOf('function clearCandidate(')),c);
 vm.runInContext(source.slice(source.indexOf('async function loadStructure('),source.indexOf('function labelSelection(')),c);
 c.showVhhSequence(row);get('observation-select').value='new';const switching=c.loadStructure();
 get('vhh-sequence-value').onpointerup({button:0});for(let n=0;n<5;n++)await Promise.resolve();
 assert.deepEqual(focused,[]);assert.match(get('vhh-structure-feedback').textContent,/기다리세요/);
 resolve(next);await switching;assert.equal(c.state.structure.observation_id,'new');assert.equal(get('vhh-structure-feedback').textContent,'');
 get('vhh-sequence-value').onpointerup({button:0});for(let n=0;n<5;n++)await Promise.resolve();
 assert.deepEqual(focused,['new']);assert.match(get('vhh-structure-feedback').textContent,/B:90/);
});
