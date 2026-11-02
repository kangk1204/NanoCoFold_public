import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {koreanContext} from './i18n_context.mjs';

const source=readFileSync(process.env.NANOCOFOLD_VIEWER_UI_SOURCE||new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const adapter=readFileSync(process.env.NANOCOFOLD_VIEWER_ADAPTER_SOURCE||new URL('../nanocofold/templates/result-molstar.js',import.meta.url),'utf8');
const defer=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail;});promise.catch(()=>{});return{promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function fixture({failure=false,delayedPlot=false,plotFailure=false,language='ko',pae=null}={}){
 const nodes=new Map(),plots=[],relayouts=[],focused=[],summaries=new Map(),gate=defer(),plotGate=defer();let creates=0,errorStatus='',progress;
 class Element{
  constructor(){this.value='';this.textContent='';this.style={};this.children=[];this.listeners=new Map();}
  replaceChildren(...values){this.children=values;}
  append(...values){this.children.push(...values);}
  appendChild(value){this.append(value);return value;}
  removeAllListeners(name){this.listeners.delete(name);}
  on(name,handler){this.listeners.set(name,handler);}
 }
 const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
 const data={candidate_id:'opaque-candidate',content:'opaque saved coordinate bytes',format:'pdb',
  chains:[],contacts:[],clashes:[],issues:[],pae,pae_status:pae?'available':'removed_by_retention',reference:{available:false}};
 const before=JSON.stringify(data),state={result:{mode:'screen'},candidate:{candidate_id:data.candidate_id},structure:data,viewer:null,viewerData:null,viewerRequest:0};
 const viewer={data,layers:async()=>{},display:async()=>{},dispose(){this.disposed=true;}};
 const plotly={react(node,values,layout){plots.push({node,values,layout});node.data=values;node.layout=layout;return plotFailure?Promise.reject(Error('opaque plot failure')):delayedPlot?plotGate.promise:Promise.resolve();},relayout:async(_node,changes)=>{relayouts.push(changes);}};
 const c={state,$:get,window:{Plotly:plotly},Plotly:plotly,offline:false,CustomEvent:class{},
  document:{documentElement:{},querySelector:()=>new Element(),getElementById:get,dispatchEvent(){}},
  el:()=>new Element(),options(){},table(){},warnings(){},fmt:String,localizedStructureDataNote:()=>'',
  describeResultFigure(id,_label,summary){summaries.set(id,c.I18N.value(summary));},
  setPaeErrorStatus(value){errorStatus=value?c.I18N.value(c.I18N.term(value)):'';},localizeEmptyPaeAnnotation(){},syncResultChainControls(){},
  coordinatePresentation:()=>({caption:'opaque',rawAvailable:true}),resultLayers:()=>({}),resultDisplay:()=>({}),
  focusButton:()=>new Element(),notice(){},focusStructure:async(labels,text)=>focused.push({labels:[...labels],text,viewer:state.viewer}),
  disposeResultViewer(){state.viewerRequest++;state.viewer?.dispose();state.viewer=null;state.viewerData=null;},
  NanoCoFoldResultViewer:{create(_host,_data,_pick,onProgress){creates++;progress=onProgress;return failure?Promise.reject(Error('opaque renderer failure')):gate.promise;}}};
 koreanContext(c);c.I18N.setLanguage(language,{persist:false});
 vm.runInContext(source.slice(source.indexOf('async function renderStructure('),source.indexOf('async function exportResult(')),c);
 vm.runInContext(source.slice(source.indexOf('function localizeEmptyPaeAnnotation('),source.indexOf('async function loadStructure(')),c);
 return{c,state,data,before,viewer,get,gate,plotGate,plots,relayouts,focused,summaries,creates:()=>creates,error:()=>errorStatus,progress:phase=>progress(phase)};
}
for(const language of ['ko','en']){
 const caption=language==='ko'?'PAE · 용량 정리로 삭제됨':'PAE · Removed by storage cleanup';
 test(`retention notice renders while the 3D factory is pending (${language})`,async()=>{
  const f=fixture({language}),work=f.c.renderStructure();await flush();
  try{assert.equal(f.plots.length,1);assert.equal(f.get('pae').layout.annotations[0].text,caption);assert.equal(f.summaries.get('pae'),caption);assert.equal(f.state.viewer,null);assert.equal(JSON.stringify(f.data),f.before);}
  finally{f.gate.resolve(f.viewer);await work;}
 });
 test(`3D factory rejection preserves the PAE retention notice (${language})`,async()=>{
  const f=fixture({language,failure:true});await f.c.renderStructure();
  assert.equal(f.plots.length,1);assert.equal(f.get('pae').layout.annotations[0].text,caption);assert.equal(f.state.structure,f.data);assert.equal(JSON.stringify(f.data),f.before);
 });
}
test('PAE language refresh is safe before any structure or plot error exists',()=>{
 const f=fixture();f.state.structure=undefined;assert.doesNotThrow(()=>f.c.localizeEmptyPaeAnnotation());assert.equal(f.error(),'');
});
test('saved PAE values remain unchanged while 3D is pending',async()=>{
 const pae={labels:['X:1','Y:2'],values:[[0,.125],[3.5,0]]},f=fixture({pae}),work=f.c.renderStructure();await flush();
 try{assert.equal(f.plots.length,1);assert.equal(f.plots[0].values[0].z,pae.values);assert.equal(JSON.stringify(f.data),f.before);}
 finally{f.gate.resolve(f.viewer);await work;}
});
test('a pending PAE plot does not delay the current 3D factory or readiness',async()=>{
 const f=fixture({delayedPlot:true}),work=f.c.renderStructure();await flush();
 let settled=false;work.then(()=>{settled=true;},()=>{});
 try{assert.equal(f.creates(),1);f.gate.resolve(f.viewer);await flush();assert.equal(f.state.viewer,f.viewer);assert.match(f.get('structure-note').textContent,/MolStar/);assert.equal(settled,true);}
 finally{f.plotGate.resolve();f.gate.resolve(f.viewer);await work;await flush();}
});
for(const language of ['ko','en'])test(`a rejected PAE plot reports an error and leaves 3D usable (${language})`,async()=>{
 const f=fixture({plotFailure:true,language}),work=f.c.renderStructure();await flush();
 try{assert.equal(f.creates(),1);assert.match(f.error(),language==='ko'?/PAE 차트를 표시하지 못했습니다/:/The PAE chart could not be displayed/);assert.equal(f.state.paeRenderError.data,f.data);}
 finally{f.gate.resolve(f.viewer);await work;}
 assert.equal(f.state.viewer,f.viewer);assert.equal(JSON.stringify(f.data),f.before);
 const next=language==='ko'?'en':'ko';f.c.I18N.setLanguage(next,{persist:false});f.c.localizeEmptyPaeAnnotation();
 assert.match(f.error(),next==='ko'?/PAE 차트를 표시하지 못했습니다/:/The PAE chart could not be displayed/);
});
for(const reject of [false,true])test(`an old PAE completion cannot replace the current viewer or error: ${reject}`,async()=>{
 const f=fixture({delayedPlot:true}),work=f.c.renderStructure();await flush();
 const replacement={candidate_id:'replacement'},viewer={data:replacement};f.state.structure=replacement;f.state.viewerRequest++;f.state.viewer=viewer;f.state.viewerData=replacement;f.get('structure-note').textContent='current viewer';
 f.gate.resolve(f.viewer);await work;
 if(reject)f.plotGate.reject(Error('stale plot error'));else f.plotGate.resolve();await flush();
 assert.equal(f.creates(),1);assert.equal(f.state.viewer,viewer);assert.equal(f.get('structure-note').textContent,'current viewer');assert.equal(f.error(),'');assert.equal(f.viewer.disposed,true);
});
for(const language of ['ko','en'])test(`3D progress is bilingual and stays with its candidate (${language})`,async()=>{
 const f=fixture({language}),work=f.c.renderStructure();await flush();
 try{f.progress('coordinates');assert.equal(f.get('structure-note').textContent,language==='ko'?'3D 좌표를 읽고 있습니다.':'Reading the 3D coordinates.');f.state.structure={candidate_id:'replacement'};f.get('structure-note').textContent='current viewer';f.progress('layers');assert.equal(f.get('structure-note').textContent,'current viewer');}
 finally{f.gate.resolve(f.viewer);await work;}
 assert.equal(f.viewer.disposed,true);
});
for(const operation of ['focus','zoom','reset'])test(`PAE ${operation} retains its current behavior and ignores a superseded handler`,async()=>{
 const pae={labels:['X:1','Y:2'],values:[[0,.125],[3.5,0]]},f=fixture({pae}),work=f.c.renderStructure();f.gate.resolve(f.viewer);await work;await flush();
 const plot=f.get('pae'),click=plot.listeners.get('plotly_click'),reset=f.get('pae-reset').onclick;
 assert.equal(typeof click,'function');assert.equal(typeof reset,'function');f.relayouts.length=0;
 if(operation==='zoom')plot._fullLayout={xaxis:{range:[0,100]},yaxis:{range:[0,100]}};
 const act=()=>operation==='reset'?reset():click({points:[{x:'X:1',y:'Y:2',z:.125}]});act();await flush();
 if(operation==='focus'){assert.deepEqual(f.focused,[{labels:['X:1','Y:2'],text:'PAE 0.13 Å · X:1 / Y:2',viewer:f.viewer}]);}
 else if(operation==='reset'){assert.equal(JSON.stringify(f.relayouts),JSON.stringify([{'xaxis.autorange':true,'yaxis.autorange':'reversed'}]));}
 else{assert.equal(JSON.stringify(f.relayouts),JSON.stringify([{'xaxis.range':[-.5,1.5],'yaxis.range':[1.5,-.5]}]));}
 f.focused.length=0;f.relayouts.length=0;f.state.structure={candidate_id:'replacement'};f.state.viewer={};act();await flush();
 assert.deepEqual(f.focused,[]);assert.deepEqual(f.relayouts,[]);assert.equal(JSON.stringify(f.data),f.before);
});
test('the actual adapter reports its bootstrap boundary without any WebGL execution',async()=>{
 const gate=defer(),phases=[],context={window:{},molstar:{Viewer:{create:()=>gate.promise}},cancelAnimationFrame(){}};
 vm.createContext(context);vm.runInContext(adapter,context);const Class=context.window.NanoCoFoldResultViewer;
 Class.prototype.load=async()=>({obj:{data:{models:[]}}});Class.prototype.component=async()=>{};Class.prototype.display=async()=>{};Class.prototype.fit=async()=>{};Class.prototype.observeResize=()=>{};
 const work=Class.create({}, {content:'opaque',format:'pdb',chains:[]},()=>{},phase=>phases.push(phase));await flush();
 try{assert.deepEqual(phases,['viewer']);}
 finally{gate.resolve({plugin:{behaviors:{interaction:{click:{}}}},subscribe(){},dispose(){}});await work;}
 assert.deepEqual(phases,['viewer','coordinates','layers','display']);
});
