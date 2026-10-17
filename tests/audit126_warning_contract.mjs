import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
const context=vm.createContext({console});vm.runInContext(source,context);
const term=value=>context.I18N.term(value);
const cdrBody=': CDR 의존 liability 는 미평가로 기록됩니다. rank.weights.dev_flags가 0이 아니면 종합 순위와 최종 선별을 보류합니다. 실제 분석은 envs/setup_antibody.sh로 ANARCI를 설치하거나 mature.cdr.source=config와 검증한 1-based CDR 범위를 지정하세요. 미평가를 위험 없음으로 처리하지 않습니다.';
const scientificWarnings=[
 '결합 부위를 지정하지 않았습니다. 결합 위치를 제한하지 않고 탐색합니다.',
 'mock/dry-run'+cdrBody,
 'ANARCI 환경 없음'+cdrBody,
 'final_eligible 후보가 0종입니다. 합성 목록은 비어 있습니다. 임계값/예측 품질을 점검하세요.',
 'final_eligible 후보가 0종입니다. rank.fallback_to_ineligible=true는 종합점수가 있는 부적격 후보만 포함하며 미평가 보류 후보에는 적용되지 않습니다.',
 '전체 성공 후보에서 미평가인 순위 지표: hotspot_contact_frac, antifold. 해당 가중치를 제외하고 나머지 지표로 순위를 계산했습니다. 미평가를 0점이나 부적격으로 해석하지 마세요.',
 'handoff 게이트 미통과(7건): 발주 목록은 확정할 수 없습니다. `nanocofold validate-handoff` 로 항목을 확인하세요.',
];
for(const warning of scientificWarnings)test('authored science warning has an English rendering: '+warning.slice(0,36),()=>{
 const result=term(warning);assert.equal(result.ko,warning);assert.notEqual(result.en,warning);assert.doesNotMatch(result.en,/[가-힣]/);
});

test('MSA warning preserves captured Korean path byte for byte',()=>{
 const path='[local path]/나노바디2/cache.a3m';
 const warning=`캐시된 MSA가 없습니다: ${path}. 서열만 입력으로 단일서열 모드(msa=empty)로 진행합니다. 정확도를 높이려면 structure.msa.backend=colabfold_search 로 MSA를 먼저 생성하세요.`;
 const result=term(warning);assert.equal(result.ko,warning);assert.ok(result.en.includes(path));assert.ok(result.en.startsWith('No cached MSA'));
});

test('composite ranking notice translates all authored clauses including partial mapping',()=>{
 const warning='종합점수·기록 순위 보류 3/9개 모델. 이 중 1개는 CDR 개발가능성이 미평가입니다. ANARCI 환경 또는 서열에 맞는 CDR 범위를 확인하세요. 이 중 2개는 요청한 결합 부위의 구조 대응이 불완전하여 접촉률을 순위에 사용하지 않고 보류합니다. fallback_to_ineligible=true는 종합점수가 있는 부적격 후보에만 적용됩니다. 실패 예측이나 가중 CDR 미평가로 보류된 후보는 포함하지 않습니다.';
 const result=term(warning);assert.equal(result.ko,warning);assert.doesNotMatch(result.en,/[가-힣]/);assert.match(result.en,/3\/9/);assert.match(result.en,/mapping is incomplete for 2/);
});

test('same ranking notice translates weighted/interface and missing-score explanations',()=>{
 for(const tail of [
  '이 중 2개는 접촉이 없거나 가중 계면 지표가 미평가입니다. 성공한 무접촉 예측과 측정된 접촉0개 기록은 보존하고 순위·선정은 보류합니다.',
  '이 중 2개는 다른 성공 후보에서 평가된 가중 지표 (ipsae, interface_pae) 값이 없어 중앙값으로 보충하지 않고 보류합니다.',
  '저장된 종합점수가 없어 순위·선별을 추정하지 않습니다.',
 ]) assert.doesNotMatch(term('종합점수·기록 순위 보류 2/4개 모델. '+tail).en,/[가-힣]/);
});

test('authored configuration descriptions translate but unknown prose is not swallowed as a field name',()=>{
 assert.equal(term('복합체 설정은 객체여야 합니다.').en,'Complex settings must be an object.');
 assert.equal(term('random Cys 보호 설정은 true/false여야 합니다.').en,'Random cysteine protection must be true or false.');
 assert.equal(term('screen.comparison 설정은 객체여야 합니다.').en,'Setting screen.comparison must be an object.');
 const unknown='나노바디2 확인이 필요한 설정은 객체여야 합니다.';assert.equal(term(unknown).en,unknown);
});

test('unknown IDs, sequences and original records are not recursively rewritten',()=>{
 for(const original of ['나노바디2','ACDEFGHIKLMNPQRSTVWY','{"id":"표적","sequence":"ACDEFG"}','tier2'])assert.equal(term(original).en,original);
 const source='나노바디2: 파일이 없습니다: [local path]';assert.equal(term(source).en,'나노바디2: The file does not exist: [local path]');
});

test('static warning list and authored selection status opt into explicit bindings',()=>{
 const template=readFileSync(new URL('../nanocofold/templates/report.html.j2',import.meta.url),'utf8');
 assert.match(template,/<li data-report-i18n>\{\{ w \}\}<\/li>/);
 assert.match(template,/key == 'selection_rank'.*report_term\(v\)/);
 assert.equal(term('사용 안 함').en,'Disabled');assert.equal(term('미선별').en,'Not selected');
});

for(const warning of [
 'target.aux_chains는 보조 체인 목록이어야 합니다: [{name: ..., sequence: ...}]',
 "target.name는 문자열이어야 합니다. ':'를 포함하는 --set 값은 따옴표로 감싸세요.",
 'screen.extra는 목록이어야 합니다.',
 'CSV 헤더를 안전한 텍스트로 변환하면 열 이름이 중복됩니다. 원본 표의 열 이름을 구분해 주세요.',
])test('new authored input/export warning translates: '+warning.slice(0,24),()=>assert.doesNotMatch(term(warning).en,/[가-힣]/));
test('new filename-bearing diagnostics preserve the supplied filename',()=>{
 const file='[local path]/나노바디2/input.fasta';
 const translated=term('FASTA 파일을 UTF-8로 다시 저장한 뒤 입력하세요: '+file).en;
 assert.equal(translated,'Save the FASTA file as UTF-8 and try again: '+file);
});
