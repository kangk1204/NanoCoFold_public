import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const root=new URL('../',import.meta.url);
const source=readFileSync(new URL('nanocofold/templates/platform.js',root),'utf8');
const catalog=readFileSync(new URL('nanocofold/templates/platform-i18n.js',root),'utf8');
const starter=JSON.parse(readFileSync(new URL('nanocofold/_data/starter-presets.json',root)));
function fixture(){
 const nodes=new Map(),labels=new Map();
 function get(id){if(!nodes.has(id))nodes.set(id,{value:'',valueAsNumber:NaN,checked:false,hidden:false,textContent:'',isConnected:true,replaceChildren(...children){this.children=children;},setAttribute(key,value){this[key]=value;},getAttribute(key){return this[key]??null;}});return nodes.get(id);}
 get('analysis-preset').value='small-four-v1';get('mode').value='design';get('advanced').value='{}';get('seed').value='42';get('screen-engine').value='chai1';get('novice-msa').value='cache';
 const boxes=starter.engines.map(value=>({value,checked:true}));
 const context={structuredClone,window:{},state:{starter,presets:{standard:{},researcher_exploration:{design:{num_designs:1000,filters:{top_n_to_score:1000}},screen:{tier2_n:500}}},planningDefaults:{design:{engine:'rfantibody',num_designs:1000,mpnn:{seqs_per_struct:4},boltzgen:{inverse_fold_num_sequences:1},filters:{top_n_to_score:300}},screen:{engine:'chai1',tier2_n:50},rank:{final_n:30}}},$:get,
 CustomEvent:class{constructor(type){this.type=type;}},document:{documentElement:{},getElementById:()=>null,dispatchEvent(){},querySelectorAll:()=>boxes.filter(b=>b.checked),querySelector:s=>{if(!labels.has(s))labels.set(s,{hidden:false});return labels.get(s);}}};
 context.el=(_tag,text)=>({textContent:text});
 const document=context.document;delete context.document;vm.createContext(context);vm.runInContext(catalog,context);context.document=document;
 vm.runInContext(source.slice(source.indexOf('function merge('),source.indexOf('function fields()')),context);
 return {c:context,get,boxes,labels};
}
test('small preset displays effective counts rather than unrelated default 500/1000',()=>{
 const {c,get,labels}=fixture();const before=JSON.stringify(c.state);
 const cfg=c.inputSettings(true);c.renderAnalysisScale(cfg,'design');
 assert.match(c.I18N.value(c.I18N.term(c.noviceModelPlan(cfg,'design',0))),/up to 8 models/);
 assert.match(get('design-count').placeholder,/Current setting: 1/);
 assert.match(get('candidate-limit').placeholder,/Current setting: 2/);
 assert.match(get('selection-limit').placeholder,/Current setting: 8/);
 assert.equal(get('screen-engine').hidden,true);
 assert.equal(labels.get('label[for="screen-engine"]').hidden,true);
 assert.equal(JSON.stringify(c.state),before);assert.equal(get('design-count').value,'');
 c.I18N.setLanguage('ko',{persist:false});assert.match(get('design-count').placeholder,/현재 설정 1/);
 c.I18N.setLanguage('en',{persist:false});assert.match(get('design-count').placeholder,/Current setting: 1/);
});
test('explicit count changes update the plan, submit only overrides and never rewrite presets',()=>{
 const {c,get}=fixture();const before=JSON.stringify(c.state);
 get('design-count').value='3';get('design-count').valueAsNumber=3;
 get('candidate-limit').value='2';get('candidate-limit').valueAsNumber=2;
 get('selection-limit').value='5';get('selection-limit').valueAsNumber=5;
 const cfg=c.inputSettings(false);
 assert.equal(cfg.design.num_designs,3);assert.equal(cfg.rank.final_n,5);
 // RF contributes at most two; BoltzGen contributes three. The UI is not an eight-model cap.
 assert.match(c.noviceModelPlan(cfg,'design',0),/최대 20모델/);
 assert.equal(cfg.design.boltzgen,undefined,'Display defaults must not become submitted overrides');
 assert.equal(JSON.stringify(c.state),before);
});
test('workspace defaults and imported overrides determine standard hints without an invented count',()=>{
 const {c,get}=fixture();get('analysis-preset').value='standard';
 get('advanced').value='{"design":{"num_designs":7},"rank":{"final_n":4}}';
 const cfg=c.inputSettings(true);c.renderAnalysisScale(cfg,'design');
 assert.match(get('design-count').placeholder,/Current setting: 7/);
 assert.match(get('candidate-limit').placeholder,/Current setting: 300/);
 assert.match(get('selection-limit').placeholder,/Current setting: 4/);
 assert.equal(get('screen-engine').hidden,false);
 const submitted=c.inputSettings(false);assert.equal(submitted.design.mpnn,undefined);
 c.state.planningDefaults={};get('advanced').value='{}';c.renderAnalysisScale(c.inputSettings(true),'design');
 assert.match(get('design-count').placeholder,/Check during input review/);
});
test('invalid selection bounds fail before submitting and complex mode ignores VHH selection',()=>{
 const {c,get}=fixture();get('selection-limit').value='0';get('selection-limit').valueAsNumber=0;
 assert.throws(()=>c.inputSettings(false),/최종 선별 상한/);
 for(const mode of ['complex','screen']){get('mode').value=mode;assert.doesNotThrow(()=>c.inputSettings(false));}
 get('selection-limit').value='5';get('selection-limit').valueAsNumber=5;
 assert.equal(c.inputSettings(false).rank.final_n,starter.modes.screen.native_override.rank.final_n,'Hidden design/mature controls must not change screening selections');
});
test('screen and complex keep their own candidate semantics and do not show VHH scale controls',()=>{
 const {c,get}=fixture();for(const mode of ['screen','complex']){get('mode').value=mode;c.renderAnalysisScale(c.inputSettings(true),mode);assert.equal(get('analysis-scale').hidden,true);}
 assert.match(c.noviceModelPlan({screen:{comparison:{enabled:true,engines:['boltz2','chai1']}}},'screen',1001),/1001개.*2002모델/);
});

test('unfiltered random libraries do not restore the unused prefilter cap caption',()=>{
 const {c,get}=fixture();get('mode').value='mature';
 const cfg={run:{mode:'mature',mock:false},mature:{library:{strategy:'random',size:101},prefilter:{mode:'none',top_n:2}},screen:{engine:'boltz2'},rank:{include_wt_control:true}};
 c.renderAnalysisScale(cfg,'mature');
 for(const id of ['candidate-limit','candidate-limit-help','candidate-limit-current','design-count-current'])assert.equal(get(id).hidden,true,id);
 assert.match(c.noviceModelPlan(cfg,'mature'),/101모델/);
 cfg.run.mock=true;c.renderAnalysisScale(cfg,'mature');assert.equal(get('candidate-limit-help').hidden,false);
});
