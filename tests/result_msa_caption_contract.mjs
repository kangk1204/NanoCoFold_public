import {koreanContext} from './i18n_context.mjs';
/** Recorded MSA source and MSA mode are distinct; no source is inferred from precomputed mode. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8'),c={};
koreanContext(c);vm.runInContext(source.slice(source.indexOf('function resultMsaCaption('),source.indexOf('async function loadResult(id)')),c);
const cases=[
 ['complex public API source survives precomputed mode',{mode:'complex',conditions:{msa:'precomputed',msa_source:'public_api',allow_public_msa_server:true},config:{complex:{msa:'precomputed',msa_source:'public_api'}}},'API에서 확보한 MSA'],
 ['complex recorded conditions source has priority over config',{mode:'complex',conditions:{msa:'precomputed',msa_source:'cache'},config:{complex:{msa_source:'public_api'}}},'로컬·캐시 MSA 실행'],
 ['complex config source is used when conditions record only mode',{mode:'complex',conditions:{msa:'precomputed'},config:{complex:{msa_source:'cache'}}},'로컬·캐시 MSA 실행'],
 ['complex empty mode remains empty even when source metadata is retained',{mode:'complex',conditions:{msa:'empty',msa_source:'public_api'},config:{complex:{msa_source:'cache'}}},'MSA 없음 · 서열만 사용'],
 ['complex config empty mode remains visible when conditions are absent',{mode:'complex',config:{complex:{msa:'empty'}}},'MSA 없음 · 서열만 사용'],
 ['precomputed mode without any recorded source stays unknown',{mode:'complex',conditions:{msa:'precomputed'},config:{complex:{msa:'precomputed'}}},'MSA 출처 미기록'],
 ['unknown explicit conditions source is not replaced with a known config source',{mode:'complex',conditions:{msa:'precomputed',msa_source:'unrecognized'},config:{complex:{msa_source:'public_api'}}},'MSA 출처 미기록'],
 ['missing complex MSA metadata stays unknown',{mode:'complex'},'MSA 출처 미기록'],
 ['legacy complex source value remains readable',{mode:'complex',conditions:{msa:'public_api'}},'API에서 확보한 MSA'],
 ['noncomplex source continues to use screen config',{mode:'screen',conditions:{msa:'empty',msa_source:'cache'},config:{complex:{msa_source:'cache'},screen:{colabfold:{msa_source:'public_api'}}}},'API에서 확보한 MSA'],
 ['noncomplex cache source continues to use screen config',{mode:'design',config:{screen:{colabfold:{msa_source:'cache'}}}},'로컬·캐시 MSA 실행'],
 ['missing noncomplex source stays unknown',{mode:'mature',conditions:{msa_source:'public_api'}},'MSA 출처 미기록'],
];
for(const [name,summary,expected] of cases)test(name,()=>assert.equal(c.resultMsaCaption(summary),expected));
