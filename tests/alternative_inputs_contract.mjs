/** Execute the shipped input controls with local file fixtures; no server calls. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
function fixture(){
 const nodes=new Map(),events=[];
 const c={$:id=>{if(!nodes.has(id))nodes.set(id,{value:'',files:[],hidden:false,disabled:false,checked:false,listeners:{},addEventListener(kind,fn){this.listeners[kind]=fn;},focus(){this.focused=true;},dispatchEvent(event){events.push(event);c.state.inputRevision++;c.$('novice-consent').checked=false;this.listeners[event.type]?.(event);}});return nodes.get(id);},state:{inputRevision:0},clearInputStructure(){c.state.inputRevision++;},notice(){},Event:class{constructor(type,options){this.type=type;this.bubbles=options.bubbles;}}};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('let structureInputSource ='),source.indexOf('function invalidateInput()')),c);c.initAlternativeInputs();return {c,events};
}
test('a structure file disables PDB input and switching retains both raw drafts',()=>{
 const {c}=fixture(),file={name:'input.pdb'};c.$('structure-file').files=[file];c.$('pdb-id').value='2FLU';c.$('novice-consent').checked=true;
 c.$('structure-file').listeners.change();assert.equal(c.$('pdb-id').disabled,true);assert.equal(c.$('structure-pdb-option').hidden,true);
 c.$('change-structure-input').onclick();assert.equal(c.$('structure-file').disabled,true);assert.equal(c.$('pdb-id').disabled,false);assert.equal(c.$('pdb-id').value,'2FLU');assert.equal(c.$('structure-file').files[0],file);assert.equal(c.$('novice-consent').checked,false);
 c.$('change-structure-input').onclick();assert.equal(c.$('structure-file').disabled,false);assert.equal(c.$('pdb-id').value,'2FLU');
});
test('clearing the PDB draft makes the file route available again',()=>{
 const {c}=fixture();c.$('pdb-id').value='2FLU';c.$('pdb-id').listeners.input();assert.equal(c.$('structure-file').disabled,true);
 c.$('pdb-id').value='';c.$('pdb-id').listeners.input();assert.equal(c.$('structure-file').disabled,false);assert.equal(c.$('structure-file-option').hidden,false);
});
test('FASTA file import updates editable canonical text and can restore the previous draft',async()=>{
 const {c,events}=fixture();c.$('vhh-fasta').value='>old\nACDE';c.syncAlternativeInputs();assert.equal(c.$('vhh-file').disabled,true);assert.equal(c.$('vhh-fasta').disabled,false);
 c.$('vhh-file-change').onclick();assert.equal(c.$('vhh-file').disabled,false);c.$('vhh-file').files=[{text:async()=>'>new\nFGHI'}];
 await c.importCanonicalFasta('vhh-file','vhh-fasta');assert.equal(c.$('vhh-fasta').value,'>new\nFGHI');assert.equal(c.$('vhh-file').hidden,true);assert.equal(events.at(-1).bubbles,true);
 c.$('vhh-file-restore').onclick();assert.equal(c.$('vhh-fasta').value,'>old\nACDE');assert.equal(c.$('wt-fasta').value,'');
});
test('a late file read cannot replace newly edited canonical FASTA',async()=>{
 const {c}=fixture();let done;c.$('wt-fasta').value='>old\nACDE';c.$('wt-file').files=[{text:()=>new Promise(resolve=>done=resolve)}];
 const pending=c.importCanonicalFasta('wt-file','wt-fasta');c.$('wt-fasta').value='>newer\nFGHI';c.state.inputRevision++;done('>late\nLMNP');
 await assert.rejects(pending,/현재 FASTA/);assert.equal(c.$('wt-fasta').value,'>newer\nFGHI');
});
test('VHH and WT replacement reset the picker so the same file can reload without losing edited FASTA',async()=>{
 for(const [file,text] of [['vhh-file','vhh-fasta'],['wt-file','wt-fasta']]){
  const {c}=fixture(),content='>from_file\nACDE';
  c.$(file).files=[{text:async()=>content}];await c.importCanonicalFasta(file,text);
  c.$(text).value='>prior_draft\nACDE';c.$(file).value='C:\\fakepath\\library.fa';c.syncAlternativeInputs();
  c.$(file+'-change').onclick();assert.equal(c.$(file).value,'','A new chooser selection of the same file must emit change');assert.equal(c.$(text).value,'>prior_draft\nACDE','Opening the picker preserves the canonical edit');assert.equal(c.$(file).disabled,false);
  c.$(file).files=[{text:async()=>content}];await c.$(file).onchange();assert.equal(c.$(text).value,content);
  c.$(file+'-restore').onclick();assert.equal(c.$(text).value,'>prior_draft\nACDE','After reloading the same file the prior canonical edit is recoverable');
 }
});
