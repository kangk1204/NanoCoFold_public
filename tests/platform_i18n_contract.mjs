import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
function fixture(){const context={};vm.createContext(context);vm.runInContext(source,context);return context.I18N;}
test('mature model plans translate the optional preparation prefix and preserve counts',()=>{
 const ui=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
 const i=fixture(),context={I18N:i};vm.createContext(context);
 vm.runInContext(ui.slice(ui.indexOf('function noviceModelPlan('),ui.indexOf('function fields()')),context);
 for(const [variants,selected] of [[3,2],[1,1],[undefined,2]]){
  const cfg={screen:{comparison:{enabled:true,engines:['colabfold','boltz2','chai1','opendde']}},
   mature:{prefilter:{top_n:2},library:variants===undefined?{}:{max_variants:variants}}};
  const saved=JSON.stringify(cfg),message=i.term(context.noviceModelPlan(cfg,'mature',0));
  for(const language of ['en','ko','en']){
   i.setLanguage(language,{persist:false});
   const prefix=variants===undefined?'':language==='en'?`Prepare up to ${variants} variants · `:`변이 최대 ${variants}개 준비 · `;
   const expected=language==='en'?`${prefix}Up to ${selected} variants plus WT × 4 engines = up to ${(selected+1)*4} models · Final selection counts are determined after execution.`:
    `${prefix}최대 ${selected}개와 WT × 엔진 4개 = 최대 ${(selected+1)*4}모델 · 실제 선별 수는 실행 후 확정됩니다.`;
   assert.equal(i.value(message),expected);assert.equal(JSON.stringify(cfg),saved);
  }
 }
});
test('English is default; switching is reversible for static and dynamic UI messages',()=>{
 const i=fixture(),node={textContent:'',isConnected:true};
 assert.equal(i.language,'en');i.text(node,i.format('처리 {0}/{1} · 성공 {2} · 실패 {3}',[4,4,3,1]));
 assert.equal(node.textContent,'Processed 4/4 · Success 3 · Failed 1');
 i.setLanguage('ko',{persist:false});assert.equal(node.textContent,'처리 4/4 · 성공 3 · 실패 1');
 i.setLanguage('en',{persist:false});assert.equal(node.textContent,'Processed 4/4 · Success 3 · Failed 1');
});
test('raw IDs and dynamic parameters are not translated or interpreted as markup / placeholders',()=>{
 const i=fixture(),node={textContent:'',isConnected:true};
 for(const id of ['완료','미평가',"나노바디2(A3);O'Neil",'<img src=x> {0}']){
  i.text(node,id);i.setLanguage('ko',{persist:false});i.setLanguage('en',{persist:false});assert.equal(node.textContent,id);
  i.text(node,i.format('현재 모델 {0} · 체인 {1} · {2}',[id,'A, B',i.term('미평가')]));
  assert.equal(node.textContent,`Current model ${id} · Chains A, B · Not evaluated`);
 }
});
test('raw log append after clearing a node survives language switches',()=>{
 const i=fixture(),node={textContent:'',isConnected:true};
 i.text(node,i.term('작업 불러오는 중'));i.text(node,'');node.textContent+='원본 로그 완료';
 i.setLanguage('ko',{persist:false});i.setLanguage('en',{persist:false});assert.equal(node.textContent,'원본 로그 완료');
});
test('formatting composes translated UI statuses with untouched scientific parameters',()=>{
 const i=fixture();const message=i.concat([i.term('기록 제외 사유: '),'ipsae, ','아미노산2']);
 assert.equal(i.value(message),'Recorded exclusion reasons: ipsae, 아미노산2');
});
test('unsupported language selections do not change the current language',()=>{
 const i=fixture();i.setLanguage('fr',{persist:false});assert.equal(i.language,'en');
});
test('coordinate provenance captions translate in both directions without Korean fragments in English',()=>{
 const i=fixture();
 for(const suffix of ['원본 좌표 출처 미확인','변환 없음','검증된 원본과 다른 표시 좌표 · 변환 과정 미기록','좌표 직렬화와 기록된 잔기 번호 대응','좌표 직렬화·기록된 잔기 번호 대응·참조 구조 정렬']){
  const ko='검증된 원본 엔진 좌표를 별도로 보존합니다. 화면은 변환된 표시 좌표입니다 · '+suffix;
  const value=i.term(ko);assert.doesNotMatch(i.value(value),/[가-힣]/);i.setLanguage('ko',{persist:false});assert.equal(i.value(value),ko);i.setLanguage('en',{persist:false});
 }
});
test('legacy hotspot wording displays canonical terms without rewriting raw data or IDs',()=>{
 const i=fixture(),raw={candidate_id:'hotspot 미평가',label:'핫스팟 접촉 기준 미통과'};
 const saved=JSON.stringify(raw),node={textContent:'',isConnected:true};
 const label=i.term(raw.label);assert.equal(i.value(label),'Below hotspot contact threshold');
 i.setLanguage('ko',{persist:false});assert.equal(i.value(label),'결합 부위 접촉 기준 미통과');
 i.text(node,raw.candidate_id);i.setLanguage('en',{persist:false});i.setLanguage('ko',{persist:false});
 assert.equal(node.textContent,raw.candidate_id);assert.equal(JSON.stringify(raw),saved);
 assert.equal(i.value(i.term('toString')),'toString');
 const message=i.format('실행 당시 기록 적격: {0} · 지정 부위 포함 적격: {1}',[raw.candidate_id,'미평가']);
 assert.equal(i.value(message),'실행 당시 기록 적격: hotspot 미평가 · 지정 결합 부위 포함 적격: 미평가');
});
test('binding sites and general-complex residues of interest have separate bilingual labels',()=>{
 const i=fixture();for(const key of ['결합 부위(hotspot)','관심 잔기','관심 잔기 접촉률']){
  const value=i.term(key);assert.doesNotMatch(i.value(value),/[가-힣]/);
  i.setLanguage('ko',{persist:false});assert.equal(i.value(value),key);i.setLanguage('en',{persist:false});
 }
});
test('candidate eligibility captions translate composed UI terms and preserve raw engine labels',()=>{
 const ui=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
 const i=fixture(),nodes=new Map(),engine='원본 엔진(007)';
 const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'recorded',textContent:'',isConnected:true});return nodes.get(id);};
 const row={candidate_id:'원본 후보2',predictor_engine:engine,iptm:.6,ipsae:.7,evaluated:true};
 const context={I18N:i,state:{result:{mode:'screen'},candidate:row,filtered:[row]},
  $:get,options(){},fmt:String,metricCaption:()=> '표적–VHH ipTM',
  recordedEvaluationError:()=>false,modelAssessmentCaption:()=>'',
  decisionCaption:value=>value===true?'적격':value===false?'부적격':'미평가',
  candidateEligibility:()=>row.final_eligible};
 vm.createContext(context);
 vm.runInContext(ui.slice(ui.indexOf('function syncCandidateNavigation()'),ui.indexOf('function stepCandidate(')),context);
 const original=JSON.stringify(row);
 for(const [basis,ko,en] of [['recorded','실행 당시 기록 판정','Assessment recorded at execution'],['quality','품질 판정 (결합 부위 제외)','Quality assessment (excluding hotspots)']]){
  get('eligibility-basis').value=basis;
  for(const [value,koStatus,enStatus] of [[true,'적격','Eligible'],[false,'부적격','Ineligible'],[null,'미평가','Not evaluated']]){
   row.final_eligible=value;
   const saved=JSON.stringify(row);
   context.syncCandidateNavigation();
   for(const language of ['en','ko','en']){
    i.setLanguage(language,{persist:false});
    assert.equal(get('candidate-status').textContent,`${engine} · ipSAE 0.7 · ${language==='en'?'Target–VHH ipTM':'표적–VHH ipTM'} 0.6 · ${language==='en'?en:ko}: ${language==='en'?enStatus:koStatus}`);
    assert.equal(JSON.stringify(row),saved);
   }
  }
 }
 delete row.final_eligible;assert.equal(JSON.stringify(row),original);
});
