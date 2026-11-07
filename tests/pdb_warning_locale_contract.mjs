import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const context={};vm.createContext(context);vm.runInContext(readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8'),context);
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
vm.runInContext(source.slice(source.indexOf('function chainLabel('),source.indexOf('function selectedPartners(')),context);
const messages=[
 '접두사 없는 결합 부위 번호는 제공 구조의 PDB 잔기 번호로 해석했습니다. 번호 체계를 고정하려면 pdbN 또는 seqN으로 입력하세요.',
 'B:52: 매몰 잔기(relative SASA=0.1332384278627217). 노출 잔기로 교체 권장.',
 '평균 상대 노출도 0.39, 소수성 잔기 3개. 점수는 후보 비교용이며 확률이 아닙니다.',
 '체인 C: 단백질 표준 잔기가 없어 normalized structure.pdb에서 제외했습니다.',
 'context.pdb에는 structure.pdb와 같은 정규화 단백질 체인/잔기 번호에 비단백질 context를 추가 보존했습니다. SASA/당질 영향은 후속 단계에서 별도 확인하세요.',
 '체인 B: alternate atom location 25개가 있습니다. BioPython이 선택한 altloc: 19:CA=A, 19:CB=A 외 17개. 숨은 좌표 선택이므로 원본 확인을 권장합니다.',
];
test('PDB and hotspot warnings switch languages without changing values',()=>{
 for(const raw of messages){const message=context.I18N.term(raw);context.I18N.setLanguage('en',{persist:false});assert.doesNotMatch(context.I18N.value(message),/[가-힣]/);context.I18N.setLanguage('ko',{persist:false});assert.equal(context.I18N.value(message),raw);}
 context.I18N.setLanguage('en',{persist:false});assert.match(context.I18N.value(context.I18N.term(messages[1])),/B:52:.*0\.1332384278627217/);
 const note='연구자 메모 '+messages[1];assert.equal(context.I18N.value(context.I18N.term(note)),note);
});
test('chain labels preserve original names and IDs through language changes',()=>{
 const chain={id:'B',original_id:'표적{0}',name:'Lysozyme',length:129};const saved=JSON.stringify(chain),message=context.chainLabel(chain);
 for(const language of ['en','ko','en']){context.I18N.setLanguage(language,{persist:false});assert.equal(context.I18N.value(message),`B · ${language==='en'?'Original':'원본'} 표적{0} · Lysozyme · 129 aa`);}
 assert.equal(JSON.stringify(chain),saved);
});
