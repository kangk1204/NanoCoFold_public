/** Labels follow saved scope; coordinate downloads require an explicit verified receipt. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const c={metricCaptions:{iptm:'ipTM',ipsae:'ipSAE'},$(){return {options:[]};}};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('function iptmCaption('),source.indexOf('const decisionCaption'))+'\nglobalThis.caption=metricCaption;',c);
vm.runInContext(source.slice(source.indexOf('function recordedEvaluationError('),source.indexOf('function reportSourceIdentity(')),c);
vm.runInContext(source.slice(source.indexOf('function coordinatePresentation('),source.indexOf('function resultChain(')),c);
const cases=[
 ['general complex',[{iptm_scope:'whole_complex'}],'전체 복합체 ipTM'],
 ['current two-chain VHH',[{iptm_scope:'selected_target_binder_pair',iptm_aggregation:'native_global_two_chain_equivalent'}],'표적–VHH ipTM'],
 ['VHH with auxiliary chains',[{iptm_scope:'selected_target_binder_pair',n_context_chains:2}],'표적–VHH ipTM'],
 ['legacy complex does not infer scope from mode',[{mode:'complex',iptm:.8}],'ipTM · 적용 범위 미기록'],
 ['legacy VHH does not infer scope from mode',[{mode:'screen',iptm:.8}],'ipTM · 적용 범위 미기록'],
 ['unsupported legacy scope',[{iptm_scope:'two_chain_complex'}],'ipTM · 적용 범위 미기록'],
 ['different recorded scopes',[{iptm_scope:'whole_complex'},{iptm_scope:'selected_target_binder_pair'}],'ipTM · 적용 범위 혼합'],
 ['known and unknown records',[{iptm_scope:'whole_complex'},{}],'ipTM · 적용 범위 혼합'],
 ['empty results',[],'ipTM · 적용 범위 미기록'],
];
for(const [name,rows,caption] of cases)test(name,()=>{
 const before=JSON.stringify(rows);assert.equal(c.caption('iptm',rows),caption);
 assert.equal(c.caption('ipsae',rows),'ipSAE');assert.equal(JSON.stringify(rows),before);
});
for(const [name,row,error,assessment] of [
 ['successful',{evaluated:true,error:''},'오류 없음','모델 평가 완료'],
 ['successful blank error',{evaluated:true,error:'  '},'오류 없음','모델 평가 완료'],
 ['failed',{evaluated:false,error:'  예측 실패\n상세 원문  '},'예측 실패','평가 오류 · 상세 기록 확인'],
 ['failed explicit evaluated flag',{evaluated:true,error:'평가 실패'},'평가 실패','평가 오류 · 상세 기록 확인'],
 ['prediction failed',{prediction_error:'엔진 실패'},'엔진 실패','평가 오류 · 상세 기록 확인'],
 ['blank error cannot mask prediction failure',{error:'  ',prediction_error:'엔진 실패'},'엔진 실패','평가 오류 · 상세 기록 확인'],
 ['unevaluated',{evaluated:false,error:''},'미평가','모델 미평가'],
 ['missing flag',{},'미평가','모델 미평가'],
])test(name+' error and assessment are consistent',()=>{
 assert.equal(c.evaluationErrorCaption(row),error);assert.equal(c.modelAssessmentCaption(row),assessment);
});
test('caption refresh preserves implicit option values across repeated scope changes',()=>{
 const nodes=Object.fromEntries(['sort-metric','plot-x','plot-y'].map(id=>{
  let explicit;const option={textContent:'iptm',get value(){return explicit??this.textContent;},set value(value){explicit=value;}};
  return [id,{options:[option]}];
 }));
 c.$=id=>nodes[id];
 for(const [scope,caption] of [['whole_complex','전체 복합체 ipTM'],['selected_target_binder_pair','표적–VHH ipTM'],[null,'ipTM · 적용 범위 미기록']]) {
  c.refreshMetricCaptions([{iptm_scope:scope}]);
  for(const node of Object.values(nodes)) {
   assert.equal(node.options[0].value,'iptm');assert.equal(node.options[0].textContent,caption);
  }
 }
});
for(const mode of ['complex','screen','mature','design'])for(const relation of ['native_identical','transformed_display','unverified_legacy'])test(mode+' '+relation+' coordinate contract',()=>{
 const raw='data_native\n# exact UTF8 원본\n';
 const data={content:relation==='transformed_display'?'data_display\n# changed display\n':raw,
   raw_content:relation==='unverified_legacy'?null:raw,
   coordinate_source:{schema_version:2,display_relation:relation,display_transform:'renumbering',raw_preserved:relation!=='unverified_legacy',verification:{status:relation==='unverified_legacy'?'unavailable':'verified'}}};
 const before=JSON.stringify(data),result=c.coordinatePresentation(data);
 assert.equal(result.rawAvailable,relation!=='unverified_legacy');
 assert.match(result.caption,relation==='native_identical'?/동일/:relation==='transformed_display'?/변환된 표시 좌표/:/검증된 원본 엔진 좌표가 없습니다/);
 assert.equal(JSON.stringify(data),before);
 assert.equal(c.structureDataNote(data).includes('원본 예측 좌표를 표시합니다'),false);
});
for(const [name,data] of [
 ['old presence-only source',{content:'display',raw_content:'raw',coordinate_source:{raw_preserved:true}}],
 ['hash mismatch',{content:'display',raw_content:'raw',coordinate_source:{schema_version:2,raw_preserved:true,verification:{status:'hash_mismatch'},display_relation:'transformed_display'}}],
 ['verified metadata without original bytes',{content:'display',coordinate_source:{schema_version:2,raw_preserved:true,verification:{status:'verified'},display_relation:'transformed_display'}}],
])test(name+' cannot enable original download',()=>assert.equal(c.coordinatePresentation(data).rawAvailable,false));
test('native-identical metadata cannot mislabel different displayed bytes',()=>{
 const result=c.coordinatePresentation({content:'display',raw_content:'raw',coordinate_source:{schema_version:2,raw_preserved:true,verification:{status:'verified'},display_relation:'native_identical'}});
 assert.equal(result.rawAvailable,true);assert.match(result.caption,/관계는 미확인/);
});
