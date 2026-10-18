/* Executes the shipped dialog and cancellation handlers; no real jobs or GPU. */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {koreanContext} from './i18n_context.mjs';
const source=readFileSync(process.env.NANOCOFOLD_UI_SOURCE || new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function fixture(language='ko'){
  const nodes=new Map(),events=new Map(),posts=[];
  let context;
  const document={hidden:false,activeElement:null,querySelectorAll:()=>[],querySelector:()=>null,dispatchEvent(){},documentElement:{dataset:{}}};
  class Node {
    constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.hidden=false;this.isConnected=true;this.style={};this.attrs={};this.value='';}
    append(...nodes){for(const node of nodes){node.parent=this;node.isConnected=true;this.children.push(node);}}
    replaceChildren(...nodes){for(const node of this.children)node.isConnected=false;this.children=[];this.append(...nodes);}
    setAttribute(key,value){this.attrs[key]=value;}
    removeAttribute(key){delete this.attrs[key];}
    getAttribute(key){return this.attrs[key]??null;}
    addEventListener(){}
    closest(){return new Node('label');}
    focus(){document.activeElement=this;}
    showModal(){this.open=true;}
    close(){this.open=false;this.onclose?.();}
    remove(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
  }
  document.body=new Node('body');
  const walk=node=>[node,...node.children.flatMap(walk)];
  const lookup=id=>walk(document.body).find(node=>node.id===id)||nodes.get(id);
  document.getElementById=lookup;document.createElement=tag=>new Node(tag);
  const $=id=>{if(!lookup(id)){const node=new Node('div');node.id=id;nodes.set(id,node);}return lookup(id);};
  const row={id:'a',name:'Alpha 연구자',kind:'analysis',mode:'complex',state:'running',phase:'prediction',progress:{},can_resume:false};
  const batch={id:'batch-a',name:'Batch Alpha',selected:['item-a'],rows:[{id:'item-a',job_id:'a',job:row}]};
  context={document,$,offline:false,URL,URLSearchParams,AbortController,CustomEvent:class {constructor(type,options){this.type=type;this.detail=options?.detail;}},Number,Boolean,Date,Math,
    window:{location:{href:'http://localhost/?view=jobs'},addEventListener:(key,fn)=>{if(!events.has(key))events.set(key,new Set());events.get(key).add(fn);},removeEventListener:(key,fn)=>events.get(key)?.delete(fn)},
    state:{jobs:[row],job:'a',currentView:'jobs',jobSelectionVersion:0,logOffset:0,pollingJobs:false,jobOperations:{},jobActionButtons:[],resumeBudgetMinutes:{}},
    states:{running:'실행 중',cancelled:'취소',queued:'대기'},jobPhaseCaption:j=>j.phase,jobProgressCaption:()=>'',etaText:()=>'',
    notice:(message,error)=>{context.lastNotice=message;context.lastError=error;},setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){},
    api:async(path,body)=>{
      if(body!==undefined){posts.push({path,body});if(context.postResult)return context.postResult;return path.startsWith('/api/batches')?batch:{id:'a',state:'cancelling'};}
      if(path==='/api/jobs')return{jobs:[row]};if(path.includes('/logs'))return{text:'saved',next_offset:5};
      if(path==='/api/batches')return{batches:[batch]};if(path.startsWith('/api/batches/'))return batch;return row;
    }};
  koreanContext(context);context.I18N.setLanguage(language,{persist:false});
  vm.runInContext(source.slice(source.indexOf('function el('),source.indexOf('function notice(')),context);
  vm.runInContext(source.slice(source.indexOf('/* CANCEL_CONFIRMATION_START */'),source.indexOf('/* CANCEL_CONFIRMATION_END */')),context);
  vm.runInContext(source.slice(source.indexOf('async function selectJob('),source.indexOf('async function refreshResults(')),context);
  vm.runInContext(source.slice(source.indexOf('let activeBatch ='),source.indexOf('function platformStartup(')),context);
  context.rememberLocation=()=>{};
  const all=node=>walk(node),dialog=()=>lookup('cancel-confirmation');
  return{c:context,$,row,batch,posts,dialog,all,event:key=>{for(const fn of [...events.get(key)||[]])fn();},events};
}
const jobCancel=async f=>{await f.c.refreshJobs();return f.$('job-actions').children.find(node=>node.textContent===f.c.I18N.value(f.c.I18N.term('작업 취소')));};
for(const language of ['ko','en']){
 test(language+': initial cancel click shows localized target and safe focus without POST',async()=>{
   const f=fixture(language),button=await jobCancel(f);button.focus();const pending=button.onclick();
   assert.equal(f.posts.length,0);assert.equal(f.dialog().open,true);
   assert.equal(f.c.document.activeElement.id,'cancel-confirmation-keep');
   assert.equal(f.$('cancel-confirmation-title').textContent,language==='ko'?'이 작업을 취소할까요?':'Cancel this job?');
   assert.equal(f.$('cancel-confirmation-target').textContent,'Alpha 연구자');
   assert.equal(f.$('cancel-confirmation-target').children[0].textContent,'a');
   assert.match(f.$('cancel-confirmation-detail').textContent,language==='ko'?/미완료 모델.*다시 계산/:/Unfinished models may be recalculated/);
   f.$('cancel-confirmation-keep').onclick();await pending;assert.equal(f.posts.length,0);assert.equal(f.c.document.activeElement,button);
 });
}
for(const method of ['keep','escape','back','hash','pagehide','close','backdrop'])test('dismiss via '+method+' sends zero cancellation API',async()=>{
 const f=fixture(),button=await jobCancel(f),pending=button.onclick(),dialog=f.dialog();
 if(method==='keep')f.$('cancel-confirmation-keep').onclick();
 if(method==='escape'){let prevented=false;dialog.oncancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);}
 if(method==='back')f.event('popstate');if(method==='hash')f.event('hashchange');if(method==='pagehide')f.event('pagehide');
 if(method==='close')dialog.close();if(method==='backdrop')dialog.onclick({target:dialog});
 await pending;assert.deepEqual(f.posts,[]);assert.equal(f.dialog(),undefined);
 assert.equal([...f.events.values()].reduce((n,list)=>n+list.size,0),0);
});
test('Enter from default focus cannot confirm; explicit destructive focus is required',async()=>{
 const f=fixture(),pending=(await jobCancel(f)).onclick();let prevented=0;
 f.dialog().onkeydown({key:'Enter',preventDefault(){prevented++;}});assert.equal(prevented,0);assert.equal(f.posts.length,0);
 f.c.document.activeElement=f.dialog();f.dialog().onkeydown({key:'Enter',preventDefault(){prevented++;}});assert.equal(prevented,1);
 f.$('cancel-confirmation-submit').focus();f.dialog().onkeydown({key:'Enter',preventDefault(){prevented++;}});assert.equal(prevented,1);
 f.$('cancel-confirmation-keep').onclick();await pending;
});
test('double click, repeated confirm, and periodic rerender still send once',async()=>{
 const f=fixture(),button=await jobCancel(f),post=deferred();f.c.postResult=post.promise;
 const first=button.onclick(),second=button.onclick();assert.equal(f.posts.length,0);assert.equal(f.all(f.c.document.body).filter(n=>n.tag==='dialog').length,1);
 const confirm=f.$('cancel-confirmation-submit');confirm.onclick();confirm.onclick();await tick();
 await f.c.refreshJobs();assert.equal(f.posts.length,1);assert.equal(f.$('job-actions').children.find(n=>n.textContent==='작업 취소').disabled,true);
 post.resolve({state:'cancelling'});await Promise.all([first,second]);assert.equal(f.posts.length,1);
});
test('selection/polling changes do not retarget an open single-job dialog',async()=>{
 const f=fixture(),pending=(await jobCancel(f)).onclick();f.c.state.job='b';f.c.state.jobSelectionVersion++;f.row.name='Changed';
 assert.equal(f.$('cancel-confirmation-target').textContent,'Alpha 연구자');
 f.$('cancel-confirmation-submit').onclick();await pending;assert.equal(f.posts[0].path,'/api/jobs/a/cancel');
});
test('network failure reports failure without automatic retry or resume',async()=>{
 const f=fixture(),post=deferred();f.c.postResult=post.promise;const pending=(await jobCancel(f)).onclick();
 f.$('cancel-confirmation-submit').onclick();await tick();post.reject(new Error('Connection lost; check job status'));
 await pending;assert.equal(f.posts.length,1);assert.equal(f.c.lastError,true);assert.match(f.c.lastNotice,/Connection lost/);
 await f.c.refreshJobs();assert.equal(f.posts.length,1);assert.equal(f.c.state.jobOperations.a,undefined);
});
test('queued resume cancellation explains saved results and keeps resume pending on dismiss',async()=>{
 const f=fixture('en');f.row.state='queued';f.row.phase='resume_validation';
 const pending=(await jobCancel(f)).onclick();assert.equal(f.$('cancel-confirmation-title').textContent,'Cancel this queued resume?');
 assert.equal(f.$('cancel-confirmation-keep').textContent,'Keep resume queued');assert.match(f.$('cancel-confirmation-detail').textContent,/Saved results are kept/);
 f.$('cancel-confirmation-keep').onclick();await pending;assert.equal(f.posts.length,0);assert.equal(f.row.state,'queued');
});
test('resume remains explicit and is not changed into a cancel request',async()=>{
 const f=fixture();f.row.state='interrupted';f.row.can_resume=true;await f.c.refreshJobs();
 const resume=f.$('job-actions').children.find(n=>n.textContent==='검증 후 재개');await resume.onclick();
 assert.equal(f.dialog(),undefined);assert.equal(f.posts.length,1);assert.equal(f.posts[0].path,'/api/jobs/a/resume');
});
test('batch item requires confirmation, pins batch/job, and preserves a newly selected batch',async()=>{
 const f=fixture();f.c.renderBatch(f.batch);
 const button=f.all(f.$('batch-rows')).find(n=>n.tag==='button'&&n.textContent==='항목 취소');
 const pending=button.onclick();assert.equal(f.posts.length,0);
 const other={id:'batch-b',name:'Other Batch',rows:[]};f.c.renderBatch(other);
 f.$('cancel-confirmation-submit').onclick();await pending;
 assert.equal(f.posts.length,1);assert.equal(f.posts[0].path,'/api/jobs/a/cancel');assert.match(f.$('batch-summary').textContent,/Other Batch/);
});
test('batch all dismiss keeps all jobs unchanged and confirm pins the original batch',async()=>{
 const f=fixture('en');f.c.initBatch();f.c.renderBatch(f.batch);
 let pending=f.$('batch-cancel').onclick();assert.equal(f.posts.length,0);
 assert.equal(f.$('cancel-confirmation-title').textContent,'Cancel all unfinished analyses in this batch?');
 f.$('cancel-confirmation-keep').onclick();await pending;assert.equal(f.posts.length,0);
 pending=f.$('batch-cancel').onclick();f.c.renderBatch({id:'batch-b',name:'Other Batch',rows:[]});
 f.$('cancel-confirmation-submit').onclick();await pending;
 assert.equal(f.posts.length,1);assert.equal(f.posts[0].path,'/api/batches/batch-a/cancel');assert.match(f.$('batch-summary').textContent,/Other Batch/);
});
test('batch item keyboard/escape dismissal and confirmation failure do not issue a resume',async()=>{
 const f=fixture();f.c.renderBatch(f.batch);
 let button=f.all(f.$('batch-rows')).find(n=>n.tag==='button'&&n.textContent==='항목 취소');let pending=button.onclick();
 f.dialog().oncancel({preventDefault(){}});await pending;assert.equal(f.posts.length,0);
 const post=deferred();f.c.postResult=post.promise;pending=button.onclick();f.$('cancel-confirmation-submit').onclick();await tick();post.reject(new Error('Transport failure'));
 await pending;assert.deepEqual(f.posts.map(p=>p.path),['/api/jobs/a/cancel']);assert.equal(f.c.lastError,true);
});
test('dialog language can change without translating user target or changing action',async()=>{
 const f=fixture(),pending=(await jobCancel(f)).onclick();f.c.I18N.setLanguage('en',{persist:false});
 assert.equal(f.$('cancel-confirmation-title').textContent,'Cancel this job?');assert.equal(f.$('cancel-confirmation-target').textContent,'Alpha 연구자');
 assert.equal(f.$('cancel-confirmation-keep').textContent,'Keep running');f.$('cancel-confirmation-keep').onclick();await pending;assert.equal(f.posts.length,0);
});
test('an unavailable modal fails closed without cancelling',async()=>{
 const f=fixture(),original=f.c.document.createElement;f.c.document.createElement=tag=>{const node=original(tag);if(tag==='dialog')node.showModal=()=>{throw new Error('unavailable');};return node;};
 await(await jobCancel(f)).onclick();assert.equal(f.posts.length,0);assert.equal(f.c.lastError,true);assert.match(f.c.lastNotice,/확인창/);
});

for(const change of ['completed','new-resume','queued-started'])test('changed '+change+' requires fresh confirmation and never cancels a newer execution',async()=>{
 const f=fixture();if(change==='queued-started'){f.row.state='queued';f.row.started=null;}
 const pending=(await jobCancel(f)).onclick();
 if(change==='completed')f.row.state='complete';
 if(change==='new-resume'){f.row.started=456;f.row.history=[{state:'complete',finished:123}];}
 if(change==='queued-started'){f.row.state='running';f.row.started=123;}
 f.$('cancel-confirmation-submit').onclick();await pending;
 assert.equal(f.posts.length,0);assert.equal(f.c.lastError,true);assert.match(f.c.lastNotice,/상태가 바뀌었습니다/);
});
test('batch all generation change preserves every job without POST',async()=>{
 const f=fixture();f.c.initBatch();f.c.renderBatch(f.batch);const pending=f.$('batch-cancel').onclick();
 f.row.history=[{state:'interrupted'}];f.row.started=123;
 f.$('cancel-confirmation-submit').onclick();await pending;assert.equal(f.posts.length,0);assert.match(f.c.lastNotice,/상태가 바뀌었습니다/);
});
test('a UI tab change dismisses confirmation without cancelling',async()=>{
 const f=fixture();vm.runInContext(source.slice(source.indexOf('function rememberLocation('),source.indexOf('const bytesLabel')),f.c);
 f.c.refreshResults=()=>{};f.c.state.navigationReady=false;
 const pending=(await jobCancel(f)).onclick();f.c.view('results');await pending;
 assert.equal(f.posts.length,0);assert.equal(f.dialog(),undefined);assert.equal(f.c.state.currentView,'results');
});
test('names identical to catalog terms remain literal on language switch',async()=>{
 const f=fixture();f.row.name='작업 취소';const pending=(await jobCancel(f)).onclick();
 f.c.I18N.setLanguage('en',{persist:false});assert.equal(f.$('cancel-confirmation-target').textContent,'작업 취소');
 f.$('cancel-confirmation-keep').onclick();await pending;assert.equal(f.posts.length,0);
});

test('after a generation change the refreshed job action captures the current execution',async()=>{
 const f=fixture(),old=await jobCancel(f);f.row.started=123;f.row.history=[{state:'interrupted'}];
 await f.c.refreshJobs();const current=f.$('job-actions').children.find(n=>n.textContent==='작업 취소');assert.notEqual(old,current);
 const pending=current.onclick();f.$('cancel-confirmation-submit').onclick();await pending;assert.equal(f.posts.length,1);
});
test('a cancelling-only batch offers no repeated cancellation action',()=>{
 const f=fixture();f.row.state='cancelling';f.c.renderBatch(f.batch);
 assert.equal(f.$('batch-cancel').disabled,true);assert.equal(f.all(f.$('batch-rows')).filter(n=>n.tag==='button'&&n.textContent==='항목 취소').length,0);
});
