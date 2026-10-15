import {koreanContext} from './i18n_context.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function fixture({hidden=false,pending=false}={}) {
 let calls=0,finish;
 const node={replaceChildren(){},textContent:''};
 const c={offline:false,document:{hidden},state:{pollingJobs:false,diagnostic:null,job:null},
   $:()=>node,api:()=>{calls++;return pending?new Promise(resolve=>{finish=()=>resolve({jobs:[]});}):Promise.resolve({jobs:[]});},notice(){}};
 koreanContext(c);vm.runInContext(source.slice(source.indexOf('async function refreshJobs('),source.indexOf('async function refreshResults(')),c);
 return {c,calls:()=>calls,finish:()=>finish()};
}
test('hidden tabs issue no job-list or detail request',async()=>{
 const f=fixture({hidden:true});await f.c.refreshJobs();assert.equal(f.calls(),0);assert.equal(f.c.state.pollingJobs,false);
});
test('visible tabs resume polling and retain the no-overlap guard',async()=>{
 const f=fixture({hidden:true,pending:true});await f.c.refreshJobs();f.c.document.hidden=false;
 const pending=f.c.refreshJobs();await f.c.refreshJobs();assert.equal(f.calls(),1);
 f.finish();await pending;assert.equal(f.c.state.pollingJobs,false);
});
const c={phaseLabel:p=>({complete:'완료',final:'결과·보고서 저장'})[p]||p};koreanContext(c);
vm.runInContext(source.slice(source.indexOf('function jobPhaseCaption('),source.indexOf('function el(')),c);
vm.runInContext(source.slice(source.indexOf('function etaText('),source.indexOf('async function prepareInput(')),c);
for(const [state,caption] of [['running','결과·보고서 저장 중'],['cancelling','종료 정리 중'],['complete','완료']])test(state+' cannot confuse stage completion with job completion',()=>assert.equal(c.jobPhaseCaption({state,phase:'complete'}),caption));
test('completed prediction with an active job describes pending finalization',()=>{
 const label=c.etaText({state:'running',mock:false,eta:{status:'complete',completed:8,total:8}});
 assert.match(label,/예측 단계 8\/8 처리 완료/);assert.match(label,/작업은 실행 중/);
});
