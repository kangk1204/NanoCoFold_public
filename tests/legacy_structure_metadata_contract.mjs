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
