/** Saved opaque diagnostic fixtures only; no engine, network, sequence or order operations. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const block=source.split('// HANDOFF_PRESENTATION_START\n')[1].split('// HANDOFF_PRESENTATION_END')[0];
const c={};vm.createContext(c);vm.runInContext(block,c);
const summary={mode:'screen',complete:true,status:'complete',state:{phases:{final:{handoff_gate:{ok:false,n_errors:6,n_warnings:1}}}}};
test('legacy completed computation retains blocked handoff and unknown details',()=>{
 const before=JSON.stringify(summary),view=c.resultHandoffView(summary);
 assert.equal(view.status,'blocked');assert.equal(view.n_errors,6);assert.equal(view.n_warnings,1);assert.equal(view.detail_status,'missing');
 assert.equal(JSON.stringify(summary),before);
});
test('missing and malformed details cannot become a zero or pass',()=>{
 for(const diagnostics of [null,{},[],{detail_status:'available',ok:'true',errors:[],warnings:[],n_errors:0,n_warnings:0}]){
  const view=c.resultHandoffView({mode:'screen',handoff_diagnostics:diagnostics});
  assert.equal(view.status,'unknown');assert.equal(view.n_errors,null);
 }
});
test('saved detailed status requires exact boolean, counts and agreement',()=>{
 const receipt={detail_status:'available',ok:true,errors:[],warnings:[],n_errors:0,n_warnings:0};
 assert.equal(c.resultHandoffView({mode:'screen',handoff_diagnostics:receipt}).status,'passed');
 assert.equal(c.resultHandoffView({...summary,handoff_diagnostics:receipt}).status,'blocked');
 assert.equal(c.resultHandoffView({mode:'screen',handoff_diagnostics:{...receipt,n_errors:1}}).status,'unknown');
});
test('saved empty MSA remains an independent limitation',()=>{
 assert.equal(c.resultHandoffView({...summary,config:{structure:{msa:{backend:'empty'}}}}).empty_msa,true);
 assert.equal(c.resultHandoffView(summary).empty_msa,false);
});
test('explicit bilingual text preserves arbitrary saved IDs and changes reversibly',()=>{
 const context={};vm.createContext(context);vm.runInContext(readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8'),context);
 const i=context.I18N,token=i.bilingual('저장된 설명 · opaque {0} <b>','Saved explanation · opaque {0} <b>');
 assert.equal(i.value(token),'Saved explanation · opaque {0} <b>');i.setLanguage('ko',{persist:false});assert.equal(i.value(token),'저장된 설명 · opaque {0} <b>');
 i.setLanguage('en',{persist:false});assert.equal(i.value(token),'Saved explanation · opaque {0} <b>');
});
