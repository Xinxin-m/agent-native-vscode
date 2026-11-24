import {api,query} from '../api/client';
import type {RunEvent} from '../state/runs';
// Client side of the broker's message queue (docs/V2-CONTRACTS.md, "Queued messages and steering").
// A server without the queue routes answers 404; the composer then keeps its single Send.
export interface QueueItem {id:string;key:string;text:string;attachments?:string[];state:string;version?:number;position?:number;error?:string|null}
export interface Queue {key:string;revision:string;active_run_id?:string|null;held?:boolean;items:QueueItem[]}
let supported:boolean|undefined;
export const queueSupported=()=>supported===true;
export function resetQueueSupport(){supported=undefined}
const missing=(e:any)=>[404,405,501].includes(Number(e?.status))||/HTTP 40[45]|Unsupported Library route|not valid JSON|Unexpected token/.test(String(e?.message||''));
export const pendingItems=(queue:Queue|null|undefined)=>(queue?.items||[]).filter(i=>['sending','queued','delivering','failed'].includes(i.state));
// Shown at once as a sent message while the enqueue request is in flight; the broker's item replaces it.
export const withSending=(queue:Queue|null,key:string,id:string,text:string):Queue=>({key,revision:'',...queue,items:[...(queue?.items||[]).filter(i=>i.id!==id),{id,key,text,state:'sending'}]});
export const withoutItem=(queue:Queue|null,id:string)=>queue&&{...queue,items:queue.items.filter(i=>i.id!==id)};
export async function fetchQueue(key:string):Promise<Queue|null>{
 if(!key||supported===false)return null;
 try{const value=await api(query('/api/run/queue',{key}));if(!value?.queue){supported=false;return null}supported=true;return value.queue}
 catch(e){if(missing(e))supported=false;return null}
}
// Returns {queue,item}, or null when the server has no queue. Conflicts (409) are thrown with their code.
export async function queueAction(action:string,body:Record<string,any>):Promise<{queue:Queue;item:QueueItem;run?:any}|null>{
 if(supported===false)return null;
 try{const value=await api('/api/run/queue',{action,...body});if(!value?.queue){supported=false;return null}supported=true;return value}
 catch(e){if(missing(e)){supported=false;return null}throw e}
}
// The newest `queue` event in a run journal carries the pending list.
export function queueFromEvents(events:RunEvent[],key:string):Queue|null{
 const event=[...events].reverse().find(e=>e.event==='queue'&&(!key||!e.data?.key||e.data.key===key));
 return event?{key:event.data.key||key,revision:String(event.data.revision||''),items:event.data.items||[]}:null;
}
// One status per message: Sending, Queued (with when it goes) or Not delivered.
export function queueLabel(item:QueueItem,held=false,phase=''){
 if(item.state==='failed')return 'Not delivered'+(item.error?': '+item.error:'');
 if(item.state==='delivering'||item.state==='sending')return 'Sending…';
 return held?'Queued · no agent is running':phase==='background'?'Queued · sends when the background work ends':'Queued · sends when this turn ends';
}
