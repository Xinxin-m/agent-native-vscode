import {useSyncExternalStore} from 'react';
import {api,ApiError,available} from '../api/client';

// One poller for the whole app. The feed is independent of which chats are open.
export interface Item {
 id:string;source:'agent'|'app';kind:string;title:string;body:string;at:number;origin:string;chat_key?:string|null;chat_title?:string|null;thread?:string;
 options:string[];allow_text:boolean;needs_answer:boolean;state:'open'|'answered'|'archived';answer:null|{choice:string;text:string;at:number};
 link:string;seen:boolean;shown:boolean;account_id?:string;run_id?:string;tool?:string;live_id?:string;
 debuggable?:boolean;debug?:null|{state:'queued'|'running'|'fixed'|'needs_review'|'needs_signin'|'needs_approval'|'failed';text?:string;at?:number;model?:string;agent_key?:string|null;review_key?:string|null};
}
export interface Feed{mode:'loading'|'live'|'legacy';items:Item[];counts:{open:number;unseen:number;open_agent:number;open_app:number};settings:{duration_seconds:number};warnings:string[];revision:string;error:string}
const empty:Feed={mode:'loading',items:[],counts:{open:0,unseen:0,open_agent:0,open_app:0},settings:{duration_seconds:3},warnings:[],revision:'',error:''};
let feed=empty,wantArchived=false,timer:ReturnType<typeof setInterval>|undefined,hiddenAt=0,busy=false,users=0;
const listeners=new Set<()=>void>();
const set=(next:Feed)=>{feed=next;listeners.forEach(l=>l())};
export const claimant=(()=>{try{let v=sessionStorage.getItem('library-claimant');if(!v){v=Math.random().toString(36).slice(2)+Date.now().toString(36);sessionStorage.setItem('library-claimant',v)}return v}catch{return Math.random().toString(36).slice(2)}})();

// Old servers answer 404: build the same items from /api/notices.
function fromNotices(value:any):Feed{
 const items:Item[]=(value.notices||[]).map((n:any)=>({id:'notice:'+n.key,source:'app' as const,kind:n.kind||'other',title:n.message||'',body:'',at:n.at||n.first_seen||0,origin:'library',chat_key:n.chat_key,options:[],allow_text:false,needs_answer:false,state:n.archived_at?'archived' as const:'open' as const,answer:null,link:'',seen:Boolean(n.read_at),shown:true,account_id:n.account_id,run_id:n.run_id})).sort((a:Item,b:Item)=>b.at-a.at);
 const open=items.filter(i=>i.state==='open');
 return {mode:'legacy',items,counts:{open:open.length,unseen:open.filter(i=>!i.seen).length,open_agent:0,open_app:open.length},settings:{duration_seconds:value.settings?.duration_seconds??3},warnings:[],revision:'legacy:'+(value.notice_center?.revision||JSON.stringify(items.map(i=>[i.id,i.seen,i.state]))),error:''};
}
export async function poll(force=false){
 if(busy)return;busy=true;
 try{
  if(feed.mode==='legacy'&&!available('/api/notifications')){const next=fromNotices(await api('/api/notices'));if(force||next.revision!==feed.revision)set(next);return}
  const value=await api('/api/notifications'+(wantArchived?'?archived=1':''));
  if(!force&&value.revision===feed.revision&&feed.mode==='live'&&!feed.error)return;
  set({mode:'live',items:value.items||[],counts:{open:0,unseen:0,open_agent:0,open_app:0,...value.counts},settings:{duration_seconds:value.settings?.duration_seconds??3},warnings:value.warnings||[],revision:value.revision,error:''});
 }catch(e:any){
  // An older server has no feed: build it from /api/notices (the route is tried again after a back-off).
  if(e instanceof ApiError&&e.status===404&&!available('/api/notifications')){busy=false;if(feed.mode!=='legacy')set({...empty,mode:'legacy'});return poll(true)}
  if(feed.mode==='loading')set({...feed,mode:'live',error:e.message});else if(feed.error!==e.message)set({...feed,error:e.message});
 }finally{busy=false}
}
const tick=()=>{if(document.hidden&&Date.now()-hiddenAt>60000)return;void poll()};
const visibility=()=>{if(document.hidden)hiddenAt=Date.now();else void poll(true)};
const focus=()=>void poll(true);
export function startFeed(){
 if(users++===0){hiddenAt=document.hidden?Date.now():0;void poll(true);timer=setInterval(tick,3000);document.addEventListener('visibilitychange',visibility);window.addEventListener('focus',focus)}
 return()=>{if(--users===0){clearInterval(timer);document.removeEventListener('visibilitychange',visibility);window.removeEventListener('focus',focus)}};
}
export function includeArchived(on:boolean){if(wantArchived!==on){wantArchived=on;feed={...feed,revision:''};void poll(true)}}
export const useFeed=()=>useSyncExternalStore(cb=>{listeners.add(cb);return()=>void listeners.delete(cb)},()=>feed);
const needsAnswer=(i:Item)=>i.state==='open'&&i.needs_answer;
export const waitingCount=(items:Item[])=>items.filter(needsAnswer).length;
// Blue dot: waits for her decision. Red dot: failed. Nothing else is marked.
// Same split as the server's debug_agent.py: a failed run (any failure kind) is red; stalled or silent agents wait for her.
const FAILED_KINDS=['limit_reached','rate_limited','auth_expired','not_logged_in'];
export const failedKind=(i:Item)=>FAILED_KINDS.includes(i.kind)||i.kind==='other'&&!!i.run_id;
export const isFailed=(i:Item)=>i.state==='open'&&failedKind(i);
export const debugActive=(i:Item)=>!!i.debug&&['queued','running'].includes(i.debug.state);

// History (answered and archived items): one tab per message type, then day groups, newest first.
export type HistoryTab='agents'|'app'|'failed';
export const historyRows=(items:Item[],tab:HistoryTab)=>items.filter(i=>i.state!=='open'&&(tab==='failed'?failedKind(i):tab==='agents'?i.source==='agent':i.source==='app'));
export function byDay(items:Item[],now=new Date()):[string,Item[]][]{
 const day=(ms:number)=>{const d=new Date(ms);d.setHours(0,0,0,0);return d.getTime()};
 const today=day(now.getTime()),yesterday=day(today-12*3600000),groups=new Map<number,Item[]>();
 for(const i of [...items].sort((a,b)=>b.at-a.at)){const k=day(i.at*1000);if(!groups.has(k))groups.set(k,[]);groups.get(k)!.push(i)}
 return [...groups].map(([k,rows])=>[k===today?'Today':k===yesterday?'Yesterday':new Date(k).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric',...(new Date(k).getFullYear()!==now.getFullYear()?{year:'numeric'}:{})}),rows]);
}
export const isWaiting=(i:Item)=>i.state==='open'&&!isFailed(i)&&(i.needs_answer||i.kind==='waiting');

// The secondary sidebar (Cmd+L or its toggle, never the bell); its open state survives a reload.
let panelOpen=(()=>{try{return localStorage.getItem('workbench-notifications-panel')==='open'}catch{return false}})();
const panelListeners=new Set<()=>void>();
export function setPanel(open:boolean|((v:boolean)=>boolean)){const next=typeof open==='function'?open(panelOpen):open;if(next===panelOpen)return;panelOpen=next;try{localStorage.setItem('workbench-notifications-panel',next?'open':'closed')}catch{}panelListeners.forEach(l=>l())}
export const usePanel=()=>useSyncExternalStore(cb=>{panelListeners.add(cb);return()=>void panelListeners.delete(cb)},()=>panelOpen);

function patch(item:Item){set({...feed,items:feed.items.map(i=>i.id===item.id?{...i,...item}:i)})}
// Acts on the server; throws the server's own error so the card can keep the typed text.
export async function act(item:Item,action:'answer'|'archive'|'seen'|'shown'|'debug',extra:{choice?:string;text?:string}={}){
 if(feed.mode==='legacy'){
  if(action==='archive')await api('/api/notices',{action:'archive',key:item.id.replace(/^notice:/,'')});
  else if(action==='seen')await api('/api/notices',{action:'read',key:item.id.replace(/^notice:/,'')});
  await poll(true);return null;
 }
 try{
  const reply=await api('/api/notifications/act',{id:item.id,action,claimant,...extra});
  if(reply.item)patch(reply.item);
  void poll(true);
  return reply;
 }catch(e:any){
  if(['request_resolved','already_answered','item_closed','not_found'].includes(e.code))void poll(true);
  throw e;
 }
}
export async function actMany(ids:string[],action:'seen'|'shown'){
 if(!ids.length)return {claimed:[] as string[]};
 if(feed.mode==='legacy'){for(const id of ids)await api('/api/notices',{action:'read',key:id.replace(/^notice:/,'')});void poll(true);return {claimed:ids}}
 const reply=await api('/api/notifications/act',{ids,action,claimant});
 if(action==='seen')void poll(true);
 return reply;
}
