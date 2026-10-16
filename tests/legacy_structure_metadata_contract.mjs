import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const adapter=readFileSync(new URL('../nanocofold/templates/result-molstar.js',import.meta.url),'utf8');
const ui=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function fixture(data,coordinateIds=['A','B']){
 const components=[],visibility=new Map(),root={},loaded=[];
 const structure={obj:{data:{root,models:[{atomicHierarchy:{chains:{auth_asym_id:{rowCount:coordinateIds.length,value:index=>coordinateIds[index]}}}}]}}};
 const plugin={layout:{state:{regionState:{}},setProps(){}},state:{data:{updateCellState:(ref,state)=>visibility.set(ref,state.isHidden)}},
  behaviors:{interaction:{click:{}}},managers:{camera:{reset(){}},interactivity:{lociSelects:{deselectAll(){},select(){}},lociHighlights:{clearHighlights(){}}},structure:{focus:{clear(){}}}}};
 const viewer={plugin,handleResize(){},subscribe(){},dispose(){}};
 const context={window:{},molstar:{Viewer:{create:async()=>viewer},lib:{structure:{
  StructureElement:{Loci:{is:()=>true,getFirstLocation:loci=>loci.location,fromSchema:(_structure,schema)=>schema,isEmpty:()=>false}},
  StructureProperties:{chain:{auth_asym_id:location=>location.chain},residue:{auth_seq_id:location=>location.number,pdbx_PDB_ins_code:location=>location.icode||''}}}}},cancelAnimationFrame(){}};
 vm.createContext(context);vm.runInContext(adapter,context);
 const Class=context.window.NanoCoFoldResultViewer;
 Class.prototype.load=async function(content,format){loaded.push({content,format});return structure;};
 Class.prototype.component=async function(_structure,schema,key,label,color,type){
  components.push({schema,key,label,color,type});const ref=key+':'+components.length;
  if(!this.representations.has(key))this.representations.set(key,[]);this.representations.get(key).push({ref});
 };
 return {Class,components,visibility,loaded,root};
}
const variants=[['absent',{}],['empty',{chains:[]}],['IDs only',{chains:[{id:'A'},{id:'B'}]}],
 ['partial',{chains:[{id:'A',residues:[{label:'A:1',name:'GLY'}]},{id:'B'}]}]];
for(const [name,metadata]of variants){
 test(`legacy ${name}: draw actual coordinate chains neutrally and preserve native bytes/PAE/metadata`,async()=>{
  const data={candidate_id:'완료',content:'exact native PDB bytes\r\n',format:'pdb',target_chain:'A',binder_chain:'B',
   pae:{labels:['A:1','B:52A'],values:[[0,.125],[3.75,0]]},...metadata};
  const before=JSON.stringify(data),f=fixture(data),viewer=await f.Class.create({},data,()=>{});
  assert.equal(viewer.data,data);assert.equal(JSON.stringify(data),before);assert.equal(f.loaded[0].content,data.content);
  assert.equal(viewer.metadataIncomplete,true);assert.equal(f.components.length,2);
  assert.deepEqual(f.components.map(component=>component.key),['unclassified','unclassified']);
  assert.deepEqual(f.components.map(component=>component.schema.auth_asym_id),['A','B']);
  assert.ok(f.components.every(component=>component.color===0x909ea9));
  await viewer.layers({target:false,binder:false,context:false});
  assert.ok([...f.visibility.values()].every(hidden=>hidden===false),'unknown roles cannot disappear behind a saved context=false');
  const schema=viewer.schema(['B:52A'],'CA');assert.equal(schema.items[0].auth_asym_id,'B');assert.equal(schema.items[0].auth_seq_id,52);assert.equal(schema.items[0].pdbx_PDB_ins_code,'A');
  const location=viewer.location({structure:{root:f.root},location:{chain:'B',number:52,icode:'A'}});
  assert.equal(location.label,'B:52A');assert.equal(location.mapping,null);
  await viewer.display({sequence:true,residues:true});assert.equal(f.components.length,2,'no fabricated metadata-dependent atom layers');
  assert.equal(JSON.stringify(data),before);
 });
}
test('rich metadata keeps payload identity, recorded roles, components and layer behavior',async()=>{
 const data={content:'saved bytes',format:'pdb',chains:[{id:'A',role:'target',residues:[{label:'A:1'}]},
  {id:'B',role:'binder',residues:Array.from({length:5},(_,index)=>({label:'B:'+(index+1)}))}]};
 const before=JSON.stringify(data),f=fixture(data),viewer=await f.Class.create({},data,()=>{});
 assert.equal(viewer.data,data);assert.equal(viewer.metadataIncomplete,false);
 assert.deepEqual(f.components.map(component=>[component.key,component.type]),[['target','ball-and-stick'],['binder','cartoon']]);
 await viewer.layers({target:false,binder:true,context:false});
 for(const [ref,hidden]of f.visibility)assert.equal(hidden,ref.startsWith('target:'));
 assert.equal(JSON.stringify(data),before);
});
test('coordinate chains absent from partial metadata remain visible without assigning target/binder roles',async()=>{
 const data={content:'saved bytes',format:'pdb',chains:[{id:'A',role:'target',residues:[{label:'A:1'}]}]};
 const before=JSON.stringify(data),f=fixture(data),viewer=await f.Class.create({},data,()=>{});
 assert.deepEqual(f.components.map(component=>component.key),['target','unclassified']);
 assert.equal(viewer.metadataIncomplete,true);await viewer.layers({target:false,binder:false,context:false});
 assert.equal([...f.visibility.entries()].find(([ref])=>ref.startsWith('unclassified:'))[1],false);
 assert.equal(JSON.stringify(data),before);
});
test('target/binder controls require recorded role and residue metadata, not IDs alone',()=>{
 const context={state:{},};vm.createContext(context);
 vm.runInContext(ui.slice(ui.indexOf('function resultChain('),ui.indexOf('function syncResultChainControls(')),context);
 for(const [,metadata]of variants){context.state.structure={target_chain:'A',binder_chain:'B',...metadata};
  assert.equal(context.resultChain('target'),undefined);assert.equal(context.resultChain('binder'),undefined);}
 const target={id:'A',role:'target',residues:[{label:'A:1'}]};context.state.structure={target_chain:'A',chains:[target]};
 assert.equal(context.resultChain('target'),target);
});

for (const [surface, source] of [['shared UI', ui], ['embedded offline UI', readFileSync(new URL('../nanocofold/templates/platform_offline.html', import.meta.url), 'utf8')]]) {
 const measure=source.slice(source.indexOf('  action("measure-result-distance",'),source.indexOf('  action("export-result",'));
 function distanceFixture(structure) {
  const calls=[], context={state:{structure}, $:id=>({value:id==='residue-a'?'B:1':'B:2'}),
   focusStructure:async(labels,text)=>calls.push({focus:[...labels],text}),
   action:(_name,callback)=>{context.measure=callback;}};
  context.state.viewer={distance:async(...labels)=>calls.push({distance:labels})};
  vm.createContext(context);vm.runInContext(measure,context);
  return {context,calls};
 }
 test(`${surface}: partial legacy metadata retains exact later-chain CA measurement without modifying records`,async()=>{
  const structure={chains:[null,{id:'A'},{id:'C',residues:{label:'C:1'}},{id:'B',residues:[null,{},
   {label:'B:1',ca:[0,0,0]},{label:'B:2',ca:[0,3,4]}]}]};
  const before=JSON.stringify(structure),{context,calls}=distanceFixture(structure);
  await context.measure();
  assert.deepEqual(calls,[{focus:['B:1','B:2'],text:'B:1:CA – B:2:CA · 5.000 Å'},{distance:['B:1','B:2']}]);
  assert.equal(JSON.stringify(structure),before);
 });
 for (const [name,structure] of [['absent chains',{}],['missing residues',{chains:[{id:'A'}]}],
  ['recorded labels without CA',{chains:[{id:'B',residues:[{label:'B:1'},{label:'B:2'}]}]}]]) {
  test(`${surface}: ${name} gives the explicit missing-CA error without inventing measurements`,async()=>{
   const before=JSON.stringify(structure),{context,calls}=distanceFixture(structure);
   await assert.rejects(context.measure(),/Cα 좌표가 없는 잔기입니다\./);
   assert.deepEqual(calls,[]);assert.equal(JSON.stringify(structure),before);
  });
 }
}
