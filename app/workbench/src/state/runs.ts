import {useSyncExternalStore,useCallback} from 'react';
import {cacheRunState,writeRunCache} from './runCache';
import {api,query} from '../api/client';
export interface RunEvent {seq:number;event:string;data:any;ts?:string}
export interface RunState {id:string;run:any;events:RunEvent[];cursor:number;snapshotSeq?:number;truncatedBefore?:number|null;limit?:number;error?:string}
export const terminal=(status:string)=>['completed','failed','interrupted','cancelled','canceled','error'].includes(status);
// The server sends the newest 2000 events on a fresh load (events_truncated_before marks the rest).
export const journalLimit=2000;
export const emptyRun=(id:string):RunState=>({id,run:{id},events:[],cursor:0});
export function replayEvents(state:RunState,events:RunEvent[],run?:any):RunState{
 const bySeq=new Map(state.events.map(e=>[e.seq,e]));for(const e of events||[])if(Number.isFinite(e.seq)&&!bySeq.has(e.seq))bySeq.set(e.seq,e);
 const ordered=[...bySeq.values()].sort((a,b)=>a.seq-b.seq),cursor=ordered.reduce((max,e)=>Math.max(max,e.seq),state.cursor);
 const snapshot=run&&(typeof run.text==='string'||typeof run.output==='string'),snapshotSeq=snapshot?(Number.isFinite(run.last_seq)?run.last_seq:cursor):state.snapshotSeq;
 // Keep the newest events a fresh load gets; drop only events the output snapshot already covers.
 let drop=snapshotSeq===undefined?0:Math.max(0,ordered.length-(state.limit??journalLimit));while(drop>0&&ordered[drop-1].seq>snapshotSeq!)drop--;
 return {...state,...(run?{run:{...state.run,...run}}:{}),events:drop?ordered.slice(drop):ordered,cursor,snapshotSeq,...(drop?{truncatedBefore:ordered[drop].seq}:{}),error:undefined};
}
export function pendingRequests(state:RunState){if(terminal(state.run?.status)||state.events.some(e=>e.event==='done'))return [];const resolved=new Set(state.events.filter(e=>e.event==='request_resolved').map(e=>String(e.data?.request_id)));return state.events.filter(e=>['approval','question'].includes(e.event)&&e.data&&(e.data.request_id||e.data.id)&&!resolved.has(String(e.data.request_id||e.data.id)))}
export function runOutput(state:RunState){
 const baseline=state.snapshotSeq,events=baseline===undefined?state.events:state.events.filter(e=>e.seq>baseline);
 const done=[...events].reverse().find(e=>e.event==='done'&&typeof e.data?.text==='string');
 if(done)return done.data.text;
 const tail=events.filter(e=>e.event==='delta').map(e=>e.data?.text||'').join('');
 if(baseline!==undefined)return String(state.run?.text??state.run?.output??'')+tail;
 return String(state.run?.text||state.run?.output||tail||'');
}
const states=new Map<string,RunState>(),listeners=new Map<string,Set<()=>void>>(),watchers=new Map<string,{refs:number;timer?:ReturnType<typeof setTimeout>;source?:EventSource;busy:boolean;closed:boolean}>();
function get(id:string){if(!states.has(id)){let state=emptyRun(id);if(id)try{const saved=JSON.parse(localStorage.getItem('library.workbench.run.'+id)||'null');if(saved?.id===id&&Array.isArray(saved.events))state=cacheRunState(saved)}catch{}states.set(id,state)}return states.get(id)!}
const persistence=new Map<string,ReturnType<typeof setTimeout>>();
function persist(id:string){try{writeRunCache(localStorage,states.get(id)!)}catch{}persistence.delete(id)}
function put(id:string,s:RunState){states.set(id,s);if(id){if(terminal(s.run.status)){clearTimeout(persistence.get(id));persist(id)}else if(!persistence.has(id))persistence.set(id,setTimeout(()=>persist(id),300));}notify(id)}
// Events stream in bursts of hundreds. Render the first at once, then at most once per 100 ms, not once per event.
const notifiedAt=new Map<string,number>(),notifyTimers=new Map<string,ReturnType<typeof setTimeout>>();
function notify(id:string){
 if(notifyTimers.has(id))return;
 const fire=()=>{notifyTimers.delete(id);notifiedAt.set(id,Date.now());listeners.get(id)?.forEach(fn=>fn())},wait=(notifiedAt.get(id)??-Infinity)+100-Date.now();
 if(wait>0)notifyTimers.set(id,setTimeout(fire,wait));else fire();
}
export function subscribeRun(id:string,fn:()=>void){if(!listeners.has(id))listeners.set(id,new Set());listeners.get(id)!.add(fn);return()=>{listeners.get(id)?.delete(fn)}}
export function ingestRun(run:any,events:RunEvent[]=[]){if(run?.id)put(run.id,replayEvents(get(run.id),events,run))}
export async function refreshRun(id:string){try{const after=get(id).cursor;const data=await api(query('/api/run',{id,after}),undefined,AbortSignal.timeout(20_000));if(!data.run)throw Error('Run is unavailable');put(id,{...replayEvents(get(id),data.events||[],data.run),...(after===0?{truncatedBefore:data.events_truncated_before??null}:{})});return get(id)}catch(e){put(id,{...get(id),error:(e as Error).message});throw e}}
export async function loadEarlierEvents(id:string){const state=get(id);if(!state.truncatedBefore)return state;const data=await api(query('/api/run',{id,before:state.truncatedBefore}));const events=data.events||[],current=get(id);put(id,{...replayEvents({...current,limit:(current.limit??journalLimit)+events.length},events,data.run),truncatedBefore:data.events_truncated_before??null});return get(id)}
const eventNames=['failure','delta','start','done','state','approval','question','request_resolved','error','tool','tool_result','output','file_change','provider_event','account_selection','account_pool','provider_switch','compacted','steered','routing','session','warning','recovery','process_started','control_error','unsupported_request','provider_warning','control_receipt','queue','auto_compact','human_required','question_deferred','approval_auto_accepted'];
function watch(id:string){if(!id)return()=>{};const old=watchers.get(id);if(old){old.refs++;return()=>release(id)}const w={refs:1,busy:false,closed:false} as {refs:number;timer?:ReturnType<typeof setTimeout>;source?:EventSource;busy:boolean;closed:boolean};watchers.set(id,w);
 const connect=()=>{if(typeof EventSource==='undefined'||terminal(get(id).run.status)||w.closed)return;const source=new EventSource(query('/api/run/events',{id,after:get(id).cursor}));w.source=source;const receive=(event:MessageEvent)=>{try{const data=JSON.parse(event.data);put(id,replayEvents(get(id),[data]));if(['done','failure','error'].includes(data.event))void refreshRun(id).catch(()=>{})}catch{}};eventNames.forEach(name=>source.addEventListener(name,receive as EventListener));source.onerror=()=>{source.close();w.source=undefined}};
 const poll=async()=>{if(w.closed||w.busy)return;w.busy=true;try{await refreshRun(id);if(terminal(get(id).run.status)){w.source?.close();w.source=undefined}else if(!w.source)connect()}catch{}finally{w.busy=false;if(!w.closed&&!terminal(get(id).run.status))w.timer=setTimeout(poll,get(id).error?3500:w.source?.readyState===1?15000:1700)}};void poll();return()=>release(id);
}
function release(id:string){const w=watchers.get(id);if(!w)return;if(--w.refs<=0){w.closed=true;clearTimeout(w.timer);w.source?.close();watchers.delete(id)}}
export function useRun(id?:string){const subscribe=useCallback((fn:()=>void)=>{const key=id||'',unsubscribe=subscribeRun(key,fn),unwatch=watch(key);return()=>{unsubscribe();unwatch()}},[id]);return useSyncExternalStore(subscribe,()=>get(id||''))}
const controls=new Map<string,Promise<any>>();
export async function controlRun(id:string,action:string,extra:any={}){const token=`${id}:${action}:${extra.request_id||''}`;if(controls.has(token))return controls.get(token);const request=async()=>{try{if(action==='answer'){const latest=await refreshRun(id);if(!pendingRequests(latest).some(e=>String(e.data.request_id||e.data.id)===String(extra.request_id)))return {ok:true,already_resolved:true}}const result=await api('/api/run/control',{id,action,...extra});if(result.run)ingestRun(result.run);await refreshRun(id);return result}catch(e){await refreshRun(id).catch(()=>{});throw e}};const locks=globalThis.navigator?.locks;const promise=locks?locks.request('library.control.'+token,request):request();controls.set(token,promise);try{return await promise}finally{controls.delete(token)}}
