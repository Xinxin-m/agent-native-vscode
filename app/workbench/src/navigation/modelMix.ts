import {modelDisplay} from '../chat/modelDisplay';

export type MixRow={label:string;value:number;share:number};
export type ProviderMix={provider:string;total:number;models:MixRow[]};

// Output tokens per model, per provider, from usage_history buckets. Past five models fold into Other.
export function modelMix(buckets:any[]=[],metric='out'):ProviderMix[]{
 const by=new Map<string,Map<string,number>>();
 for(const b of buckets){const provider=b.source==='codex'?'codex':b.source==='claude'?'claude':'';if(!provider)continue;
  const models=by.get(provider)||new Map<string,number>(),label=modelDisplay(b.model||'Unknown');models.set(label,(models.get(label)||0)+Number(b[metric]||0));by.set(provider,models);}
 return ['claude','codex'].filter(p=>by.has(p)).map(provider=>{
  let rows=[...by.get(provider)!].map(([label,value])=>({label,value})).filter(r=>r.value>0).sort((a,b)=>b.value-a.value);
  if(rows.length>6)rows=[...rows.slice(0,5),{label:'Other',value:rows.slice(5).reduce((s,r)=>s+r.value,0)}];
  const total=rows.reduce((s,r)=>s+r.value,0);
  return {provider,total,models:rows.map(r=>({...r,share:total?r.value/total:0}))};
 }).filter(p=>p.total>0);
}

// Shades of the one bar blue: the largest model is full strength.
export const shade=(i:number)=>`color-mix(in srgb,var(--bar) ${[100,74,54,40,30,22][Math.min(i,5)]}%,var(--panel))`;
