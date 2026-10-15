import {koreanContext} from './i18n_context.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const functions=source.slice(source.indexOf('async function focusStructure('),source.indexOf('async function renderStructure('));
const measure=source.slice(source.indexOf('  action("measure-result-distance",'),source.indexOf('  action("export-result",'));
function fixture() {
  let finish;
  const calls=[],notes={textContent:'current candidate note'};
  const viewer={focus:()=>new Promise(resolve=>{finish=resolve;}),distance:(...args)=>calls.push({viewer:'original',args})};
  const replacement={distance:(...args)=>calls.push({viewer:'replacement',args})};
  const structure={chains:[{residues:[{label:'X:1',ca:[0,0,0]},{label:'Y:2',ca:[0,0,3]}]}]};
  const context={state:{viewer,structure},el:()=>({}),notice:(...args)=>calls.push({notice:args}),
    $:id=>id==='residue-a'?{value:'X:1'}:id==='residue-b'?{value:'Y:2'}:notes,
    action:(name,callback)=>{context.measureDistance=callback;}};
  koreanContext(context);vm.runInContext(functions+measure,context);
  return {context,calls,notes,replacement,finish:()=>finish()};
}
for(const surface of ['pair button','CA distance']) {
  for(const change of ['none','candidate','structure','disposed']) {
    test(surface+' retains the owning viewer after an awaited focus: '+change,async()=>{
      const f=fixture(),c=f.context;
      const work=surface==='pair button'
        ?c.focusButton('atoms',['X:1','Y:2'],'original note',['X:1','Y:2','CA','CA']).onclick()
        :c.measureDistance();
      if(change==='candidate') {c.state.viewer=f.replacement;c.state.structure={};}
      if(change==='structure') c.state.structure={};
      if(change==='disposed') c.state.viewer=null;
      f.finish();await work;
      if(change==='none') {
        assert.equal(f.calls.length,1);
        assert.equal(f.calls[0].viewer,'original');
        assert.deepEqual(f.calls[0].args,surface==='pair button'?['X:1','Y:2','CA','CA']:['X:1','Y:2']);
      } else {
        assert.deepEqual(f.calls,[]);
        assert.equal(f.notes.textContent,'current candidate note');
      }
    });
  }
}
