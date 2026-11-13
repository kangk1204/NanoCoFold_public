import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const root=new URL('../',import.meta.url),source=readFileSync(process.env.NANOCOFOLD_UI_TEST_SOURCE||new URL('nanocofold/templates/platform.js',root),'utf8');
const starter=JSON.parse(readFileSync(new URL('nanocofold/_data/starter-presets.json',root)));
function fixture(){
 const nodes=new Map(),actions=new Map(),writes=[];
 const get=id=>{if(!nodes.has(id)){const n={value:'',checked:false,hidden:false,textContent:'',dataset:{},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k]??null;}};Object.defineProperty(n,'valueAsNumber',{get(){return this.value===''?NaN:Number(this.value);}});nodes.set(id,n);}return nodes.get(id);};
 for(const [id,value]of Object.entries({'analysis-preset':'small-four-v1',mode:'design',advanced:'{}',seed:'42','screen-engine':'chai1','novice-msa':'cache','job-name':'opaque'}))get(id).value=value;
 const document={documentElement:{},getElementById:()=>null,dispatchEvent(){},querySelectorAll:()=>starter.engines.map(value=>({value,checked:true})),querySelector:()=>({hidden:false})};
 const c={structuredClone,window:{confirm:()=>true},state:{starter,planningDefaults:{design:{engine:'rfantibody',num_designs:1000,mpnn:{seqs_per_struct:4},boltzgen:{inverse_fold_num_sequences:1},filters:{top_n_to_score:300}},screen:{engine:'chai1',tier2_n:50},rank:{final_n:30,include_wt_control:false}},presets:{standard:{}},inputRevision:1,readiness:true,submissionId:'stable'},$:get,CustomEvent:class{},fields(){},view(){},notice(){},action:(name,fn)=>actions.set(name,fn),selectJob:async()=>{},renderInputReadiness(){},crypto:{randomUUID:()=> 'unique'},api:async(path,body)=>{writes.push({path,body});return{id:'fake-job'};}};
 vm.createContext(c);vm.runInContext(readFileSync(new URL('nanocofold/templates/platform-i18n.js',root),'utf8'),c);c.document=document;
 vm.runInContext(source.slice(source.indexOf('function merge('),source.indexOf('function fields()')),c);
  vm.runInContext(source.slice(source.indexOf('function invalidateInput('),source.indexOf('function chainLabel(')),c);
 return{c,get,writes,actions};
}
test('native mutation range controls map CDR3 and custom masks without changing CDR annotations',()=>{
 const {c,get}=fixture();get('mode').value='mature';get('mutation-strategy').value='random';
 get('mutation-count').value='100';get('mutation-min').value='2';get('mutation-max').value='2';
 get('mutation-region').value='cdr3';
 const original={mature:{cdr:{source:'config',regions:{CDR3:[99,106]}}}}, before=JSON.stringify(original);
 let cfg=c.matureInputSettings(original);assert.equal(cfg.mature.library.mutation_region,'cdr3');
 assert.equal(cfg.mature.library.size,101);assert.equal(cfg.mature.library.min_mutations,2);assert.equal(cfg.mature.library.max_mutations,2);
 get('mutation-region').value='custom';get('mature-library-settings').hidden=true;get('random-mutation-fields').hidden=true;assert.throws(()=>c.matureInputSettings(original),/번호를 입력/);
 assert.equal(get('mature-library-settings').hidden,false);assert.equal(get('random-mutation-fields').hidden,false);assert.equal(get('mutation-ranges-field').hidden,false);c.clearScalePreview();assert.match(get('mutation-scope-current').textContent || '',/not determined|미확정/);
 get('mutation-ranges').value='99-103, 106';cfg=c.matureInputSettings(original);
 assert.equal(JSON.stringify(cfg.mature.library.mutation_ranges),'[[99,103],[106,106]]');
 assert.equal(JSON.stringify(original),before);
 get('mutation-ranges').value='99-103, 103';assert.throws(()=>c.matureInputSettings(original),/겹칩니다/);
});
const design={run:{mode:'design',seed:42},design:{engine:'rfantibody',num_designs:1000,mpnn:{seqs_per_struct:4},filters:{top_n_to_score:1000}},screen:{engine:'boltz2',tier2_n:500},rank:{final_n:30}};
test('researcher design plan includes the final single-engine candidate cap',()=>{const {c}=fixture();assert.match(c.noviceModelPlan(design,'design',0),/500모델/);});
test('WT-off mature plans eight representative models, WT-on plans twelve',()=>{const{c}=fixture();const cfg={mature:{library:{max_variants:3},prefilter:{top_n:2}},screen:{comparison:{enabled:true,engines:starter.engines}},rank:{include_wt_control:false}};assert.match(c.noviceModelPlan(cfg,'mature',0),/8모델/);cfg.rank.include_wt_control=true;assert.match(c.noviceModelPlan(cfg,'mature',0),/12모델/);});
test('reanalyze restores the saved Seed and predictor rather than previous controls',()=>{const{c,get,actions}=fixture();c.state.result={config:{...structuredClone(design),run:{mode:'design',seed:99,mock:true}},mode:'design',name:'opaque'};const start=source.indexOf('  action("reanalyze", () => {');vm.runInContext(source.slice(start,source.indexOf('  action("compare-runs"',start)),c);actions.get('reanalyze')();const cfg=c.inputSettings(false);assert.equal(cfg.run.seed,99);assert.equal(cfg.screen.engine,'boltz2');assert.equal(get('mock').checked,true);});
test('small budgets, unique sequence uncertainty, per-generator caps and engine conditions stay separate',()=>{const{c}=fixture();const small=c.inputSettings(true),before=JSON.stringify(small),p=c.analysisQuantityPlan(small,'design',null);assert.equal(p.modelUpper,8);assert.equal(p.uniqueSequences,null);assert.equal(p.generators.length,2);assert.equal(JSON.stringify(small),before);small.design.num_designs=1000;small.design.filters.top_n_to_score=500;small.screen.tier2_n=500;assert.equal(c.analysisQuantityPlan(small,'design',null).modelUpper,6000);small.score={boltz2:{seed:99}};small.screen.colabfold.num_seeds=3;const q=c.analysisQuantityPlan(small,'design',null);assert.equal(q.conditions.boltz2.seed,99);assert.equal(q.conditions.colabfold.seeds,3);});
test('count sources and invalid previews never display a stale effective number',()=>{const{c,get}=fixture();assert.equal(c.quantitySource('design-count','design.num_designs'),'작은 시작 설정');get('advanced').value='{"design":{"num_designs":7}}';assert.equal(c.quantitySource('design-count','design.num_designs'),'가져온 설정·고급 설정');get('design-count').value='9';assert.equal(c.quantitySource('design-count','design.num_designs'),'직접 입력');c.clearScalePreview();assert.ok(!/\d/.test(get('design-count').placeholder));});
function ready(f,cfg){f.c.state.input={id:'opaque-review',config:cfg};f.c.state.readiness=true;}
test('random native library budgets ignore unused top_n and stale DMS limits',()=>{
 const {c}=fixture();const cfg={run:{mode:'mature',mock:false},mature:{library:{strategy:'random',size:101,max_variants:3},prefilter:{mode:'none',top_n:2}},screen:{engine:'boltz2'},rank:{include_wt_control:true}};
 const before=JSON.stringify(cfg);assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,101);
 cfg.rank.include_wt_control=false;assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,100);
 cfg.screen.comparison={enabled:true,engines:['boltz2','chai1']};assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,200);
 cfg.run.mock=true;assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,4);
 const original=JSON.parse(before);assert.match(c.noviceModelPlan(original,'mature'),/101모델/);
 assert.doesNotMatch(c.I18N.value(c.I18N.term(c.noviceModelPlan(original,'mature'))),/[가-힣]/);
});
test('unfiltered unbounded DMS never advertises a prefilter cap as its compute budget',()=>{
 const {c}=fixture();const cfg={mature:{library:{strategy:'dms'},prefilter:{mode:'none',top_n:2}},rank:{include_wt_control:true}};
 assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,null);
 cfg.mature.library.max_variants=3;assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,4);
});
test('native mutation controls turn 100 variants into 101 sequences and preserve quality settings',()=>{
 const {c,get}=fixture();get('mode').value='mature';get('analysis-preset').value='standard';get('screen-engine').value='boltz2';
 get('advanced').value=JSON.stringify({score:{thresholds:{ipsae:0.6}},mature:{library:{strategy:'dms',max_variants:3}}});
 get('mutation-strategy').value='random';get('mutation-count').value='100';get('mutation-min').value='1';get('mutation-max').value='3';get('candidate-limit').value='2';
 const cfg=c.inputSettings(false);assert.equal(cfg.mature.library.size,101);assert.equal(cfg.mature.library.min_mutations,1);assert.equal(cfg.mature.library.max_mutations,3);
 assert.equal(cfg.mature.prefilter.mode,'none');assert.equal(cfg.mature.antifold.enabled,false);assert.equal(cfg.rank.include_wt_control,true);assert.equal(cfg.score.thresholds.ipsae,0.6);
 assert.equal(c.analysisQuantityPlan(cfg,'mature').modelUpper,101);
 get('mutation-wt').value='exclude';assert.equal(c.analysisQuantityPlan(c.inputSettings(false),'mature').modelUpper,100);
 get('mutation-min').value='4';assert.throws(()=>c.inputSettings(false),/최대/);
});
test('switching from DMS to random starts at 100 variants in both preview and request',()=>{
 const {c,get}=fixture();get('mode').value='mature';get('analysis-preset').value='standard';
 c.state.planningDefaults.mature={library:{strategy:'dms',size:10000}};
 get('mutation-strategy').value='random';
 assert.equal(c.inputSettings(true).mature.library.size,101);
 assert.equal(c.inputSettings(false).mature.library.size,101);
});
test('imported mutation configuration clears old controls and retains counts and WT choice',()=>{
 const {c,get}=fixture();get('mutation-strategy').value='random';get('mutation-count').value='100';get('mutation-max').value='3';
 const cfg={run:{mode:'mature',seed:99},screen:{engine:'boltz2'},mature:{library:{strategy:'random',size:8,min_mutations:1,max_mutations:2},prefilter:{mode:'none',top_n:2}},rank:{include_wt_control:false}};
 c.applyAnalysisSettings(cfg);assert.equal(get('mutation-count').value,'');const applied=c.inputSettings(false);
 assert.equal(applied.mature.library.size,8);assert.equal(applied.mature.library.max_mutations,2);assert.equal(applied.rank.include_wt_control,false);
 assert.equal(c.analysisQuantityPlan(applied,'mature').modelUpper,7);
});
test('all visible mutation controls have English labels and validation messages',()=>{
 const {c}=fixture();for(const key of ['변이 생성 설정','변이 생성 방식','현재 설정 유지','생성할 변이 후보 수 · WT 제외','후보당 최소 치환 잔기 수','후보당 최대 치환 잔기 수','WT 대조군 평가','포함','제외','CDR 단일 잔기 치환 (DMS)','CDR 무작위 다중 잔기 치환','변이 수와 치환 수는 허용 범위의 정수로 입력하세요.','WT 대조군 포함 여부를 확인하세요.'])
  assert.doesNotMatch(c.I18N.value(c.I18N.term(key)),/[가-힣]/,key);
});
test('large confirmation cancellation submits zero; double click submits one',async()=>{const f=fixture();ready(f,design);f.c.window.confirm=()=>false;await f.c.submitReviewedAnalysis();assert.equal(f.writes.length,0);let approve;f.c.window.confirm=()=>new Promise(resolve=>{approve=resolve;});const a=f.c.submitReviewedAnalysis(),b=f.c.submitReviewedAnalysis();assert.equal(f.writes.length,0);approve(true);await Promise.all([a,b]);assert.equal(f.writes.length,1);assert.equal(f.writes[0].path,'/api/jobs');});
test('changing the reviewed input while confirmation is open invalidates approval',async()=>{const f=fixture();ready(f,design);let approve;f.c.window.confirm=()=>new Promise(resolve=>{approve=resolve;});const pending=f.c.submitReviewedAnalysis();f.c.state.inputRevision++;approve(true);await assert.rejects(pending,/변경/);assert.equal(f.writes.length,0);});
test('small eight-model request needs no large-run confirmation; unknown is never zero',async()=>{const f=fixture();ready(f,f.c.inputSettings(true));f.c.window.confirm=()=>{throw Error('unexpected confirmation');};await f.c.submitReviewedAnalysis();assert.equal(f.writes.length,1);assert.equal(f.c.analysisQuantityPlan({},'design',null).modelUpper,null);});
test('import, clear, and Seed edits preserve nonuniform engine settings and counts',()=>{const{c,get}=fixture();const cfg=c.inputSettings(true);cfg.run.seed=7;cfg.score={boltz2:{seed:99}};cfg.screen.colabfold.num_seeds=3;cfg.screen.colabfold.num_models=2;const original=JSON.stringify(cfg);c.applyAnalysisSettings(cfg);get('seed').value='11';let applied=c.inputSettings(false);assert.equal(applied.run.seed,11);assert.equal(applied.score.boltz2.seed,99);assert.equal(applied.screen.colabfold.num_models,2);assert.equal(c.analysisQuantityPlan(applied,'design',null).modelUpper,8);get('seed').value='';applied=c.inputSettings(false);assert.equal(applied.run.seed,7);assert.equal(JSON.stringify(cfg),original);});
test('YAML import handler replaces old count and seed controls atomically',async()=>{const{c,get}=fixture();get('design-count').value='1000';get('candidate-limit').value='500';const cfg=structuredClone(design);cfg.run={mode:'design',seed:99,mock:false};get('yaml-file').files=[{text:async()=> 'opaque-config'}];c.api=async path=>{assert.equal(path,'/api/configs/parse');return{config:cfg};};const start=source.indexOf('  $("yaml-file").onchange = async () => {');vm.runInContext(source.slice(start,source.indexOf('  action("load-structure"',start)),c);await get('yaml-file').onchange();assert.equal(get('design-count').value,'');assert.equal(c.inputSettings(false).run.seed,99);assert.equal(c.inputSettings(false).design.num_designs,1000);});
test('a half-entered number cannot silently become a default request',()=>{const{c,get}=fixture();get('design-count').validity={badInput:true};assert.throws(()=>c.inputSettings(false),/整数|정수/);get('design-count').validity.badInput=false;get('seed').validity={badInput:true};assert.throws(()=>c.inputSettings(false),/Seed/);});
test('complex small comparison uses four engines while inactive VHH paths stay inactive',()=>{const{c,get}=fixture();get('mode').value='complex';const cfg=c.inputSettings(true);cfg.design.refinement.enabled=true;assert.match(c.noviceModelPlan(cfg,'complex',null),/계획 모델 4개/);});
test('custom complex seed lists and sample conditions remain explicit',()=>{const{c}=fixture();const cfg={run:{seed:42},complex:{seeds:[7,9],models_per_seed:2,comparison_engines:['boltz2']}};const saved=JSON.stringify(cfg),p=c.analysisQuantityPlan(cfg,'complex');assert.equal(p.modelUpper,4);assert.deepEqual(Array.from(p.conditions.boltz2.seeds),[7,9]);assert.equal(p.conditions.boltz2.modelsPerSeed,2);assert.equal(JSON.stringify(cfg),saved);});
test('draft restoration keeps counts and marks the restored seed source',()=>{const{c,get}=fixture();c.draft={fields:{mode:'design','analysis-preset':'standard',advanced:'{"design":{"engine":"rfantibody","num_designs":7}}',seed:'99','design-count':'','candidate-limit':''}};c.host={contains:()=>true};c.restoring=false;const begin=source.indexOf('restoring=true;for(const [id,value]of Object.entries(draft.fields||{}))');vm.runInContext(source.slice(begin,source.indexOf('if(draft.fields?.comparison_engines)',begin)),c);assert.equal(c.inputSettings(false).run.seed,99);assert.equal(c.inputSettings(false).design.num_designs,7);assert.equal(c.quantitySource('seed','run.seed'),'복원한 입력');get('seed').value='';c.state.planningDefaults.run={seed:83};assert.equal(c.inputSettings(false).run.seed,83);assert.equal(c.inputSettings(true).run.seed,83);});
test('bilingual quantity labels and confirmation text are translated without losing numbers',()=>{const{c}=fixture();const ko=c.noviceModelPlan(design,'design',0);const en=c.I18N.value(c.I18N.term(ko));assert.match(en,/500 models/);assert.ok(!/[가-힣]/.test(en));assert.match(c.I18N.value(c.I18N.format('적용값: {0} · 출처: {1}',[1,c.I18N.term('작은 시작 설정')])),/Effective value: 1/);});

test('lost analysis receipt finds the exact accepted job with GET and never resubmits', async () => {
 const {c}=fixture();
 ready({c}, design);
 const calls=[],selected=[];
 c.setTimeout=fn=>fn();
 c.selectJob=async id=>selected.push(id);
 c.api=async (path, body)=>{
   calls.push({path, body});
   if(body) throw Error('lost');
   const unrelated={kind:'analysis',name:'opaque',id:'other',submission_id:'different'};
   return {active_jobs:[unrelated],jobs:calls.length===2?[]:[{...unrelated,id:'accepted',submission_id:c.state.submissionId}]};
 };
 await c.submitReviewedAnalysis();
 assert.deepEqual(selected,['accepted']);
 assert.equal(calls.filter(x=>x.body).length,1);
 assert.equal(calls.length,3);
});
test('missing or rejected analysis receipts never open a different job', async () => {
 for(const rejected of [false,true]) {
   const {c}=fixture();ready({c},design);c.setTimeout=fn=>fn();
   let posts=0,reads=0;c.selectJob=async ()=>assert.fail('opened an unrelated job');
   c.api=async (path,body)=>{
     if(body){posts++;const err=Error('submission not confirmed');err.responseRejected=rejected;throw err;}
     reads++;return {jobs:[{kind:'analysis',name:'opaque',id:'older',submission_id:'old'}]};
   };
   await assert.rejects(c.submitReviewedAnalysis(),/submission not confirmed/);
   assert.equal(posts,1);assert.equal(reads,rejected?0:8);
 }
});
test('a late accepted job leaves later navigation intact',async()=>{
 const {c}=fixture();ready({c},design);
 c.api=async()=>{c.state.navigationRequest=4;return{id:'saved'};};
 c.view=()=>assert.fail('overrode later navigation');
 c.selectJob=()=>assert.fail('overrode selected job');
 let resets=0;c.state.nextAnalysis=()=>resets++;
 await c.submitReviewedAnalysis();
 assert.equal(resets,1);
});
test('a late acceptance preserves edits to the next analysis',async()=>{
 const {c}=fixture();ready({c},design);
 c.api=async()=>{c.state.inputRevision++;return{id:'previous'};};
 c.state.nextAnalysis=()=>assert.fail('cleared a new draft');
 c.view=()=>assert.fail('overrode the new form');
 await c.submitReviewedAnalysis();
});
