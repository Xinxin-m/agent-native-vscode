export function modelDisplay(model:any, catalog:any[]=[]):string {
 const id=String(model?.id||model||'').replace(/\[1m\]/gi,'');
 const resolved=String(model?.resolved_model||catalog.find(m=>m.id===id)?.resolved_model||catalog.find(m=>String(m.id).startsWith('claude-'+id+'-'))?.id||id);
 const match=resolved.match(/^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?/);
 if(match)return match[1][0].toUpperCase()+match[1].slice(1)+' '+match[2]+(match[3]?'.'+match[3]:'');
 return String(model?.label||model?.name||id).replace(/\[1m\]|\s*\(1M context\)/gi,'');
}
