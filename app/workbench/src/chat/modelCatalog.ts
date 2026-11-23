import {modelDisplay} from './modelDisplay';
// The server builds the list: Claude versions from each CLI profile's additionalModelOptionsCache
// and the tiers file named by config models_file, newest first per family;
// Codex from ~/.codex/models_cache.json. Nothing is hard-coded here.
// Aliases such as opus resolve to an exact version that is also listed, so each model is shown
// once: the selected id wins (a saved alias stays selected) and a 1M-context variant follows its base.
export function composerModels(provider:string,advertised:any[]=[],selected='',catalog:any[]=advertised){
 const out:any[]=[],first=new Map<string,number>();
 for(const model of advertised){
  if(!model.id){out.push(model);continue}
  const label=modelDisplay(model,catalog)+(/\[1m\]$/i.test(model.id)?' (1M context)':'');
  const at=first.get(label);
  if(at===undefined){first.set(label,out.length);out.push({...model,label});}
  else if(model.id===selected)out[at]={...model,label};
 }
 const rank=(m:any,i:number)=>m.id?2*(first.get(String(m.label).replace(/ \(1M context\)$/,''))??i)+(String(m.label).endsWith(' (1M context)')?1:0):2*i;
 return out.map((m,i)=>[rank(m,i),m] as const).sort((a,b)=>a[0]-b[0]).map(([,m])=>m);
}
