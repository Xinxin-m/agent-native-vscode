import type {RunState} from './runs';
export const runCachePrefix='library.workbench.run.';
const fields=['id','status','key','conversation_key','session_id','provider','host_id','account_id','created_at','updated_at','operation','recovery_blocked'];
/** Event journals live on the server. A browser cache must replay from zero. */
export function cacheRunState(state:RunState):RunState{
 const run:any={};for(const field of fields){const value=state.run?.[field];if(typeof value==='string')run[field]=value.slice(0,256);else if(typeof value==='boolean'||typeof value==='number')run[field]=value;}
 return {id:state.id,run,events:[],cursor:0};
}
/** Keep all disposable run metadata below 256 KiB, reserving room for drafts. */
export function writeRunCache(storage:Pick<Storage,'length'|'key'|'getItem'|'setItem'>,state:RunState){
 const key=runCachePrefix+state.id,value=JSON.stringify(cacheRunState(state));let bytes=2*(key.length+value.length);
 for(let i=0;i<storage.length;i++){const other=storage.key(i);if(other?.startsWith(runCachePrefix)&&other!==key)bytes+=2*(other.length+(storage.getItem(other)?.length||0));}
 if(bytes<=256*1024&&storage.getItem(key)!==value)storage.setItem(key,value);
}
