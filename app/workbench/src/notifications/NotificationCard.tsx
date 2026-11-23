import {useState} from 'react';
import {X} from 'lucide-react';
import {act,isWaiting,isFailed,type Item} from './feed';
import {refreshRun} from '../state/runs';

export const ago=(at:number)=>{const s=Math.max(0,Date.now()/1000-at);return s<60?'just now':s<3600?Math.floor(s/60)+' min ago':s<86400?Math.floor(s/3600)+' h ago':new Date(at*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'})};
const providerOf=(i:Item)=>{const k=i.tool||i.chat_key||i.account_id||'';return k.startsWith('claude')?'Claude':k.startsWith('codex')?'Codex':i.origin==='monitor'?'Monitor':''};
const first=(s:string)=>s.split('\n')[0];
const debugWords:Record<string,string>={queued:'Debug queued',running:'Debugging',fixed:'Fixed',needs_review:'Needs your review',needs_signin:'Sign in needed',needs_approval:'Needs your approval',failed:'Debug failed'};

interface Props{item:Item;variant:'page'|'popup';onOpenChat:(item:Item)=>void;onHide?:(item:Item)=>void}
export function NotificationCard({item,variant,onOpenChat,onHide}:Props){
 const [text,setText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const answerable=item.options.length>0||item.allow_text;
 const live=item.state==='open'||(item.state==='answered'&&item.kind==='signed_out');
 async function send(choice?:string){
  if(busy||(!choice&&!text.trim()))return;
  setBusy(true);setError('');
  try{
   const reply=await act(item,'answer',{choice,text:text.trim()||undefined});
   setText('');
   if(item.run_id)void refreshRun(item.run_id).catch(()=>{});
   return reply;
  }catch(e:any){setError(e.message||'Not sent')}finally{setBusy(false)}
 }
 async function cross(){
  if(variant==='popup'){onHide?.(item);return}
  setBusy(true);setError('');
  try{await act(item,'archive')}catch(e:any){setError(e.message||'Not archived')}finally{setBusy(false)}
 }
 // One click sends a debug agent; progress comes back on the item through the feed poll.
 // Debug lives in the panel; pop-ups stay short.
 const debug=item.debug,debugging=!!debug&&['queued','running'].includes(debug.state),canDebug=variant==='page'&&item.debuggable===true;
 async function startDebug(){
  if(busy||debugging)return;
  setBusy(true);setError('');
  try{await act(item,'debug')}catch(e:any){setError(e.status===409?'A debug is already running for this item.':e.message||'Debug did not start')}finally{setBusy(false)}
 }
 const mark=isWaiting(item)?['wait','Waiting for your decision']:isFailed(item)?['fail','Failed']:null;
 const context=[providerOf(item),ago(item.at)].filter(Boolean);
 const subject=item.chat_key&&item.chat_title?<a href="#" className="ncard-chat" onClick={e=>{e.preventDefault();onOpenChat(item)}}>{item.chat_title}</a>:null;
 // The server titles some notices just "Notice" and puts the message in the body.
 const generic=item.title==='Notice'&&!!item.body,heading=generic?item.body:item.title;
 const reason=!subject&&!generic&&item.kind!=='approval'&&item.body?first(item.body):'';
 const waiting=item.kind==='waiting',titleLink=!answerable&&!waiting&&(item.chat_key||item.link);
 const title=<>{heading}{item.kind==='approval'&&item.body&&<> <code>{first(item.body)}</code></>}</>;
 const answer=item.answer&&(item.answer.choice||item.answer.text);
 const openThread=!waiting&&!!item.link&&(!!item.chat_key||answerable);
 return <div className={'ncard'+(variant==='popup'?' ncard-popup':'')+(item.state!=='open'?' ncard-done':'')} role="group" aria-label={heading} data-id={item.id} data-kind={item.kind}
  onKeyDown={e=>{if(e.key==='Escape'&&variant==='popup'){e.stopPropagation();onHide?.(item)}}}>
  <span className="ncard-mark">{mark&&<i className={'mk '+mark[0]} role="img" aria-label={mark[1]} title={mark[1]}/>}</span>
  <div className="ncard-body">
   {titleLink?(item.chat_key?<button type="button" className="ncard-title ncard-link" onClick={()=>onOpenChat(item)}>{title}</button>:<a className="ncard-title ncard-link" href={item.link} target="_blank" rel="noopener noreferrer">{title}</a>):<div className="ncard-title">{title}</div>}
   <div className="ncard-context">{context.join(' · ')}{(subject||reason)&&' · '}{subject}{reason}</div>
   {live&&item.options.length>0&&<div className="ncard-options">{item.options.map(o=><button key={o} type="button" className="ncard-btn" disabled={busy} onClick={()=>void send(o)}>{o}</button>)}</div>}
   {live&&item.allow_text&&<div className="ncard-field"><input value={text} disabled={busy} placeholder={item.options.length?'Or type an answer':'Type an answer'} aria-label={'Answer: '+heading} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/></div>}
   {item.state!=='open'&&answer&&<div className="ncard-answered">You chose {item.answer!.choice||item.answer!.text}{item.answer!.at?' · '+ago(item.answer!.at):''}</div>}
   {(waiting||openThread||(live&&canDebug))&&<div className="ncard-foot">
    {waiting&&<a href="#" className="ncard-chat" onClick={e=>{e.preventDefault();onOpenChat(item)}}>Open</a>}
    {openThread&&<a className="ncard-chat" href={item.link} target="_blank" rel="noopener noreferrer">Open thread</a>}
    {live&&canDebug&&<button type="button" className="ncard-chat ncard-debug-btn" disabled={busy||debugging} title={debugging?'A debug is running':'Send an agent to find and fix the cause'} onClick={()=>void startDebug()}>Debug</button>}
   </div>}
   {debug&&<div className="ncard-debug" role="status"><span className="ncard-debug-state">{debugWords[debug.state]||debug.state}</span>{debug.text?': '+debug.text:''}{debug.agent_key&&<> · <a href="#" className="ncard-chat" onClick={e=>{e.preventDefault();onOpenChat({...item,chat_key:debug.agent_key,chat_title:'Debug agent',run_id:undefined,kind:'debug'})}}>Debug agent's chat</a></>}{debug.review_key&&<> · <a href="#" className="ncard-chat" onClick={e=>{e.preventDefault();onOpenChat({...item,chat_key:debug.review_key,chat_title:'Reviewer',run_id:undefined,kind:'debug'})}}>Reviewer's chat</a></>}</div>}
   {error&&<div className="ncard-error" role="alert">{error}</div>}
  </div>
  {!(variant==='page'&&item.state==='archived')&&<button type="button" className="ncard-x" aria-label={variant==='popup'?'Hide':'Archive'} title={variant==='popup'?'Hide (stays in Notifications)':'Archive'} disabled={busy} onClick={()=>void cross()}><X size={14}/></button>}
 </div>;
}
