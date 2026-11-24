import {useState} from 'react';
import {X,Bug,MessageSquare,ExternalLink} from 'lucide-react';
import {act,isWaiting,isFailed,debugActive,type Item} from './feed';
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
 const debug=item.debug,debugging=!!debug&&debugActive(item),canDebug=variant==='page'&&item.debuggable===true;
 async function startDebug(){
  if(busy||debugging)return;
  setBusy(true);setError('');
  try{await act(item,'debug')}catch(e:any){setError(e.status===409?'A debug is already running for this item.':e.message||'Debug did not start')}finally{setBusy(false)}
 }
 const state=isWaiting(item)?'wait':isFailed(item)?'fail':'';
 // The server titles some notices just "Notice" and puts the message in the body.
 const generic=item.title==='Notice'&&!!item.body,heading=generic?item.body:item.title;
 const reason=!item.chat_title&&!generic&&item.kind!=='approval'&&item.body?first(item.body):'';
 const meta=[providerOf(item),ago(item.at),item.chat_title||reason].filter(Boolean).join(' · ');
 const command=item.kind==='approval'&&item.body?first(item.body):'';
 const answer=item.answer&&(item.answer.choice||item.answer.text);
 const openChat=!!item.chat_key||item.kind==='waiting',thread=item.kind!=='waiting'&&!!item.link;
 const icon=(label:string,onClick:()=>void,child:any,disabled=false,cls='')=><button type="button" className={'ncard-icon '+cls} aria-label={label} title={label} disabled={disabled} onClick={onClick}>{child}</button>;
 return <div className={'ncard'+(state?' ncard-'+state:'')+(variant==='popup'?' ncard-popup':'')+(item.state!=='open'?' ncard-done':'')} role="group" aria-label={(state==='wait'?'Waiting for your decision: ':state==='fail'?'Failed: ':'')+heading} data-id={item.id} data-kind={item.kind}
  onKeyDown={e=>{if(e.key==='Escape'&&variant==='popup'){e.stopPropagation();onHide?.(item)}}}>
  <div className="ncard-top">
   <div className="ncard-title">{heading}</div>
   <div className="ncard-icons">
    {openChat&&icon(item.chat_title?'Open chat: '+item.chat_title:item.chat_key?'Open chat':'Open Sessions',()=>onOpenChat(item),<MessageSquare size={16}/>)}
    {thread&&<a className="ncard-icon ncard-thread" href={item.link} target="_blank" rel="noopener noreferrer" aria-label="Open thread" title="Open thread"><ExternalLink size={16}/></a>}
    {live&&canDebug&&icon(debugging?'A debug is running':'Debug: send an agent to find and fix the cause',()=>void startDebug(),<Bug size={16}/>,busy||debugging,'ncard-debug-btn')}
    {!(variant==='page'&&item.state==='archived')&&icon(variant==='popup'?'Hide (stays in Notifications)':'Archive',()=>void cross(),<X size={16}/>,busy,'ncard-x')}
   </div>
  </div>
  {meta&&<div className="ncard-meta">{meta}</div>}
  {command&&<code className="ncard-code">{command}</code>}
  {live&&item.options.length>0&&<div className="ncard-options">{item.options.map(o=><button key={o} type="button" className="ncard-btn" disabled={busy} onClick={()=>void send(o)}>{o}</button>)}</div>}
  {live&&item.allow_text&&<div className="ncard-field"><input value={text} disabled={busy} placeholder={item.options.length?'Or type an answer':'Type an answer'} aria-label={'Answer: '+heading} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/></div>}
  {item.state!=='open'&&answer&&<div className="ncard-answered">You chose {item.answer!.choice||item.answer!.text}{item.answer!.at?' · '+ago(item.answer!.at):''}</div>}
  {debug&&<div className="ncard-debug" role="status">{debugging&&<i className="mk work" aria-hidden="true"/>}<span><b>{debugWords[debug.state]||debug.state}</b>{debug.text?': '+debug.text:''}{debug.agent_key&&<> · <button type="button" className="ncard-inline" onClick={()=>onOpenChat({...item,chat_key:debug.agent_key,chat_title:'Debug agent',run_id:undefined,kind:'debug'})}>Debug agent's chat</button></>}{debug.review_key&&<> · <button type="button" className="ncard-inline" onClick={()=>onOpenChat({...item,chat_key:debug.review_key,chat_title:'Reviewer',run_id:undefined,kind:'debug'})}>Reviewer's chat</button></>}</span></div>}
  {error&&<div className="ncard-error" role="alert">{error}</div>}
 </div>;
}
