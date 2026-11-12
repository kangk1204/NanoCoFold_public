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

test('full-sequence prediction review preserves counts, mapping choices and source warnings', () => {
  const cases = [
    ["체인 B: 관측된 표준 잔기 205개, 기록된 전체 서열 214개입니다. 말단·내부 결손과 수정 잔기 여부를 확인하세요.",
     "Chain B: 205 standard residues have coordinates. The recorded full sequence contains 214 residues. Check for missing terminal or internal segments and for modified residues."],
    ["전체 서열로 새 단백질 구조를 예측합니다. 원본 좌표는 참조로만 보존하며 미관측 좌표를 채운 실험 구조로 표시하지 않습니다.",
     "NanoCoFold predicts a new structure from the full protein sequence. The original coordinates are retained for reference. Any newly modeled positions are predictions."],
    ["원본의 수정 잔기·chromophore·당질·리간드·핵산·막 환경은 이 단백질 서열 예측에 재현되지 않습니다.",
     "This protein-sequence prediction omits modified residues, chromophores, glycans, ligands, nucleic acids, and the membrane environment from the original structure."],
    ["선택한 원본 결합 부위를 전체 서열 위치로 대응했습니다. 노출도와 접촉은 예측 구조에서 다시 평가합니다.",
     "The selected hotspots from the original structure were mapped to positions in the full sequence. Solvent exposure and contacts are reassessed on the predicted structure."],
    ["결합 부위는 예측 구조에서 다시 계산합니다.",
     "Hotspots are recalculated from the predicted structure."],
    ["체인 B: 일부 관측 잔기의 전체 서열 위치가 불명확합니다. 원본 번호·결합 부위를 임의 대응하지 않았습니다.",
     "Chain B: some observed residues could not be mapped unambiguously to the full sequence. Their original residue numbers and hotspots remain unmapped."],
    ["결합 부위 미지정: 결합 위치를 제한하지 않고 탐색합니다.",
     "No binding site specified: exploration does not constrain the binding location."],
  ];
  for (const [source, expected] of cases) {
    const message = context.I18N.term(source);
    context.I18N.setLanguage('en', {persist: false});
    assert.equal(context.I18N.value(message), expected);
    context.I18N.setLanguage('ko', {persist: false});
    assert.equal(context.I18N.value(message), source);
  }
});

test('pending submission and recovery are distinct in both languages', () => {
  for (const [source, expected] of [
    ['분석 요청을 보내는 중입니다. 접수되면 작업 화면이 열립니다.', 'Submitting your analysis. The job will open when it is accepted.'],
    ['서버에서 이 요청의 작업을 찾고 있습니다.', 'Checking whether the server accepted this analysis.'],
  ]) {
    const message = context.I18N.term(source);
    context.I18N.setLanguage('en', {persist: false});
    assert.equal(context.I18N.value(message), expected);
    context.I18N.setLanguage('ko', {persist: false});
    assert.equal(context.I18N.value(message), source);
  }
});
