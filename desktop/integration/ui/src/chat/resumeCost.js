/* Shared resume line: models used, age, context size and prompt-cache state of a saved chat.
 * The indexer writes chat.resume; this file only formats it. Identical copies live in
 * web/, vscode-library/media/, workbench/src/chat/ and the Library Code UI. */
(function(root){
'use strict';
const MIN=60000,HOUR=60*MIN,DAY=24*HOUR;
// Codex documents no cache lifetime. Local rollouts (Nov 2026) stayed warm under an hour,
// were warm about half the time at 1 to 3 hours, and were mostly cold after that.
const CODEX_WARM=HOUR,CODEX_COLD=3*HOUR;
function modelName(id,catalog){
 const hit=(catalog||[]).find(m=>m&&m.id===id&&(m.name||m.label));
 if(hit)return String(hit.name||hit.label).replace(/\s*\((CLI alias|1M context)\)/gi,'');
 const v=String(id||'').match(/^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?/);
 return v?v[1][0].toUpperCase()+v[1].slice(1)+' '+v[2]+(v[3]?'.'+v[3]:''):String(id||'');
}
function tokens(n){return n>=1e6?(n/1e6).toFixed(n>=1e7?0:1).replace(/\.0$/,'')+'M':n>=1000?Math.round(n/1000)+'k':String(n);}
function age(ms){
 if(ms<MIN)return 'just now';
 if(ms<HOUR)return Math.floor(ms/MIN)+' min ago';
 if(ms<DAY)return Math.floor(ms/HOUR)+' h ago';
 const days=Math.floor(ms/DAY);
 return days===1?'yesterday':days<60?days+' days ago':Math.floor(days/30)+' months ago';
}
// Returns {text,title,tick} or null. tick: a warm countdown is showing, so refresh each minute.
function describe(chat,options){
 const r=chat&&chat.resume;if(!r)return null;
 const now=(options&&options.now)||Date.now(),catalog=options&&options.catalog;
 const counts=chat.models||{},ids=Object.keys(counts).sort((a,b)=>counts[b]-counts[a]||a.localeCompare(b));
 if(r.model&&!ids.includes(r.model))ids.unshift(r.model);
 const names=[...new Set(ids.map(id=>modelName(id,catalog)))];
 const last=Date.parse(chat.ts_last||r.at||''),at=Date.parse(r.at||'');
 const parts=[names.slice(0,2).join(' + ')+(names.length>2?' +'+(names.length-2):'')];
 if(Number.isFinite(last))parts.push(age(now-last));
 const size=r.context_tokens,reread=size?'resuming re-reads about '+tokens(size)+' input tokens':'';
 const title=[Number.isFinite(last)?'Last message '+new Date(last).toLocaleString():'',
  'Models: '+ids.map(id=>modelName(id,catalog)+(counts[id]?' ('+counts[id]+' calls)':'')).join(', '),
  r.effort?'Last effort: '+r.effort:'',
  size?'Context: '+size.toLocaleString()+' tokens'+(r.context_window?' of a '+r.context_window.toLocaleString()+' window':'')+(r.compacted?', after compaction':''):r.compacted?'Context compacted; its size is known after the next turn.':''];
 let cache,tick=false;
 if(!size)cache=r.compacted?'context compacted':'';
 else if(chat.source==='codex'){
  const idle=Number.isFinite(at)?now-at:Infinity;
  cache=idle<CODEX_WARM?tokens(size)+' tokens in context · cache likely warm':idle<CODEX_COLD?'cache may be cold: resuming may re-read about '+tokens(size)+' input tokens':'cache likely cold: '+reread;
  title.push('Codex does not document how long its prompt cache lasts. Estimate from local rollouts: usually warm under 1 hour, mostly cold after 3 hours.');
 }else{
  const ttl=(r.cache_ttl_s||0)*1000,left=Number.isFinite(at)&&ttl?at+ttl-now:-1;
  tick=left>0;
  cache=left>0?tokens(size)+' tokens in context · cache warm for '+Math.max(1,Math.ceil(left/MIN))+' more min':'cache '+(ttl?'expired':'cold')+': '+reread;
  if(ttl)title.push('Prompt cache: '+(ttl>=HOUR?'1 hour':'5 minute')+' lifetime from the last call at '+new Date(at).toLocaleTimeString()+'.');
 }
 parts.push(cache);
 return {text:parts.filter(Boolean).join(' · '),title:title.filter(Boolean).join('\n'),tick};
}
const api={describe,modelName,tokens,age};
root.LibraryResumeCost=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
