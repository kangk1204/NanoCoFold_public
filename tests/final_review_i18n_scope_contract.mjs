import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const dir=new URL('../nanocofold/templates/',import.meta.url);
const source=readFileSync(new URL('platform.js',dir),'utf8');
const catalog=readFileSync(new URL('platform-i18n.js',dir),'utf8');
function fixture(){
 const nodes=new Map(),handlers=new Map(),calls=[];
 const a={candidate_id:'candidate_A',iptm:.2,ipsae:.3,iptm_scope:'whole_complex',engine:'chai1',seed:42,msa:'cache',restraint:false};
 const b={...a,candidate_id:'candidate_B',iptm:.9,ipsae:.95};
 const get=id=>{if(!nodes.has(id))nodes.set(id,{id,value:id==='plot-x'?'iptm':'ipsae',textContent:'',isConnected:true,on(){},setAttribute(){}});return nodes.get(id);};
 const plotly={react:(node,data,layout)=>calls.push({id:node.id,data,layout})};
 const context={window:{Plotly:plotly},Plotly:plotly,state:{result:{mode:'complex'},observations:[a,b],filtered:[a,b],candidate:a},candidateEligibility:()=>null,selectCandidate(){},fmt:String};
 vm.createContext(context);vm.runInContext(catalog,context);
 context.document={getElementById:get,documentElement:{},querySelector:()=>null,addEventListener:(name,fn)=>handlers.set(name,fn),dispatchEvent:event=>handlers.get(event.type)?.()};
 context.CustomEvent=class {constructor(type){this.type=type;}};
 vm.runInContext(source.slice(0,source.indexOf('const decisionCaption')),context);
 vm.runInContext(source.slice(source.indexOf('function describeResultFigure('),source.indexOf('function mockZeroSelectionNotice(')),context);
 vm.runInContext(source.slice(source.indexOf('function renderScatter()'),source.indexOf('async function selectCandidate(')),context);
 vm.runInContext(source.slice(source.indexOf('function renderDistribution('),source.indexOf('async function loadStructure(')),context);
 vm.runInContext(source.slice(source.indexOf('document.addEventListener("nanocofold:language"'),source.lastIndexOf('startPlatform();')),context);
 return {context,nodes,calls,get};
}
test('language events preserve selected candidate, score arrays, observation count and metric scope',()=>{
 const {context:c,calls,get}=fixture();const raw=JSON.stringify(c.state.observations);
 for(const [id,value] of [['candidate_A',.2],['candidate_B',.9]]){
  c.state.candidate=c.state.observations.find(row=>row.candidate_id===id);
  for(const language of ['en','ko','en']){
   c.I18N.setLanguage(language,{persist:false});
   const chart=calls.filter(call=>call.id==='distribution').at(-1);
   assert.deepEqual(JSON.parse(JSON.stringify(chart.data.map(trace=>trace.y))),[[value]]);
   assert.equal(chart.layout.yaxis.title.text,language==='en'?'Whole-complex ipTM':'전체 복합체 ipTM');
   assert.deepEqual(JSON.parse(JSON.stringify(chart.layout.xaxis.ticktext)),[language==='en'?'Condition 1':'조건 1']);
   assert.match(get('observation-note').textContent,language==='en'?/^Observations for this candidate: 1\./:/^현재 후보 1개 관측/);
   assert.equal(c.state.candidate.candidate_id,id);assert.equal(JSON.stringify(c.state.observations),raw);
  }
 }
 c.state.candidate=null;c.I18N.setLanguage('en',{persist:false});
 const empty=calls.filter(call=>call.id==='distribution').at(-1);
 assert.equal(empty.data.length,0);assert.equal(empty.layout.annotations[0].text,'ipTM of repeated observations not evaluated');
 c.I18N.setLanguage('ko',{persist:false});
 assert.equal(calls.at(-1).layout.annotations[0].text,'반복 관측의 ipTM 미평가');
});
