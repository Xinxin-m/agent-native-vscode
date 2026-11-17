// The server builds the list: Claude versions from ~/.claude/evolve/data/models.json,
// Codex from ~/.codex/models_cache.json. Nothing is hard-coded here.
export function composerModels(provider:string,advertised:any[]=[]){
 if(provider!=='claude')return advertised;
 return advertised.map(model=>/^(?:opus|sonnet|haiku|fable)(?:\[1m\])?$/.test(model.id)?{...model,label:model.id.replace(/\[1m\]$/,'').replace(/^./,(c:string)=>c.toUpperCase())+(model.id.endsWith('[1m]')?' (1M context)':'')+' (CLI alias)'}:model);
}
