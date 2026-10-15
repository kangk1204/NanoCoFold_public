import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const catalog=readFileSync(new URL('../nanocofold/templates/platform-i18n.js',import.meta.url),'utf8');
export function koreanContext(context){
  const document=context.document;delete context.document;vm.createContext(context);
  vm.runInContext(catalog,context);context.I18N.setLanguage('ko',{persist:false});
  if(document)context.document=document;return context;
}
