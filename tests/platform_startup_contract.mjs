/** Startup visibility must follow completed initialization; offline viewer bypasses input UI. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const html=readFileSync(new URL('../nanocofold/templates/platform.html',import.meta.url),'utf8');
function fixture({offline=false,boot}={}){
 const nodes=new Map(),buttons=[{disabled:false},{disabled:false}],notices=[];let reloads=0;
 const c={offline,boot,notice:(...args)=>notices.push(args),window:{location:{reload(){reloads++;}}},document:{body:{dataset:{}},querySelectorAll:()=>buttons},$:id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:''});return nodes.get(id);}};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function platformStartup('),source.indexOf('\nasync function boot()')),c);
 return {c,buttons,notices,get reloads(){return reloads;}};
}
test('server HTML hides the raw input form before any deferred script executes',()=>{
 assert.match(html,/<body data-startup="loading">/);assert.match(html,/body\[data-startup="loading"\] #view-input/);assert.match(html,/body\[data-startup="failed"\] #view-input \{ display: none !important; \}/);assert.match(html,/id="platform-startup-message">화면을 준비하고 있습니다/);assert.match(source,/await initNoviceFlow\(\)/);
});
test('input remains hidden and menu disabled until the asynchronous boot finishes',async()=>{
 let done;const f=fixture({boot:()=>new Promise(resolve=>done=resolve)}),pending=f.c.startPlatform();assert.equal(f.c.document.body.dataset.startup,'loading');assert.ok(f.buttons.every(b=>b.disabled));assert.equal(f.c.$('platform-startup').hidden,false);
 done();await pending;assert.equal(f.c.document.body.dataset.startup,'ready');assert.equal(f.c.$('platform-startup').hidden,true);assert.ok(f.buttons.every(b=>!b.disabled));
});
test('failed boot retains the hidden form and exposes a clean document retry',async()=>{
 const f=fixture({boot:async()=>{throw Error('server unavailable');}});await f.c.startPlatform();assert.equal(f.c.document.body.dataset.startup,'failed');assert.match(f.c.$('platform-startup-message').textContent,/server unavailable/);assert.equal(f.c.$('platform-startup-retry').hidden,false);f.c.$('platform-startup-retry').onclick();assert.equal(f.reloads,1);assert.ok(f.buttons.every(b=>b.disabled));
});
test('offline viewer executes only its existing boot and skips input loading state',async()=>{
 let calls=0;const f=fixture({offline:true,boot:async()=>{calls++;}});f.c.platformStartup=()=>{throw Error('Offline must not initialize input loading controls');};await f.c.startPlatform();assert.equal(calls,1);assert.equal(f.c.document.body.dataset.startup,undefined);assert.ok(f.buttons.every(b=>!b.disabled));
});
