import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {koreanContext} from './i18n_context.mjs';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function fixture(){
 class Node {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.open=false;this.isConnected=true;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return (this._text||'')+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this._text='';this.children=nodes;}
  setAttribute(k,v){this.attrs[k]=v;}
 }
 const output=new Node('div'),context={$:()=>output,document:{createElement:tag=>new Node(tag),documentElement:{dataset:{}},getElementById:()=>null,querySelector:()=>null,dispatchEvent(){}},CustomEvent:class{}};
 koreanContext(context);
 vm.runInContext(source.slice(source.indexOf('function el('),source.indexOf('function notice(')),context);
 vm.runInContext(source.slice(source.indexOf('/* DIAGNOSTIC_PRESENTATION_START */'),source.indexOf('/* DIAGNOSTIC_PRESENTATION_END */')),context);
 const all=n=>[n,...n.children.flatMap(all)];return{c:context,output,all};
}
test('environment check explains required blockers and keeps exact evidence in a closed original record',()=>{
 const {c,output,all}=fixture(),diagnosis={ok:false,checks:[
  {name:'report.delivery_assets',ok:true,required:true,evidence:{blocker:'original evidence field'}},
  {name:'gpu',ok:false,required:true,evidence:{path:'/original/경로',note:'<script>raw()</script>'}},
  {name:'esm.weights',ok:false,required:false}, {name:'boltz2.env_imports',ok:false,required:true},
 ]},before=JSON.stringify(diagnosis);
 c.renderDiagnostics(diagnosis);
 const nodes=all(output),original=nodes.find(n=>n.id==='diagnostics-original');
 assert.equal(original.open,false);assert.equal(original.children[1].textContent,JSON.stringify(diagnosis,null,2));
 assert.equal(JSON.stringify(diagnosis),before);assert.equal(nodes.some(n=>n.tag==='script'),false);
 assert.match(output.textContent,/필수 확인/);assert.match(output.textContent,/이 선택 기능 없이 분석할 수 있습니다/);
 c.I18N.setLanguage('en',{persist:false});
 const table=nodes.find(n=>n.tag==='table');
 assert.match(table.textContent,/Boltz-2 · Runtime environment/);assert.match(table.textContent,/Action required/);
 assert.match(table.textContent,/NVIDIA GPU/);assert.match(table.textContent,/Analysis can proceed without this optional feature/);
 assert.equal(/[가-힣]/.test(table.textContent),false);
 assert.equal(original.children[1].textContent,JSON.stringify(diagnosis,null,2));
 c.I18N.setLanguage('ko',{persist:false});assert.match(table.textContent,/Boltz-2 · 실행 환경/);
});
test('unknown checks remain literal and missing details retain the readiness heading',()=>{
 const {c,output}=fixture();assert.equal(c.diagnosticCaption('future.<check>'),'future.<check>');
 c.renderDiagnostics({ok:false});assert.match(output.textContent,/환경 확인 기록/);assert.doesNotMatch(output.textContent,/실행 준비 미충족/);assert.match(output.textContent,/아직 없습니다/);
 c.I18N.setLanguage('en',{persist:false});assert.match(output.textContent,/not available yet|No detailed environment/);
});
