import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
function fixture(){const context={};vm.createContext(context);vm.runInContext(source,context);return context.I18N;}
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
