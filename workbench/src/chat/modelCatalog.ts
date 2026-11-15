// Verified against https://platform.claude.com/docs/en/models/overview,
// 2026-11-15. Explicit IDs avoid changing the chosen version behind an alias.
// Provider/account restrictions are still enforced by the native CLI.
export const currentClaudeModels=[
 {id:'claude-opus-5-5',name:'Opus 5.5'},
 {id:'claude-sonnet-5-5',name:'Sonnet 5.5'},
 {id:'claude-fable-5-2',name:'Fable 5.2'},
 {id:'claude-haiku-5-5',name:'Haiku 5.5'},
];
export function composerModels(provider:string,advertised:any[]=[]){
 if(provider!=='claude')return advertised;
 const currentIds=new Set(currentClaudeModels.map(m=>m.id));
 const aliases=advertised.filter(m=>!currentIds.has(m.id)).map(model=>/^(?:opus|sonnet|haiku|fable)(?:\[1m\])?$/.test(model.id)?{...model,label:model.id.replace(/\[1m\]$/,'').replace(/^./,(c:string)=>c.toUpperCase())+(model.id.endsWith('[1m]')?' (1M context)':'')+' (CLI alias)'}:model);
 const versions=currentClaudeModels.map(model=>({...advertised.find(m=>m.id===model.id),...model}));
 const defaults=aliases.filter(m=>!m.id),others=aliases.filter(m=>m.id);
 return [...defaults,...versions,...others];
}
