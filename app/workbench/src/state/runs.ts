import {useSyncExternalStore,useCallback} from 'react';
import {cacheRunState,writeRunCache} from './runCache';
import {api,query} from '../api/client';
export interface RunEvent {seq:number;event:string;data:any;ts?:string}
export interface RunState {id:string;run:any;events:RunEvent[];cursor:number;snapshotSeq?:number;truncatedBefore?:number|null;error?:string}
export const terminal=(status:string)=>['completed','failed','interrupted','cancelled','canceled','error'].includes(status);
export const emptyRun=(id:string):RunState=>({id,run:{id},events:[],cursor:0});
export function replayEvents(state:RunState,events:RunEvent[],run?:any):RunState{
 const bySeq=new Map(state.events.map(e=>[e.seq,e]));for(const e of events||[])if(Number.isFinite(e.seq)&&!bySeq.has(e.seq))bySeq.set(e.seq,e);
 const ordered=[...bySeq.values()].sort((a,b)=>a.seq-b.seq),cursor=ordered.reduce((max,e)=>Math.max(max,e.seq),state.cursor);
 const snapshot=run&&(typeof run.text==='string'||typeof run.output==='string');
 return {...state,...(run?{run:{...state.run,...run}}:{}),events:ordered,cursor,...(snapshot?{snapshotSeq:Number.isFinite(run.last_seq)?run.last_seq:cursor}:{}),error:undefined};
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
function put(id:string,s:RunState){states.set(id,s);if(id){if(terminal(s.run.status)){clearTimeout(persistence.get(id));persist(id)}else if(!persistence.has(id))persistence.set(id,setTimeout(()=>persist(id),300));}listeners.get(id)?.forEach(fn=>fn())}
export function ingestRun(run:any,events:RunEvent[]=[]){if(run?.id)put(run.id,replayEvents(get(run.id),events,run))}
export async function refreshRun(id:string){try{const after=get(id).cursor;const data=await api(query('/api/run',{id,after}));if(!data.run)throw Error('Run is unavailable');put(id,{...replayEvents(get(id),data.events||[],data.run),truncatedBefore:after===0?data.events_truncated_before??null:get(id).truncatedBefore});return get(id)}catch(e){put(id,{...get(id),error:(e as Error).message});throw e}}
export async function loadEarlierEvents(id:string){const state=get(id);if(!state.truncatedBefore)return state;const data=await api(query('/api/run',{id,before:state.truncatedBefore}));put(id,{...replayEvents(get(id),data.events||[],data.run),truncatedBefore:data.events_truncated_before??null});return get(id)}
const eventNames=['failure','delta','start','done','state','approval','question','request_resolved','error','tool','tool_result','output','file_change','provider_event','account_selection','compacted','steered','routing','session','warning','recovery','process_started','control_error','unsupported_request','provider_warning','control_receipt','queue','auto_compact','human_required','question_deferred','approval_auto_accepted'];
function watch(id:string){if(!id)return()=>{};const old=watchers.get(id);if(old){old.refs++;return()=>release(id)}const w={refs:1,busy:false,closed:false} as {refs:number;timer?:ReturnType<typeof setTimeout>;source?:EventSource;busy:boolean;closed:boolean};watchers.set(id,w);
 const connect=()=>{if(typeof EventSource==='undefined'||terminal(get(id).run.status)||w.closed)return;const source=new EventSource(query('/api/run/events',{id,after:get(id).cursor}));w.source=source;const receive=(event:MessageEvent)=>{try{const data=JSON.parse(event.data);put(id,replayEvents(get(id),[data]));if(['done','failure','error'].includes(data.event))void refreshRun(id).catch(()=>{})}catch{}};eventNames.forEach(name=>source.addEventListener(name,receive as EventListener));source.onerror=()=>{source.close();w.source=undefined}};
 const poll=async()=>{if(w.closed||w.busy)return;w.busy=true;try{await refreshRun(id);if(terminal(get(id).run.status)){w.source?.close();w.source=undefined}else if(!w.source)connect()}catch{}finally{w.busy=false;if(!w.closed&&!terminal(get(id).run.status))w.timer=setTimeout(poll,get(id).error?3500:w.source?.readyState===1?15000:1700)}};void poll();return()=>release(id);
}
function release(id:string){const w=watchers.get(id);if(!w)return;if(--w.refs<=0){w.closed=true;clearTimeout(w.timer);w.source?.close();watchers.delete(id)}}
export function useRun(id?:string){const subscribe=useCallback((fn:()=>void)=>{const key=id||'';if(!listeners.has(key))listeners.set(key,new Set());listeners.get(key)!.add(fn);const unwatch=watch(key);return()=>{listeners.get(key)?.delete(fn);unwatch()}},[id]);return useSyncExternalStore(subscribe,()=>get(id||''))}
const controls=new Map<string,Promise<any>>();
export async function controlRun(id:string,action:string,extra:any={}){const token=`${id}:${action}:${extra.request_id||''}`;if(controls.has(token))return controls.get(token);const request=async()=>{try{if(action==='answer'){const latest=await refreshRun(id);if(!pendingRequests(latest).some(e=>String(e.data.request_id||e.data.id)===String(extra.request_id)))return {ok:true,already_resolved:true}}const result=await api('/api/run/control',{id,action,...extra});if(result.run)ingestRun(result.run);await refreshRun(id);return result}catch(e){await refreshRun(id).catch(()=>{});throw e}};const locks=globalThis.navigator?.locks;const promise=locks?locks.request('library.control.'+token,request):request();controls.set(token,promise);try{return await promise}finally{controls.delete(token)}}
