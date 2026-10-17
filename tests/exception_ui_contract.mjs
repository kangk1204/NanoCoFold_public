import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(process.env.NANOCOFOLD_UI_SOURCE || new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function fixture(){
 const nodes=new Map();
 function node(id){if(!nodes.has(id))nodes.set(id,{id,children:[],textContent:'',hidden:false,disabled:false,style:{},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=[...v];},setAttribute(){},removeAttribute(){}});return nodes.get(id);}
 const state={jobs:[],job:'a',logOffset:0,pollingJobs:false,jobSelectionVersion:0,jobOperations:{},jobActionKey:null,jobActionButtons:[],resumeBudgetMinutes:{},inputRevision:0};
 const context={state,$:node,offline:false,document:{hidden:false},window:{location:{href:'http://localhost/'}},
 I18N:{term:s=>s,text:(n,s)=>n.textContent=s,concat:a=>a.join(''),format:(s,args)=>s.replace(/\{(\d+)\}/g,(_,i)=>args[i])},
 states:{running:'실행 중',interrupted:'중단',cancelling:'취소 중'},phaseLabel:x=>x,jobPhaseCaption:j=>j.phase,etaText:()=>'',
 el:(tag,text)=>{const n=node(Symbol(tag));n.tag=tag;n.textContent=text||'';return n;},notice:m=>context.lastNotice=m,
 api:async path=>path==='/api/jobs'?{jobs:[{id:'a',name:'Alpha',kind:'analysis',state:'running',progress:{}}]}:path.includes('/logs')?{text:'saved\n',next_offset:6}:{id:'a',name:'Alpha',kind:'analysis',state:'running',progress:{}},
 URL,URLSearchParams,Number,Math,Boolean,Date,setTimeout,clearTimeout,AbortController};
 vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('async function selectJob('),source.indexOf('async function refreshResults(')),context);
 context.rememberLocation=()=>{};return context;
}
test('cancel is single flight, including a periodic rerender during the request',async()=>{
 const f=fixture(),post=defer();let posts=0;
 const previous=f.api;f.api=(path,body)=>body!==undefined?(posts++,post.promise):previous(path);
 await f.refreshJobs();let button=f.$('job-actions').children.find(n=>n.textContent==='작업 취소');
 const first=button.onclick();const second=button.onclick();
 await f.refreshJobs();button=f.$('job-actions').children.find(n=>n.textContent==='작업 취소');
 assert.equal(button.disabled,true);assert.equal(posts,1);
 post.resolve({id:'a',state:'cancelling'});await Promise.all([first,second]);
});
test('selecting A again rejects the stale preselection log response',async()=>{
 const f=fixture(),log=defer();let reads=0;const previous=f.api;
 f.api=path=>path.includes('/logs')&&++reads===1?log.promise:previous(path);
 const refreshing=f.refreshJobs();await new Promise(r=>setTimeout(r,0));
 await f.selectJob('a');log.resolve({text:'STALE',next_offset:99});await refreshing;
 assert.doesNotMatch(f.$('job-log').textContent,/STALE/);
 assert.notEqual(f.state.logOffset,99);
});
test('bounded API reads abort a stalled request and release its timer',async()=>{
 const timers=[];const c={window:{PLATFORM_TOKEN:'token'},AbortController,
 setTimeout:(fn)=>{timers.push(fn);return 1;},clearTimeout:()=>{},
 I18N:{term:s=>s},fetch:(_path,options)=>new Promise((_resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(new Error('aborted'))))};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function action(')),c);
 const pending=c.api('/api/jobs',undefined,{timeoutMs:10});
 assert.equal(timers.length,1);timers[0]();await assert.rejects(pending,/상태 응답/);
});

function batchFixture(){
 const f=fixture();f.I18N.attribute=()=>{};
 vm.runInContext(source.slice(source.indexOf('let activeBatch ='),source.indexOf('async function refreshBatchHistory(')),f);
 return f;
}
test('a partially submitted batch offers explicit retry while registered jobs stay locked',()=>{
 const f=batchFixture();f.renderBatch({id:'b',name:'Batch',selected:['one','two'],budget_seconds:120,
 rows:[{id:'one',job_id:'job-one',job:{state:'running'}},{id:'two',job_id:null,submission_error:'Temporary admission error'}]});
 assert.equal(f.$('batch-submit').disabled,false);
 assert.match(f.$('batch-submit').textContent,/미제출 항목 다시 제출/);
 assert.match(f.$('batch-next-step').textContent,/이미 등록된 작업/);
 f.renderBatch({id:'b',name:'Batch',selected:['one','two'],rows:[{id:'one',job_id:'job-one',job:{state:'running'}},{id:'two',job_id:'job-two',job:{state:'queued'}}]});
 assert.equal(f.$('batch-submit').disabled,true);
});
test('interrupted batch rows expose saved partial output with its limitation',()=>{
 const f=batchFixture();f.renderBatch({id:'b',name:'Batch',selected:['one'],rows:[{id:'one',job_id:'job-one',job:{state:'interrupted',run_dir:'/workspace/results/run',result_available:true,can_resume:true}}]});
 function all(n){return [n,...n.children.flatMap(all)];}
 const nodes=all(f.$('batch-rows'));
 assert.ok(nodes.some(n=>n.tag==='a'&&n.textContent==='부분 결과 열기'&&n.href==='/?result=job-one'));
 assert.ok(nodes.some(n=>n.textContent.includes('전체 분석 완료를 뜻하지 않습니다')));
});

for(const [returned,caption] of [['complete','이미 종료'],['cancelled','작업이 취소되었습니다']]) {
 test('cancel request reports the actual returned '+returned+' state',async()=>{
  const f=fixture(),previous=f.api;
  f.api=(path,body)=>body!==undefined?Promise.resolve({id:'a',state:returned}):previous(path);
  await f.refreshJobs();await f.$('job-actions').children.find(n=>n.textContent==='작업 취소').onclick();
  assert.match(f.lastNotice,new RegExp(caption));
  assert.doesNotMatch(f.lastNotice,/취소로 바뀔 때까지/);
 });
}
test('read-only status POST times out as a read and never replays the request',async()=>{
 const timers=[];let requests=0;
 const c={window:{PLATFORM_TOKEN:'token'},AbortController,
 setTimeout:fn=>{timers.push(fn);return 1;},clearTimeout:()=>{},
 fetch:(_path,options)=>{requests++;return new Promise((_resolve,reject)=>options.signal?.addEventListener('abort',()=>reject(new Error('aborted'))));}};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function action(')),c);
 const pending=c.api('/api/environment/status',{input_id:'input'}, {timeoutMs:10,readOnly:true});
 assert.equal(timers.length,1);timers[0]();await assert.rejects(pending,/상태 응답/);assert.equal(requests,1);
});

test('a cancelled queued batch job does not advertise nonexistent partial output',()=>{
 const f=batchFixture();f.renderBatch({id:'b',name:'Batch',selected:['one'],rows:[{id:'one',job_id:'job-one',job:{state:'cancelled',run_dir:'/workspace/results/not-created',result_available:false,can_resume:true}}]});
 function all(n){return [n,...n.children.flatMap(all)];}
 assert.equal(all(f.$('batch-rows')).filter(n=>n.tag==='a').length,0);
});
test('resume minutes retain the same input across polling and restore the draft per job',async()=>{
 const f=fixture();f.I18N.attribute=()=>{};
 const row={id:'a',name:'Alpha',kind:'analysis',state:'budget_exhausted',can_resume:true,progress:{}};
 const other={id:'b',name:'Beta',kind:'analysis',state:'running',can_resume:false,progress:{}};
 f.api=async path=>path==='/api/jobs'?{jobs:[row,other]}:path.includes('/logs')?{text:'',next_offset:0}:path.endsWith('/b')?other:row;
 const all=n=>[n,...n.children.flatMap(all)];
 await f.refreshJobs();const field=all(f.$('job-actions')).find(n=>n.tag==='input');
 field.value='17';field.oninput?.();await f.refreshJobs();
 assert.equal(all(f.$('job-actions')).find(n=>n.tag==='input'),field);
 assert.equal(field.value,'17');
 await f.selectJob('b');await f.selectJob('a');
 assert.equal(all(f.$('job-actions')).find(n=>n.tag==='input').value,'17');
 row.state='queued';row.can_resume=false;await f.refreshJobs();
 assert.equal(all(f.$('job-actions')).filter(n=>n.tag==='input').length,0);
});
