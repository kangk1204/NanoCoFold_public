"""Actual saved warnings and prediction seed evidence stay display-only."""
from pathlib import Path
import subprocess


def test_native_seed_and_warning_presentation():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "--input-type=module", "-e", r'''
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync('nanocofold/templates/platform.js','utf8');
const context=vm.createContext({});
const start=source.indexOf('function recordedCandidateSeed(');
vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
const row={candidate_id:'candidate',engine:'boltz2',library_seed:123};
const observations=[{candidate_id:'candidate',engine:'boltz2',seed:42}];
const before=JSON.stringify({row,observations});
assert.equal(context.recordedCandidateSeed(row,observations),42);
assert.equal(context.recordedCandidateSeed(row,[]),null);
assert.equal(context.recordedCandidateSeed(row,[{...observations[0],seed:0}]),0);
assert.equal(context.recordedCandidateSeed(row,[...observations,{...observations[0],seed:1}]),'1, 42');
assert.equal(JSON.stringify({row,observations}),before);
vm.runInContext(readFileSync('nanocofold/templates/platform-i18n.js','utf8'),context);
const warnings=[
 'ESM env 를 찾지 못해 ESM 서열 점수를 결측(NaN)으로 기록합니다. envs/setup_esm.sh 를 실행하세요.',
 '소수성 잔기가 2개입니다. 자동 결합 부위의 소수성 패치 기준(≥3)은 탐색용 휴리스틱이며, 실제 결합이나 설계 성공을 보장하지 않습니다.',
];
for(const raw of warnings){
 const message=context.I18N.term(raw);
 context.I18N.setLanguage('en',{persist:false});assert.doesNotMatch(context.I18N.value(message),/[가-힣]/);
 context.I18N.setLanguage('ko',{persist:false});assert.equal(context.I18N.value(message),raw);
 context.I18N.setLanguage('en',{persist:false});assert.doesNotMatch(context.I18N.value(message),/[가-힣]/);
}
assert.equal(context.I18N.value(context.I18N.term('연구자 메모 '+warnings[1])),'연구자 메모 '+warnings[1]);
'''], cwd=root, check=True, capture_output=True, text=True)
