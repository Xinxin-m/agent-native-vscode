import '../chat/authRecovery.js';
import {api} from '../api/client';
import {useEffect,useState} from 'react';
import {useWorkbench} from './context';
import {conversationIdentity,conversations} from './conversations';
import type {Resource} from './types';
const viewedKey='workbench-chat-viewed',eventName='workbench-chat-viewed';
const timestamp=(value:any)=>typeof value==='number'?value*(value<1e12?1000:1):Date.parse(value||'')||0;
function readViewed():Record<string,string>{try{return JSON.parse(globalThis.localStorage?.getItem(viewedKey)||'{}')}catch{return {}}}
function nativeKey(resource:Resource){return resource.key||(resource.kind==='chat'?conversations.peek(conversationIdentity(resource))?.nativeKey:undefined);}
export function chatActivity(resource:Resource,data:any,runs:any[]=[],attention:any[]=[],viewed:Record<string,string>={},liveSessions:any[]=[]){
 if(resource.kind!=='chat')return {unread:false,running:false,awaiting:false,finished:false,recovery:false};
 const key=nativeKey(resource),chat=(data?.chats||[]).find((c:any)=>c.key===key)||resource.meta||{};
 const exactRuns=runs.filter((run:any)=>(key&&[run.key,run.conversation_key].includes(key))||(resource.runId&&run.id===resource.runId));
 const latest=[...exactRuns].sort((a:any,b:any)=>timestamp(b.created_at)-timestamp(a.created_at))[0];
 const active=exactRuns.filter((run:any)=>['starting','running','waiting','interrupting','waiting_input','waiting_approval','awaiting_input','awaiting_approval'].includes(run.status));
 const awaiting=active.some((run:any)=>['waiting_input','waiting_approval','awaiting_input','awaiting_approval'].includes(run.status))||attention.some((item:any)=>(key&&[item.key,item.conversation_key].includes(key))||active.some((run:any)=>run.id===item.run_id));
 const liveRunning=liveSessions.some(s=>key&&s.chat_key===key&&s.alive&&!s.process_only&&['running','working','busy'].includes(s.status)&&Number(s.observed_at)>0&&Date.now()/1000-Number(s.observed_at)<150&&Number(s.observed_at)<=Date.now()/1000+5&&(!latest||!['completed','complete','failed','error','interrupted','cancelled','canceled','stopped'].includes(latest.status)||(!!timestamp(latest.finished_at)&&Number(s.observed_at)*1000>timestamp(latest.finished_at))));
 const running=!awaiting&&(active.length>0||liveRunning);
 // Replies older than the first time this client tracked reading ('*') count as read, so a fresh client is never blue on every chat.
 const seen=Math.max(timestamp(key&&viewed[key]),timestamp(key&&data?.views?.[key]?.last),timestamp(viewed['*']));
 const finished=!active.length&&exactRuns.some((run:any)=>run.status==='completed'&&timestamp(run.finished_at)>seen);
 return {recovery:latest?Boolean(latest.failure&&latest.failure.kind!=='other'&&latest.status==='failed'):Boolean(chat.failure&&chat.failure.kind!=='other'),unread:!!key&&timestamp(chat.last_assistant_ts)>seen,finished,running,awaiting};
}
export const canMarkChatViewed=(visibility:string,focused:boolean)=>visibility==='visible'&&focused;
export function markChatViewed(resource:Resource){if(!canMarkChatViewed(document.visibilityState,document.hasFocus()))return;const key=resource.kind==='chat'&&nativeKey(resource);if(!key)return;const viewed=readViewed(),previous=timestamp(viewed[key]);viewed[key]=new Date().toISOString();if(!previous||timestamp(resource.meta?.ts_last)>previous)void api('/api/view',{key,ts:viewed[key]}).catch(()=>{});try{localStorage.setItem(viewedKey,JSON.stringify(viewed));}catch{}window.dispatchEvent(new Event(eventName));}
function readTrackedViews(){const viewed=readViewed();if(!viewed['*']){viewed['*']=new Date().toISOString();try{localStorage.setItem(viewedKey,JSON.stringify(viewed));}catch{}}return viewed;}
export function useChatActivities(){const wb=useWorkbench();const [viewed,setViewed]=useState(readTrackedViews);useEffect(()=>{const refresh=()=>setViewed(readViewed());window.addEventListener(eventName,refresh);window.addEventListener('storage',refresh);return()=>{window.removeEventListener(eventName,refresh);window.removeEventListener('storage',refresh);};},[]);return (resource:Resource)=>chatActivity(resource,wb.data,wb.runs,wb.attention||[],viewed,wb.liveSessions||[]);}
export function useChatActivity(resource:Resource){return useChatActivities()(resource);}
