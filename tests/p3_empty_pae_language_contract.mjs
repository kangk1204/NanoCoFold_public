import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const templates=new URL('../nanocofold/templates/',import.meta.url);
const platform=readFileSync(new URL('platform.js',templates),'utf8');
const offlineHtml=readFileSync(new URL('platform_offline.html',templates),'utf8');
const catalog=readFileSync(new URL('platform-i18n.js',templates),'utf8');
const offlineSource=offlineHtml.slice(offlineHtml.lastIndexOf('<script>')+8,offlineHtml.lastIndexOf('</script>'));
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const plain=value=>JSON.parse(JSON.stringify(value));

function fixture(source,{status='not_assessed',heatmap=false,missing=false,delayed=false,error=null}={}){
  const nodes=new Map(),events=new Map(),calls=[],pending=[];
  class Element {
    constructor(id=''){this.id=id;this.value='';this.textContent='';this.children=[];this.isConnected=true;this.listeners=new Map();this.style={};this.attributes={};}
    append(...children){this.children.push(...children);}
    appendChild(child){this.append(child);return child;}
    replaceChildren(...children){this.children=children;}
    setAttribute(key,value){this.attributes[key]=value;}
    after(child){nodes.set(child.id,child);this.adjacent??=[];this.adjacent.push(child);}
    addEventListener(type,handler){this.listeners.set(type,handler);}
    emit(type){this.listeners.get(type)?.({target:this});}
    removeAllListeners(){}
    on(){}
  }
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);};
  const lookup=id=>id==='pae-status'&&!nodes.has(id)?null:get(id);
  const document={documentElement:{},getElementById:lookup,querySelector:()=>new Element(),querySelectorAll:()=>[],
    createTreeWalker:()=>({nextNode:()=>false}),addEventListener:(type,handler)=>events.set(type,handler),dispatchEvent:event=>events.get(event.type)?.(event)};
  const setPath=(object,path,value)=>{const parts=path.replace(/\[(\d+)\]/g,'.$1').split('.');let node=object;for(const part of parts.slice(0,-1))node=node[part]??={};node[parts.at(-1)]=value;};
  const plotly={
    react(node,data,layout){
      calls.push({method:'react',node,data,layout});
      const commit=()=>{node.data=data;node.layout=layout;};
      if(delayed){const gate=deferred();pending.push(gate);return gate.promise.then(commit);}
      commit();return Promise.resolve();
    },
    relayout(node,update){calls.push({method:'relayout',update});for(const [path,value]of Object.entries(update))setPath(node.layout,path,value);return Promise.resolve();},
    restyle(node,update){calls.push({method:'restyle',update});for(const trace of node.data)Object.assign(trace,update);return Promise.resolve();},
  };
  const data={candidate_id:'unchanged_완료',content:'saved coordinate bytes',format:'pdb',target_chain:'A',binder_chain:'B',
    chains:[{id:'A',residues:[{label:'A:-1',name:'ALA'}]},{id:'B',residues:[{label:'B:52A',name:'GLY'}]}],
    reference:{available:false},contacts:[],clashes:[],issues:[],pae_status:status,pae_error:error,
    pae:heatmap?{labels:['A:-1','B:52A'],values:[[0,3],[9,0]]}:null};
  const viewer={selectedLabels:['B:52A'],camera:{position:[3,5,8],target:[1,2,3]},layers:async()=>{},display:async()=>{},focus(){throw Error('language must not focus camera');}};
  const state={result:{id:'saved',mode:'screen'},candidate:{candidate_id:data.candidate_id,sequence:'ACDE'},structure:missing?null:data,viewerData:data,viewer,
    observations:[{candidate_id:data.candidate_id,seed:42,iptm:.81}],structureRequest:0,selectedResidues:['B:52A']};
  const payload={summary:state.result,candidates:{rows:[state.candidate]},observations:{rows:state.observations},structures:missing?[]:[data]};
  const c={document,window:{Plotly:plotly,NANOCOFOLD_OFFLINE:payload},Plotly:plotly,state,offline:true,$:lookup,
    el:tag=>new Element(tag),options(){},table(){},warnings(){},resultLayers:()=>({}),resultDisplay:()=>({}),
    localizedStructureDataNote:()=>'',renderScatter(){},syncResultChainControls(){},focusButton:()=>new Element(),
    disposeResultViewer(){state.viewer=null;},notice(){},fmt:String,
    URLSearchParams,location:{search:''},NodeFilter:{SHOW_TEXT:4},localStorage:{getItem:()=>null,setItem(){}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}}};
  vm.createContext(c);vm.runInContext(catalog,c);
  vm.runInContext(source.slice(source.indexOf('function renderDistribution('),source.indexOf('function labelSelection(')),c);
  vm.runInContext(source.slice(source.indexOf('async function renderStructure('),source.indexOf('async function exportResult(')),c);
  c.renderDistribution=()=>{};
  c.I18N.init();
  vm.runInContext(source.slice(source.indexOf('document.addEventListener("nanocofold:language"'),source.lastIndexOf('startPlatform();')),c);
  const change=language=>{get('language-select').value=language;get('language-select').emit('change');assert.equal(c.I18N.language,language);};
  const render=()=>missing?c.loadStructure():c.renderStructure();
  const snapshot=()=>JSON.stringify({payload,structure:state.structure,candidate:state.candidate,observations:state.observations,
    selectedResidues:state.selectedResidues,viewer:state.viewer&&{selectedLabels:state.viewer.selectedLabels,camera:state.viewer.camera},
    observation:get('observation-select').value});
  return {c,get,calls,pending,change,render,snapshot,data};
}

for(const [surface,source]of [['shared UI',platform],['offline embedded UI',offlineSource]]){
  test(`${surface}: source is present`,()=>assert.ok(source.includes('async function renderStructure(')));
  for(const [name,options,english,korean]of [
    ['PAE not evaluated',{},'PAE not evaluated','PAE 미평가'],
    ['PAE removed by retention',{status:'removed_by_retention'},'PAE · Removed by storage cleanup','PAE · 용량 정리로 삭제됨'],
    ['structure unavailable',{missing:true},'Structure / PAE not evaluated','구조 / PAE 미평가'],
  ]){
    test(`${surface}: ${name} follows actual EN → KO → EN change events and preserves plot state`,async()=>{
      const f=fixture(source,options);await f.render();
      const plot=f.get('pae');assert.equal(plot.layout.annotations[0].text,english);
      plot.layout.annotations[0].font={size:17,color:'#135'};plot.layout.shapes=[{type:'line',x0:2,x1:2}];
      const before=f.snapshot(),data=plot.data,annotations=plot.layout.annotations,shapes=plot.layout.shapes;
      for(const [language,text]of [['en',english],['ko',korean],['en',english]]){
        f.change(language);await flush();assert.equal(plot.layout.annotations[0].text,text);
        assert.equal(plot.data,data);assert.equal(plot.layout.annotations,annotations);assert.equal(plot.layout.shapes,shapes);
        assert.deepEqual(plain(plot.layout.annotations[0].font),{size:17,color:'#135'});assert.equal(f.snapshot(),before);
      }
      assert.equal(f.calls.filter(call=>call.method==='react').length,1,'language must not recreate the plot');
    });
    test(`${surface}: ${name} resolves a delayed plot in the latest selected language`,async()=>{
      const f=fixture(source,{...options,delayed:true});const work=f.render();await flush();
      assert.equal(f.pending.length,1);f.change('ko');f.pending[0].resolve();await work;await flush();
      assert.equal(f.get('pae').layout.annotations[0].text,korean);
      const before=f.snapshot();f.change('en');await flush();assert.equal(f.get('pae').layout.annotations[0].text,english);assert.equal(f.snapshot(),before);
    });
  }
  test(`${surface}: long PAE errors use one wrapping bilingual status and clear on success`,async()=>{
    const error='PAE 자료가 손상되어 표시할 수 없습니다. 표와 구조는 계속 확인할 수 있습니다.';
    const english='The PAE data is damaged and cannot be displayed. Tables and structures remain available.';
    const f=fixture(source,{status:'invalid',error});await f.render();
    const plot=f.get('pae'),status=f.get('pae-status'),before=f.snapshot();
    assert.equal(plot.layout.annotations[0].text,'PAE');
    assert.equal(status.attributes.role,'status');assert.equal(status.style.overflowWrap,'anywhere');
    assert.equal(status.style.whiteSpace,'normal');assert.equal(plot.adjacent.length,1);
    for(const [language,text]of [['en',english],['ko',error],['en',english]]){
      f.change(language);await flush();assert.equal(status.textContent,text);assert.equal(status.hidden,false);
      assert.equal(plot.layout.annotations[0].text,'PAE');assert.equal(f.snapshot(),before);
      assert.equal(plot.adjacent.length,1,'language changes must not append duplicate status paragraphs');
    }
    f.data.pae_error=null;f.data.pae={labels:['A:-1','B:52A'],values:[[0,3],[9,0]]};
    await f.render();assert.equal(status.hidden,true);assert.equal(status.textContent,'');
    assert.equal(plot.data[0].z,f.data.pae.values);assert.equal(plot.adjacent.length,1);
    f.change('ko');await flush();assert.equal(status.hidden,true);assert.equal(status.textContent,'');
  });
  test(`${surface}: populated PAE language events retain trace arrays, boundaries, selection, camera and JSON`,async()=>{
    const f=fixture(source,{heatmap:true});await f.render();const plot=f.get('pae'),before=f.snapshot();
    const traces=plot.data,z=traces[0].z,x=traces[0].x,y=traces[0].y,shapes=plot.layout.shapes,annotations=plot.layout.annotations;
    assert.equal(shapes.length,2);
    for(const [language,caption]of [['en','Column · Evaluated residue'],['ko','열 · 평가 잔기'],['en','Column · Evaluated residue']]){
      f.change(language);await flush();assert.equal(plot.layout.xaxis.title.text,caption);
      assert.equal(plot.data,traces);assert.equal(traces[0].z,z);assert.equal(traces[0].x,x);assert.equal(traces[0].y,y);
      assert.equal(plot.layout.shapes,shapes);assert.equal(plot.layout.annotations,annotations);assert.equal(f.snapshot(),before);
      assert.match(traces[0].hovertemplate,language==='en'?/^Aligned reference/:/^정렬 기준/);
    }
    assert.equal(f.calls.filter(call=>call.method==='react').length,1);
  });
}
