import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const envFunctions=source.slice(source.indexOf('function environmentPayload()'),source.indexOf('async function selectJob('));
const translation=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};};
function fixture(){
 const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{value:id==='environment-mode'?'complex':'',disabled:false,replaceChildren(){},textContent:''});return nodes.get(id);};
 const c={state:{environmentRequest:0,environmentJob:null,environment:null,environmentTarget:null,starter:{modes:{complex:{native_override:{run:{mode:'complex'}}},mature:{native_override:{run:{mode:'mature'}}}}}},$:node,
 I18N:{term:s=>s,text:(n,s)=>n.textContent=s},renderDiagnostics(){},notice(){},view(){},api:async()=>({ready:false,reason:'not_checked'})};
 c.state.inputRevision=0;c.state.inputReadinessRequest=0;
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function renderInputReadiness()'),source.indexOf('function environmentPayload()'))+envFunctions,c);return c;
}
test('changing purpose releases the previously tracked environment job',async()=>{
 const f=fixture();f.state.environmentJob='complex-job';f.$('environment-mode').value='mature';
 await f.refreshEnvironmentStatus();assert.equal(f.state.environmentJob,null);assert.equal(f.$('diagnose-environment').disabled,false);
});
test('an old status response cannot overwrite the current purpose',async()=>{
 const f=fixture(),a=deferred(),b=deferred();let n=0;f.api=()=>++n===1?a.promise:b.promise;
 const first=f.refreshEnvironmentStatus();f.$('environment-mode').value='mature';const second=f.refreshEnvironmentStatus();
 b.resolve({ready:false,reason:'not_checked'});await second;a.resolve({ready:true,state:'complete',job_id:'old'});await first;
 assert.equal(f.state.environment.ready,false);assert.equal(f.state.environmentJob,null);
});
test('a stale submission response cannot attach its job to a different purpose',async()=>{
 const f=fixture(),pending=deferred();let submissions=0;
 f.api=(path)=>path==='/api/environment/check'?(submissions++,pending.promise):Promise.resolve({ready:false,reason:'not_checked'});
 const submitted=f.submitDiagnostic();f.$('environment-mode').value='mature';await f.refreshEnvironmentStatus();
 pending.resolve({id:'old-complex-job'});await submitted;
 assert.equal(f.state.environmentJob,null);assert.equal(f.$('diagnose-environment').disabled,false);assert.equal(submissions,1);
});
test('submitting a check invalidates an older successful status reply',async()=>{
 const f=fixture(),status=deferred(),submission=deferred();let statusCalls=0;
 f.api=(path)=>path==='/api/environment/check'?submission.promise:++statusCalls===1?status.promise:Promise.resolve({ready:false,state:'queued',job_id:'new-job',diagnostics:{}});
 const old=f.refreshEnvironmentStatus();const started=f.submitDiagnostic();status.resolve({ready:true,state:'complete',job_id:'old-job',diagnostics:{ok:true,checks:[]}});await old;
 assert.notEqual(f.state.environment?.ready,true);submission.resolve({id:'new-job'});await started;
 assert.equal(f.state.environmentJob,'new-job');assert.equal(f.state.environment.ready,false);
});
test('status failures remain unready and recover through a read-only retry',async()=>{
 const f=fixture(),paths=[];let fail=true;
 f.api=async path=>{paths.push(path);if(fail)throw Error('temporary status failure');return{ready:false,state:'running',job_id:'current-job',diagnostics:{}};};
 await f.refreshEnvironmentStatus();assert.equal(f.state.environment.ready,false);assert.equal(f.state.environment.reason,'status_unavailable');
 assert.equal(f.$('diagnose-environment').disabled,false);fail=false;await f.refreshEnvironmentStatus();
 assert.equal(f.state.environmentJob,'current-job');assert.equal(f.$('diagnose-environment').disabled,true);
 assert.deepEqual(paths,['/api/environment/status','/api/environment/status']);
});
test('failed submission releases controls and does not invent a successful receipt',async()=>{
 const f=fixture();f.api=async path=>{if(path==='/api/environment/check')throw Error('submission failed');return{ready:false,reason:'not_checked'};};
 await assert.rejects(f.submitDiagnostic(),/submission failed/);
 assert.equal(f.state.environmentSubmitting,false);assert.equal(f.state.environmentJob,null);
 assert.equal(f.state.environment.ready,false);assert.equal(f.$('diagnose-environment').disabled,false);
});
test('rechecking a passed input disables analysis and rejects its older readiness response',async()=>{
 const f=fixture(),oldStatus=deferred(),submission=deferred();let inputReads=0;
 f.state.input={id:'reviewed-input',config:{run:{mode:'complex',mock:false}}};f.state.readiness=true;
 f.api=(path,payload)=>path==='/api/environment/check'?submission.promise:
   payload?.input_id?(++inputReads===1?oldStatus.promise:Promise.resolve({ready:false})):
   Promise.resolve({ready:false,state:'queued',job_id:'new-check',diagnostics:{}});
 f.renderInputReadiness();assert.equal(f.$('start-job').disabled,false);
 const old=f.readInputReadiness(f.state.input);const submit=f.submitDiagnostic();
 assert.equal(f.state.readiness,false);assert.equal(f.$('start-job').disabled,true);
 oldStatus.resolve({ready:true});await old;assert.equal(f.state.readiness,false);
 submission.resolve({id:'new-check'});await submit;
 assert.equal(f.state.readiness,false);assert.equal(f.$('start-job').disabled,true);
});
test('an environment recheck does not disable a reviewed CPU demo',async()=>{
 const f=fixture();f.state.input={id:'mock-input',config:{run:{mode:'screen',mock:true}}};f.state.readiness=true;
 f.api=async path=>path==='/api/environment/check'?{id:'check'}:{ready:false,state:'queued',job_id:'check',diagnostics:{}};
 await f.submitDiagnostic();assert.equal(f.state.readiness,true);
});
test('fixed English interface messages contain no Korean, while raw data stays unchanged',()=>{
 const c={};vm.createContext(c);vm.runInContext(translation,c);
 vm.runInContext('globalThis.catalog=NCF_TRANSLATIONS',c);
 for(const [key,value]of Object.entries(c.catalog))assert.doesNotMatch(value,/[가-힣]/u,key);
 const raw={id:'결합 부위 접촉 수',sequence:'ACDEFG',score:0.832};const before=JSON.stringify(raw),node={textContent:'',isConnected:true};
 c.I18N.text(node,raw.id);for(const lang of ['ko','en'])c.I18N.setLanguage(lang,{persist:false});
 assert.equal(node.textContent,raw.id);assert.equal(JSON.stringify(raw),before);
});
test('minimal retention warning uses the translated DOM text only',()=>{
 const css=readFileSync(new URL('../nanocofold/templates/platform.css',import.meta.url),'utf8');
 assert.doesNotMatch(css,/#result-summary\[data-retention="minimal"\]::after\s*\{[^}]*content:/);
 assert.match(source,/summary\.retention === "minimal"\).*I18N\.term\("핵심 보관본/);
});
