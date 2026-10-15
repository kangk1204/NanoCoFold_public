import {koreanContext} from './i18n_context.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function noviceSettingsFixture() {
  const fields={'analysis-preset':{value:'legacy'},seed:{value:'42'},'screen-engine':{value:'boltz2'},'novice-consent':{checked:false}};
  const context={$:id=>fields[id]};koreanContext(context);
  vm.runInContext(source.slice(source.indexOf('function noviceComplexSettings('),source.indexOf('function fields()')),context);
  return {context,fields};
}
test('restored complex comparison keeps its own MSA source, seeds and selected engines',()=>{
  const {context:c}=noviceSettingsFixture();
  const settings=c.noviceComplexSettings({complex:{comparison_engines:['colabfold','boltz2'],msa_source:'local_mmseqs2',seeds:[7,9],models_per_seed:2,recycles:6}});
  assert.equal(settings.msa_source,'local_mmseqs2');assert.deepEqual(settings.seeds,[7,9]);
  assert.equal(settings.recycles,6);assert.equal(settings.models_per_seed,2);assert.equal('engine' in settings,false);
  assert.throws(()=>c.noviceComplexSettings({complex:{comparison_engines:['colabfold']}}),/MSA 준비 방식/);
});
test('new complex comparison uses the visible selection and only fresh input consent',()=>{
  const {context:c,fields}=noviceSettingsFixture();fields['analysis-preset'].value='small-four-v1';
  const cfg={complex:{comparison_engines:['opendde'],allow_public_msa_server:true},screen:{comparison:{engines:['colabfold','boltz2']},colabfold:{msa_source:'public_api'}}};
  let settings=c.noviceComplexSettings(cfg);assert.deepEqual(settings.comparison_engines,['colabfold','boltz2']);
  assert.equal(settings.allow_public_msa_server,false);fields['novice-consent'].checked=true;
  assert.equal(c.noviceComplexSettings(cfg).allow_public_msa_server,true);
});
test('model plan follows custom candidate counts and never drops a screen library',()=>{
  const {context:c}=noviceSettingsFixture(),screen={comparison:{enabled:true,engines:['colabfold','boltz2','chai1','opendde']}};
  assert.match(c.noviceModelPlan({screen,design:{engine:'both',num_designs:3,mpnn:{seqs_per_struct:1}}},'design',0),/최대 24모델/);
  assert.match(c.noviceModelPlan({screen},'screen',101),/101개.*404모델/);
  assert.match(c.noviceModelPlan({screen,mature:{library:{max_variants:3},prefilter:{top_n:9}}},'mature',0),/최대 16모델/);
  assert.match(c.noviceModelPlan({complex:{proteins:[],comparison_engines:['boltz2','opendde'],seeds:[42,7],models_per_seed:2}},'complex',0),/계획 모델 8개/);
});
function node() {
  return {value:'',checked:false,hidden:false,disabled:false,open:true,textContent:'',dataset:{},children:[],
    label:{hidden:false},closest(){return this.label;},focus(){this.focused=true;},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
    addEventListener(){},setAttribute(){},removeAttribute(){}};
}
function tutorialFixture() {
  const elements=new Map(),fields=['name','screen','mature','complex'].map(key=>Object.assign(node(),{dataset:{batchField:key}}));
  const calls=[],context={$:id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);},
    document:{querySelectorAll:()=>fields,hidden:false},setInterval:()=>1,clearInterval(){},
    notice:(...args)=>calls.push({notice:args}),view:name=>calls.push({view:name}),
    el:()=>node(),action(){},setTimeout,console};
  context.api=async route=>{
    calls.push({route}); assert.match(route,/^\/api\/tutorials\/(complex|screen|mature|design)$/);
    const mode=route.split('/').at(-1);
    return {mode,title:mode,description:'public input',requires_api_consent:mode!=='complex',
      payload:JSON.parse(readFileSync(new URL('../examples/tutorial/'+mode+'_batch.json',import.meta.url),'utf8'))};
  };
  koreanContext(context);
  vm.runInContext(source.slice(source.indexOf('let activeBatch ='),source.indexOf('\nasync function boot()')),context);
  return {context,elements,fields,calls};
}
for(const mode of ['complex','screen','mature','design']) {
  test('public '+mode+' loads exact settings, revokes consent and never submits',async()=>{
    const f=tutorialFixture(),c=f.context;
    c.$('batch-api-consent').checked=true;c.$('batch-use-config').checked=true;
    for(const id of ['batch-library','batch-wt','batch-partners'])c.$(id).value='old input';
    await c.loadTutorial(mode);
    const expected=JSON.parse(readFileSync(new URL('../examples/tutorial/'+mode+'_batch.json',import.meta.url),'utf8'));
    assert.deepEqual(JSON.parse(c.$('batch-content').value),expected);
    assert.equal(c.$('batch-mode').value,mode);assert.equal(c.$('batch-format').value,'json');
    assert.equal(c.$('batch-api-consent').checked,false);assert.equal(c.$('batch-use-config').checked,false);
    assert.equal(c.$('batch-submit').disabled,true);assert.equal(c.$('batch-content-details').open,false);
    assert.ok(f.fields.every(field=>field.hidden));assert.equal(c.$('batch-tutorial-summary').hidden,false);
    assert.ok(c.$(mode==='complex'?'batch-review':'batch-api-consent').focused);
    assert.deepEqual(f.calls.filter(call=>call.route),[{route:'/api/tutorials/'+mode}]);
  });
}
test('tutorial read failure preserves existing inputs and exposes repairable content',async()=>{
  const f=tutorialFixture(),c=f.context;c.$('batch-content').value='original';
  c.api=async()=>{throw Error('read failed');};await c.loadTutorial('complex');
  assert.equal(c.$('batch-content').value,'original');assert.equal(c.$('batch-content-details').open,true);
  assert.ok(c.$('batch-content').focused);assert.match(c.$('batch-activity').textContent,/read failed/);
  assert.equal(c.$('load-tutorial').disabled,false);
});
test('stale tutorial response cannot replace newly edited input',async()=>{
  const f=tutorialFixture(),c=f.context;let finish;
  c.api=()=>new Promise(resolve=>{finish=resolve;});const work=c.loadTutorial('complex');
  c.$('batch-content').value='new input';c.resetBatchReview();
  finish({mode:'complex',payload:{name:'old'},title:'old'});await work;
  assert.equal(c.$('batch-content').value,'new input');
});
test('only the active FASTA mode asks for its common input',()=>{
  const f=tutorialFixture(),c=f.context;c.$('batch-format').value='fasta';
  for(const mode of ['screen','mature','complex','design']) {
    c.$('batch-mode').value=mode;c.syncBatchFields();
    assert.deepEqual(f.fields.filter(field=>!field.hidden).map(field=>field.dataset.batchField),
      mode==='design'?['name']:['name',mode]);
  }
});
test('submitted batches cannot be submitted again through the displayed button',()=>{
  const f=tutorialFixture(),c=f.context;
  c.renderBatch({id:'review',name:'example',selected:null,rows:[{id:'one'}]});
  assert.equal(c.$('batch-submit').disabled,false);assert.match(c.$('batch-next-step').textContent,/미제출/);
  c.renderBatch({id:'review',name:'example',selected:['one'],rows:[{id:'one',job:{state:'running'}}]});
  assert.equal(c.$('batch-submit').disabled,true);assert.match(c.$('batch-next-step').textContent,/계산을 제출/);
  c.$('batch-api-consent').checked=true;c.resetBatchReview(false);
  assert.equal(c.$('batch-api-consent').checked,true);assert.equal(c.$('batch-submit').disabled,true);
});
test('explicit consent retains the loaded example summary while invalidating review',async()=>{
  const f=tutorialFixture(),c=f.context;await c.loadTutorial('screen');
  const summary=c.$('batch-tutorial-summary').textContent;
  c.$('batch-api-consent').checked=true;c.resetBatchReview(false);
  assert.equal(c.$('batch-tutorial-summary').hidden,false);
  assert.equal(c.$('batch-tutorial-summary').textContent,summary);
  assert.equal(c.$('batch-submit').disabled,true);
});

const adapter=readFileSync(new URL('../nanocofold/templates/result-molstar.js',import.meta.url),'utf8');
function viewerFixture() {
  const calls=[];let resize;
  const cameraState={position:[0,0,20],target:[0,0,0],up:[0,1,0]};
  const pose=()=>JSON.parse(JSON.stringify(cameraState));
  const camera={
    focusLoci:(...args)=>{calls.push({focus:args});cameraState.position=[0,0,10];},
    reset:(...args)=>{calls.push({reset:args});cameraState.position=[0,0,20];},
  };
  const context={window:{},molstar:{},ResizeObserver:class{constructor(cb){resize=cb;}observe(){}},
    requestAnimationFrame:()=>{throw Error('A layout resize must not schedule automatic refocus');},cancelAnimationFrame(){}};
  koreanContext(context);vm.runInContext(adapter,context);
  const plugin={managers:{camera,interactivity:{lociSelects:{deselectAll(){},select(){}},lociHighlights:{clearHighlights(){}}},structure:{focus:{clear(){}}}},
    canvas3d:{camera:{viewport:{width:200,height:100},getSnapshot:pose}}};
  const viewer=new context.window.NanoCoFoldResultViewer({plugin,handleResize(){calls.push({resize:true});}},{chains:[]});
  viewer.loci=labels=>Array.from(labels);
  const host={clientWidth:200,clientHeight:100};viewer.observeResize(host);
  return {viewer,calls,pose,host,resize:()=>resize(),manualCamera(){cameraState.position=[13,7,31];cameraState.target=[1,2,3];cameraState.up=[0.3,0.8,0.1];return pose();}};
}
test('whole-structure view preserves manual rotation and zoom after resize without clearing selection',async()=>{
  const f=viewerFixture();await f.viewer.focus(['A:1']);await f.viewer.fit();const manual=f.manualCamera();f.resize();
  assert.equal(f.calls.filter(call=>call.focus).length,1);
  assert.equal(f.calls.filter(call=>call.reset).length,1);
  assert.equal(f.calls.filter(call=>call.resize).length,1);
  assert.deepEqual(f.pose(),manual);assert.deepEqual(Array.from(f.viewer.selectedLabels),['A:1']);
});
test('clearing highlight does not change manual camera framing on resize',async()=>{
  const f=viewerFixture();await f.viewer.focus(['B:1']);await f.viewer.clearSelection();const manual=f.manualCamera();f.resize();
  assert.equal(f.calls.filter(call=>call.focus).length,1);assert.equal(f.viewer.selectedLabels.length,0);
  assert.equal(f.calls.filter(call=>call.resize).length,1);assert.deepEqual(f.pose(),manual);
});
test('a pending focus followed by explicit fit finishes whole, then resize preserves the current camera',async()=>{
  const f=viewerFixture(),focus=f.viewer.focus(['A:1']),fit=f.viewer.fit();await Promise.all([focus,fit]);
  assert.ok(f.calls.at(-1).reset);const before=f.pose();f.resize();
  assert.deepEqual(f.pose(),before);assert.equal(f.viewer.cameraFocusLabels,null);
  assert.equal(f.calls.filter(call=>call.reset).length,1);assert.equal(f.calls.filter(call=>call.resize).length,1);
});
test('hidden or disposed viewer resize does no camera or drawing work, while restoring size resizes once',()=>{
  const f=viewerFixture(),manual=f.manualCamera();f.host.clientWidth=0;f.resize();
  assert.equal(f.calls.length,0);assert.deepEqual(f.pose(),manual);
  f.host.clientWidth=200;f.host.clientHeight=0;f.resize();assert.equal(f.calls.length,0);
  f.host.clientHeight=100;f.resize();assert.deepEqual(f.pose(),manual);assert.equal(f.calls.filter(c=>c.resize).length,1);
  f.viewer.disposed=true;f.resize();assert.equal(f.calls.filter(c=>c.resize).length,1);
});
