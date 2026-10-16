import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const ui=readFileSync(new URL('../nanocofold/templates/platform.js',import.meta.url),'utf8');
const catalog=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
function fixture(){
 const locale={};vm.createContext(locale);vm.runInContext(catalog,locale);const nodes=[],I18N=locale.I18N;
 function el(tag,text){const node={tag,children:[],textContent:'',isConnected:true,classList:{contains:()=>false},attributes:{},append(...items){this.children.push(...items);},appendChild(child){this.children.push(child);return child;},replaceChildren(...children){this.children=children;},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k]??null;},hasAttribute(k){return k in this.attributes;}};if(text!==undefined)I18N.text(node,text);nodes.push(node);return node;}
 const named=new Map();const get=id=>{if(!named.has(id))named.set(id,{...el('div'),value:'',disabled:false});return named.get(id);};
 const context={I18N,el,$:get,URLSearchParams,offline:false,fmt:v=>v==null?I18N.value(I18N.term('미평가')):String(v),metricCaption:key=>({ipsae:'ipSAE',iptm:'표적–VHH ipTM'}[key]||key),states:{complete:'완료'},state:{catalogRequest:0,catalogSort:'created',catalogDescending:true},api:async()=>({items:[{name:'완료',id:'raw-미평가',mode:'screen',created:1,candidate_count:3,model_count:4,eligible_model_count:1,status:'complete',retention:'full',elapsed_seconds:0,storage_bytes:100}]})};vm.createContext(context);return{context,nodes,I18N,el,get};
}
test('actual catalog header construction localizes captions and sort arrows but never raw analysis names',async()=>{
 const f=fixture();vm.runInContext(ui.slice(ui.indexOf('const bytesLabel ='),ui.indexOf('async function previewRetention(')),f.context);
 await f.context.refreshCatalog();const buttons=f.nodes.filter(n=>n.tag==='button'),link=f.nodes.find(n=>n.tag==='a');
 assert.equal(buttons.length,10);assert.equal(buttons[0].textContent,'Analysis');assert.equal(buttons[2].textContent,'Run date ↓');assert.equal(buttons[4].textContent,'Saved structure observations');assert.equal(link.textContent,'완료');
 f.I18N.setLanguage('ko',{persist:false});assert.equal(buttons[0].textContent,'분석');assert.equal(buttons[2].textContent,'실행일 ↓');assert.equal(link.textContent,'완료');
 f.I18N.setLanguage('en',{persist:false});assert.equal(buttons[0].textContent,'Analysis');assert.equal(link.textContent,'완료');
});
test('generic table localizes only explicitly owned value columns and composed captions',()=>{
 const f=fixture();vm.runInContext(ui.slice(ui.indexOf('function table('),ui.indexOf('function rememberLocation(')),f.context);
 const rows=[{'지표':'표적–VHH ipTM','통과':'미통과','후보':'완료','값':.123456789}],before=JSON.stringify(rows);
 f.context.table(f.el('div'),rows,['지표','통과','후보','값'],{localizedValues:['지표','통과']});
 const cells=f.nodes.filter(n=>n.tag==='td');assert.equal(cells[0].textContent,'Target–VHH ipTM');assert.equal(cells[1].textContent,'Not passed');assert.equal(cells[2].textContent,'완료');assert.equal(cells[3].textContent,'0.123456789');
 assert.doesNotMatch(f.nodes.find(n=>n.tag==='caption').textContent,/[가-힣]/);
 f.I18N.setLanguage('ko',{persist:false});assert.equal(cells[0].textContent,'표적–VHH ipTM');assert.equal(cells[1].textContent,'미통과');assert.equal(cells[2].textContent,'완료');assert.equal(JSON.stringify(rows),before);
});
