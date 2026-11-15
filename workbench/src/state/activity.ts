import {useEffect,useState} from 'react';
import {useWorkbench} from './context';
import {terminal} from './runs';
import {conversationIdentity,conversations} from './conversations';
import type {Resource} from './types';
const viewedKey='workbench-chat-viewed',eventName='workbench-chat-viewed';
const timestamp=(value:any)=>typeof value==='number'?value*(value<1e12?1000:1):Date.parse(value||'')||0;
function readViewed():Record<string,string>{try{return JSON.parse(globalThis.localStorage?.getItem(viewedKey)||'{}')}catch{return {}}}
function nativeKey(resource:Resource){return resource.key||(resource.kind==='chat'?conversations.peek(conversationIdentity(resource))?.nativeKey:undefined);}
export function chatActivity(resource:Resource,data:any,runs:any[]=[],attention:any[]=[],viewed:Record<string,string>={},liveSessions:any[]=[]){
 if(resource.kind!=='chat')return {unread:false,running:false,awaiting:false};
 const key=nativeKey(resource),chat=(data?.chats||[]).find((c:any)=>c.key===key)||resource.meta||{};
 const exactRuns=runs.filter((run:any)=>(key&&[run.key,run.conversation_key].includes(key))||(resource.runId&&run.id===resource.runId));
 const active=exactRuns.filter((run:any)=>run.status&&!terminal(run.status));
 const awaiting=attention.some((item:any)=>(key&&[item.key,item.conversation_key].includes(key))||active.some((run:any)=>run.id===item.run_id));
 const seen=Math.max(timestamp(key&&viewed[key]),timestamp(key&&data?.views?.[key]?.last));
 return {unread:!!key&&timestamp(chat.ts_last)>seen,running:active.length>0||liveSessions.some(s=>key&&s.chat_key===key&&s.alive&&!s.process_only&&Number(s.observed_at)>0&&Date.now()/1000-Number(s.observed_at)<150&&Number(s.observed_at)<=Date.now()/1000+5),awaiting};
}
export const canMarkChatViewed=(visibility:string,focused:boolean)=>visibility==='visible'&&focused;
export function markChatViewed(resource:Resource){if(!canMarkChatViewed(document.visibilityState,document.hasFocus()))return;const key=resource.kind==='chat'&&nativeKey(resource);if(!key)return;const viewed=readViewed();viewed[key]=new Date().toISOString();try{localStorage.setItem(viewedKey,JSON.stringify(viewed));}catch{}window.dispatchEvent(new Event(eventName));}
export function useChatActivities(){const wb=useWorkbench(),[viewed,setViewed]=useState(readViewed);useEffect(()=>{const refresh=()=>setViewed(readViewed());window.addEventListener(eventName,refresh);window.addEventListener('storage',refresh);return()=>{window.removeEventListener(eventName,refresh);window.removeEventListener('storage',refresh);};},[]);return (resource:Resource)=>chatActivity(resource,wb.data,wb.runs,wb.attention||[],viewed,wb.liveSessions||[]);}
export function useChatActivity(resource:Resource){return useChatActivities()(resource);}
