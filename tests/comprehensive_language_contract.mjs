import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {gzipSync} from 'node:zlib';
import {createHash,webcrypto} from 'node:crypto';
const catalog=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function i18n(){const c={};vm.createContext(c);vm.runInContext(catalog,c);return c.I18N;}
test('complete backend clauses translate after raw Korean candidate prefixes and remain reversible',()=>{
 const i=i18n(),id="완료: 나노바디2(A3);O'Neil {0}";
 for(const [ko,en] of [['작업이 없습니다.','The job does not exist.'],['진행 상태 파일을 읽지 못했습니다. 로그를 확인하세요.','Could not read the progress-state file. Check the logs.'],['선택한 표적 체인이 없습니다.','The selected target chain does not exist.']]){
  const raw=id+': '+ko,message=i.term(raw);
  assert.equal(i.value(message),id+': '+en);i.setLanguage('ko',{persist:false});assert.equal(i.value(message),raw);i.setLanguage('en',{persist:false});
 }
 assert.equal(i.value(i.term('진행 상태 형식 오류')),'Invalid progress-state format.');
 assert.equal(i.value(i.term('진행 이 문구는 사용자가 입력함')),'진행 이 문구는 사용자가 입력함');
 assert.equal(i.value(i.term('진행 1/미기록')),'Progress 1/Not recorded');
});
test('unmeasured numeric values use the active display language without changing numeric formatting',()=>{
 const i=i18n(),c={I18N:i};vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const fmt ='),source.indexOf('/* REPORT_SEMANTICS_START */'))+'globalThis.formatValue=fmt',c);
 for(const lang of ['en','ko','en']){i.setLanguage(lang,{persist:false});
  for(const value of [null,undefined,NaN,Infinity])assert.equal(c.formatValue(value),lang==='en'?'Not evaluated':'미평가');
  assert.equal(c.formatValue(.123456),'0.123');assert.equal(c.formatValue(0),'0');
 }
});
function packed(values){const n=Math.sqrt(values.length),bytes=Buffer.alloc(values.length*8);values.forEach((v,i)=>bytes.writeDoubleLE(v,i*8));
 const sha=createHash('sha256').update(bytes).digest('hex');return {sha,record:{encoding:'gzip-base64-float64le',dimension:n,bytes:bytes.length,sha256:sha,data:gzipSync(bytes).toString('base64')}};}
function decoder(){const c={window:{},atob,Blob,DecompressionStream,crypto:webcrypto};vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const offlinePaeCache ='),source.indexOf('function localizeEmptyPaeAnnotation()')),c);return c;}
test('offline PAE decodes exact numeric bytes, caches by digest and keeps independent labels untouched',async()=>{
 const c=decoder(),p=packed([0,.123456789,5,30]),payload={pae_arrays:{[p.sha]:p.record}},pae={values_ref:p.sha,labels:['A:1','B:2']};
 const before=JSON.stringify({pae,payload}),matrix=await c.offlinePaeValues(pae,payload);
 assert.equal(JSON.stringify(matrix),'[[0,0.123456789],[5,30]]');assert.strictEqual(matrix,await c.offlinePaeValues({...pae,labels:['X:3','Y:4']},payload));
 assert.equal(JSON.stringify({pae,payload}),before);
 const legacy=[[1,2],[3,4]];assert.strictEqual(await c.offlinePaeValues({values:legacy}),legacy);
});
test('offline PAE rejects truncation, over-expansion, hash mismatch and invalid values before plotting',async()=>{
 for(const mutate of [r=>r.data=gzipSync(Buffer.alloc(24)).toString('base64'),r=>r.data=gzipSync(Buffer.alloc(4096)).toString('base64'),r=>r.data=gzipSync(Buffer.alloc(32,1)).toString('base64'),r=>r.bytes=1]){
  const c=decoder(),p=packed([1,2,3,4]);mutate(p.record);
  await assert.rejects(c.offlinePaeValues({values_ref:p.sha,labels:['A:1','B:2']},{pae_arrays:{[p.sha]:p.record}}),/PAE/);
 }
 const c=decoder(),p=packed([1,2,3,Infinity]);await assert.rejects(c.offlinePaeValues({values_ref:p.sha,labels:['A:1','B:2']},{pae_arrays:{[p.sha]:p.record}}),/PAE/);
});
test('offline PAE view size is bounded before decompression while full compressed record remains intact',async()=>{
 const c=decoder(),sha='a'.repeat(64),dimension=4097,record={encoding:'gzip-base64-float64le',dimension,bytes:dimension**2*8,sha256:sha,data:'preserved'};
 const payload={pae_arrays:{[sha]:record}},before=JSON.stringify(payload);
 await assert.rejects(c.offlinePaeValues({values_ref:sha,labels:Array(dimension).fill('A:1')},payload),/128 MiB/);assert.equal(JSON.stringify(payload),before);
});

test('raw binding-site captions retain provenance and corrupt PAE has a bilingual, PAE-specific explanation',()=>{
 const i=i18n(),c={};vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const metricCaptions ='),source.indexOf('const decisionCaption ='))+'globalThis.actualMetricCaption=metricCaption;',c);
 const rawCaption=c.actualMetricCaption('site_satisfied');
 assert.equal(rawCaption,'지정 결합 부위 판정 (기록)');
 const caption=i.term(rawCaption);
 const damaged='PAE 자료가 손상되어 표시할 수 없습니다. 표와 구조는 계속 확인할 수 있습니다.';
 const damageCaption=i.term(damaged);
 assert.equal(i.value(caption),'Recorded binding-site decision');
 assert.equal(i.value(damageCaption),'The PAE data is damaged and cannot be displayed. Tables and structures remain available.');
 i.setLanguage('ko',{persist:false});assert.equal(i.value(caption),rawCaption);assert.equal(i.value(damageCaption),damaged);
 assert.notEqual(i.value(caption),i.value(i.term('지정 결합 부위 충족')));
 i.setLanguage('en',{persist:false});assert.equal(i.value(caption),'Recorded binding-site decision');
});

test('partial-coordinate warning translates all counts and reverses without rewriting the record',()=>{
 const i=i18n(),raw='입력 서열의 95.8%만 제공 구조에 대응합니다 (205/214잔기). 나머지 9잔기는 제공 좌표로 평가하지 못합니다. 결손·부분 구조를 확인하세요. 전체 입력 서열은 보존합니다.';
 const message=i.term(raw); assert.match(i.value(message),/covers 95.8%.*205\/214.*remaining 9/);
 i.setLanguage('ko',{persist:false});assert.equal(i.value(message),raw);
});

test('shuffled PAE restores exact float64 bytes including non-float32 values',async()=>{
 const p=packed([0,.12345678912345678,30,1.23456789012345]),raw=Buffer.alloc(32);
 [0,.12345678912345678,30,1.23456789012345].forEach((v,i)=>raw.writeDoubleLE(v,i*8));
 const shuffled=Buffer.alloc(32);for(let lane=0;lane<8;lane++)for(let i=0;i<4;i++)shuffled[lane*4+i]=raw[i*8+lane];
 p.record.encoding='gzip-base64-shuffled-float64le';p.record.data=gzipSync(shuffled).toString('base64');
 const c=decoder(),rows=await c.offlinePaeValues({values_ref:p.sha,labels:['A:1','B:1']},{pae_arrays:{[p.sha]:p.record}});
 assert.equal(JSON.stringify(rows),'[[0,0.12345678912345678],[30,1.23456789012345]]');
});

test('structure assets preserve coordinates and selection identity; corrupt assets fail closed',async()=>{
 const c={window:{},atob,Blob,DecompressionStream,TextDecoder,crypto:webcrypto};vm.createContext(c);
 vm.runInContext(source.slice(source.indexOf('const offlineStructureCache ='),source.indexOf('function setPaeErrorStatus')),c);
 const body={content:'ATOM /home/native\n',raw_content_ref:'content',format:'pdb',seed:42,pae:{values_ref:'p'}};
 const raw=Buffer.from(JSON.stringify(body)),sha=createHash('sha256').update(raw).digest('hex');
 const record={encoding:'gzip-base64-json',bytes:raw.length,sha256:sha,data:gzipSync(raw).toString('base64')};
 const payload={structure_assets:{[sha]:record}},binding={candidate_id:'first',observation_id:'s42',structure_ref:sha};
 const a=await c.offlineStructure(binding,payload),b=await c.offlineStructure({...binding,candidate_id:'second'},payload);
 assert.equal(a.candidate_id,'first');assert.equal(b.candidate_id,'second');assert.equal(a.raw_content,body.content);
 a.pae.values=[[1]];assert.equal(b.pae.values,undefined);assert.equal(payload.structure_assets[sha].data,record.data);
 const bad=structuredClone(payload);bad.structure_assets[sha].sha256='0'.repeat(64);
 await assert.rejects(c.offlineStructure(binding,bad),/해시/);
 const legacy={candidate_id:'legacy',content:'saved'};assert.strictEqual(await c.offlineStructure(legacy),legacy);
});
