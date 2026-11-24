import '../chat/authRecovery.js';
import {api} from '../api/client';
import {useEffect,useRef,useState} from 'react';
import {useWorkbench} from './context';
import {conversationIdentity,conversations} from './conversations';
import type {Resource} from './types';
const eventName='workbench-chat-viewed';
const timestamp=(value:any)=>typeof value==='number'?value*(value<1e12?1000:1):Date.parse(value||'')||0;
function nativeKey(resource:Resource){return resource.key||(resource.kind==='chat'?conversations.peek(conversationIdentity(resource))?.nativeKey:undefined);}
export function chatActivity(resource:Resource,data:any,runs:any[]=[],attention:any[]=[],viewed:Record<string,string>={},liveSessions:any[]=[]){
 if(resource.kind!=='chat')return {unread:false,running:false,awaiting:false,finished:false,recovery:false,failed:false,live:null as string|null};
 const key=nativeKey(resource),chat=(data?.chats||[]).find((c:any)=>c.key===key)||resource.meta||{};
 const exactRuns=runs.filter((run:any)=>(key&&[run.key,run.conversation_key].includes(key))||(resource.runId&&run.id===resource.runId));
 const latest=[...exactRuns].sort((a:any,b:any)=>timestamp(b.created_at)-timestamp(a.created_at))[0];
 const active=exactRuns.filter((run:any)=>['starting','running','waiting','interrupting','waiting_input','waiting_approval','awaiting_input','awaiting_approval'].includes(run.status));
 const awaiting=active.some((run:any)=>['waiting_input','waiting_approval','awaiting_input','awaiting_approval'].includes(run.status))||attention.some((item:any)=>(key&&[item.key,item.conversation_key].includes(key))||active.some((run:any)=>run.id===item.run_id));
 const liveRunning=liveSessions.some(s=>key&&s.chat_key===key&&s.alive&&!s.process_only&&['running','working','busy'].includes(s.status)&&Number(s.observed_at)>0&&Date.now()/1000-Number(s.observed_at)<150&&Number(s.observed_at)<=Date.now()/1000+5&&(!latest||!['completed','complete','failed','error','interrupted','cancelled','canceled','stopped'].includes(latest.status)||(!!timestamp(latest.finished_at)&&Number(s.observed_at)*1000>timestamp(latest.finished_at))));
 const running=!awaiting&&(active.length>0||liveRunning);
 // The server decides what she has seen (`unseen`, `seen_at`); no client keeps its own baseline.
 const seen=Math.max(timestamp(chat.seen_at),timestamp(key&&data?.views?.[key]?.last));
 const row=liveSessions.find(s=>key&&s.chat_key===key&&s.alive&&!s.process_only&&s.live_status&&Number(s.observed_at)>0&&Date.now()/1000-Number(s.observed_at)<150);
 const live:string|null=row?.live_status||chat.live_status||null;
 const finished=!active.length&&exactRuns.some((run:any)=>run.status==='completed'&&timestamp(run.finished_at)>seen);
 // Failed: the latest known run failed and the transcript has not moved on since (same rule as the server's sidebar.resolve), else the server's `failed` flag.
 const failed=!active.length&&(latest?latest.status==='failed'&&!(timestamp(chat.ts_last)>timestamp(latest.finished_at||latest.updated_at||latest.created_at)+120000):chat.failed===true);
 return {failed,recovery:latest?Boolean(failed&&latest.failure&&latest.failure.kind!=='other'):Boolean(chat.failure&&chat.failure.kind!=='other'),unread:!!key&&chat.unseen===true,finished,running,awaiting,live};
}
export const canMarkChatViewed=(visibility:string,focused:boolean)=>visibility==='visible'&&focused;
// Tell the server she has this chat in front of her; only a visible, focused window may say so.
export function markChatViewed(resource:Resource){if(!canMarkChatViewed(document.visibilityState,document.hasFocus()))return Promise.resolve(false);const key=resource.kind==='chat'&&nativeKey(resource);if(!key)return Promise.resolve(false);return reportSeen([key]);}
export function reportSeen(keys:string[]){return api('/api/seen',{keys,visible:true,focused:true}).then(()=>{window.dispatchEvent(new Event(eventName));return true;}).catch(()=>api('/api/view',{key:keys[0],ts:new Date().toISOString()}).then(()=>true).catch(()=>false));}
/** Reports every open, unseen chat once per result while the window is visible and focused (also when a reply arrives in an open chat). */
export function useSeenReporter(openKeys:()=>string[],depends:any[]){
 const wb=useWorkbench(),sent=useRef<Record<string,string>>({});
 useEffect(()=>{
  const run=()=>{if(!canMarkChatViewed(document.visibilityState,document.hasFocus()))return;
   const chats=new Map<string,any>((wb.data?.chats||[]).map((c:any)=>[c.key,c]));
   const keys=openKeys().filter(k=>chats.get(k)?.unseen===true&&sent.current[k]!==String(chats.get(k).last_assistant_ts));
   if(!keys.length)return;for(const k of keys)sent.current[k]=String(chats.get(k).last_assistant_ts);
   void reportSeen(keys.slice(0,100)).then(ok=>{if(ok)void wb.refresh?.().catch(()=>{});else for(const k of keys)delete sent.current[k];});};
  run();window.addEventListener('focus',run);document.addEventListener('visibilitychange',run);
  return()=>{window.removeEventListener('focus',run);document.removeEventListener('visibilitychange',run);};
 },[wb.data,...depends]);
}
export function useChatActivities(){const wb=useWorkbench();return (resource:Resource)=>chatActivity(resource,wb.data,wb.runs,wb.attention||[],{},wb.liveSessions||[]);}
export function useChatActivity(resource:Resource){return useChatActivities()(resource);}
