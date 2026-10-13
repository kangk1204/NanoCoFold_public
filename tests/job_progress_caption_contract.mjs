/** Processed attempts are not successful models; unknown counters are not zero. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8'),c={};
vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function jobProgressCaption('),source.indexOf('async function refreshJobs()')),c);
const cases=[
 ['known zero stage counts remain zero',{progress:{completed:0,total:0}},'진행 0/0'],
 ['known zero model outcomes remain zero',{progress:{completed:0,total:4,succeeded:0,failed:0}},'처리 0/4 · 성공 0 · 실패 0'],
 ['all processed models include both successful and failed attempts',{mode:'complex',progress:{completed:4,total:4,succeeded:2,failed:2}},'처리 4/4 · 성공 2 · 실패 2'],
 ['null counts remain unknown',{progress:{completed:null,total:null}},'진행 미기록/미기록'],
 ['missing progress remains unknown',{},'진행 미기록/미기록'],
 ['unknown total does not conceal recorded model outcomes',{progress:{completed:4,total:null,succeeded:3,failed:1}},'처리 4/미기록 · 성공 3 · 실패 1'],
 ['partial outcome receipt does not invent the missing success count',{progress:{completed:1,total:4,failed:1}},'처리 1/4 · 성공 미기록 · 실패 1'],
 ['diagnostic stages continue to display stage progress',{kind:'diagnostic',progress:{completed:1,total:3}},'진행 1/3'],
 ['mock CPU stages retain generic progress',{mock:true,phase:'target',progress:{completed:2,total:4}},'진행 2/4'],
 ['invalid count values are unknown rather than coerced',{progress:{completed:false,total:'4',succeeded:NaN,failed:-1}},'처리 미기록/미기록 · 성공 미기록 · 실패 미기록'],
];
for(const [name,job,caption] of cases)test(name,()=>assert.equal(c.jobProgressCaption(job),caption));
test('job detail uses the count helper and preserves the mock badge',()=>{
 assert.match(source,/\$\{jobProgressCaption\(job\)\}/);
 assert.match(source,/if \(job\.mock\)\s+\$\("job-detail"\)\.append\(el\("span", "MOCK · 합성 결과", "badge"\)\)/);
});

/** Worker terminal stage counts must not hide authoritative final model records. */
const terminalJob=(mode='screen',summary={model_record_total:4,scored_total:4,failed_candidates:0,expected_total:4},extra={})=>({
 kind:'analysis',mode,mock:false,state:'complete',progress:{completed:mode==='design'?4:3,total:mode==='design'?4:3},summary,...extra,
});
const terminalCases=[
 ['native screen stage 3/3 reports its four completed models',terminalJob(),'처리 4/4 · 성공 4 · 실패 0'],
 ['native mature stage 3/3 reports eight models',terminalJob('mature',{model_record_total:8,scored_total:8,failed_candidates:0,expected_total:8}),'처리 8/8 · 성공 8 · 실패 0'],
 ['native design stage 4/4 reports eight models',terminalJob('design',{model_record_total:8,scored_total:8,failed_candidates:0,expected_total:null}),'처리 8/8 · 성공 8 · 실패 0'],
 ['partial failure includes two failed model records',terminalJob('screen',{model_record_total:4,scored_total:2,failed_candidates:2,expected_total:4},{state:'partial_failure'}),'처리 4/4 · 성공 2 · 실패 2'],
 ['partial failure preserves a recorded expected workload',terminalJob('screen',{model_record_total:4,scored_total:2,failed_candidates:2,expected_total:6},{state:'partial_failure'}),'처리 4/6 · 성공 2 · 실패 2'],
 ['known zero final models and outcomes remain explicit zero',terminalJob('screen',{model_record_total:0,scored_total:0,failed_candidates:0,expected_total:0}),'처리 0/0 · 성공 0 · 실패 0'],
 ['candidate population of thirty cannot inflate four model records',terminalJob('screen',{model_record_total:4,scored_total:4,failed_candidates:0,candidate_record_total:30,total:30}),'처리 4/4 · 성공 4 · 실패 0'],
 ['candidate-only population never supplies missing model counts',terminalJob('screen',{candidate_record_total:30,total:30,scored_total:4,failed_candidates:0}),'단계 진행 3/3'],
 ['missing final summary remains explicitly stage progress',terminalJob('screen',undefined,{summary:undefined}),'단계 진행 3/3'],
 ['missing success count is not invented',terminalJob('screen',{model_record_total:4,failed_candidates:0}),'단계 진행 3/3'],
 ['missing failure count is not invented',terminalJob('screen',{model_record_total:4,scored_total:4}),'단계 진행 3/3'],
 ['null models do not become zero',terminalJob('screen',{model_record_total:null,scored_total:0,failed_candidates:0}),'단계 진행 3/3'],
 ['unevaluated records are not recast as failed models',terminalJob('screen',{model_record_total:4,scored_total:2,failed_candidates:0}),'단계 진행 3/3'],
 ['inconsistent sum does not display model outcomes',terminalJob('screen',{model_record_total:4,scored_total:4,failed_candidates:2},{state:'partial_failure'}),'단계 진행 3/3'],
 ['expected count below recorded model population is rejected',terminalJob('screen',{model_record_total:4,scored_total:4,failed_candidates:0,expected_total:3}),'단계 진행 3/3'],
 ['complete state cannot claim an unfinished expected model population',terminalJob('screen',{model_record_total:4,scored_total:4,failed_candidates:0,expected_total:6}),'단계 진행 3/3'],
 ['complete state with recorded failed models is inconsistent',terminalJob('screen',{model_record_total:4,scored_total:2,failed_candidates:2}),'단계 진행 3/3'],
 ['summary for a different mode is rejected',terminalJob('screen',{mode:'mature',model_record_total:4,scored_total:4,failed_candidates:0}),'단계 진행 3/3'],
 ['mock summary cannot support native model outcomes',terminalJob('screen',{mock:true,model_record_total:4,scored_total:4,failed_candidates:0}),'단계 진행 3/3'],
 ['active native work retains stage progress',terminalJob('screen',undefined,{state:'running'}),'진행 3/3'],
 ['diagnostics retain generic stage progress despite a model-shaped summary',terminalJob('screen',undefined,{kind:'diagnostics'}),'진행 3/3'],
 ['mock CPU stages retain generic progress despite model-shaped summary',terminalJob('screen',undefined,{mock:true}),'진행 3/3'],
 ['unrecorded mock flag does not prove native execution',terminalJob('screen',undefined,{mock:undefined}),'진행 3/3'],
 ['structured complex progress always overrides final summary',terminalJob('complex',{model_record_total:8,scored_total:8,failed_candidates:0},{progress:{completed:4,total:4,succeeded:2,failed:2}}),'처리 4/4 · 성공 2 · 실패 2'],
 ['partial structured counts also override otherwise valid summary',terminalJob('screen',undefined,{progress:{completed:1,total:4,failed:1}}),'처리 1/4 · 성공 미기록 · 실패 1'],
 ['array-shaped summary cannot supply model counts',terminalJob('screen',[]),'단계 진행 3/3'],
];
for(const [name,job,caption] of terminalCases)test(name,()=>assert.equal(c.jobProgressCaption(job),caption));
for(const [name,value] of [['boolean',false],['string','4'],['fraction',4.5],['negative',-1],['NaN',NaN],['infinite',Infinity],['unsafe integer',Number.MAX_SAFE_INTEGER+1]]) {
 for(const field of ['model_record_total','scored_total','failed_candidates','expected_total'])test(`terminal ${field} rejects ${name} without coercion`,()=>{
  const summary={model_record_total:4,scored_total:4,failed_candidates:0,expected_total:4,[field]:value};
  assert.equal(c.jobProgressCaption(terminalJob('screen',summary)),'단계 진행 3/3');
 });
}
