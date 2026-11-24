import {terminal} from '../state/runs';
import {isReceipt,sameMessage,sentLabel,unrecorded} from './pendingMessages';
// Patching execution only keeps a local unsent draft, attachments and retry ID.
export function sharedRunPatch(state:any,current:any,next:any){
 if(!next?.id||next.id===state.runId||state.forkSourceKey||(state.runId&&(current?.id!==state.runId||!terminal(current?.status))))return {};
 return {runId:next.id,...(next.key?{nativeKey:next.key,nativeProvider:next.provider}:{}),...(next.account_id?{nativeAccount:next.account_id}:{})};
}
// A steer handed to the broker resolves from the journal (receipt, rejection, or the run
// ending first) or from the transcript (submittedMessages). Unresolved steers time out into Resend.
function steerDelivery(s:any,run:any,events:any[],claimed:Set<number>,now:number){
 const after=events.filter(e=>e.seq>(s.cursor??-1));
 const receipt=after.find(e=>!claimed.has(e.seq)&&isReceipt(e,s));
 if(receipt){claimed.add(receipt.seq);return {...s,receiptSeq:receipt.seq}}
 const rejected=after.find(e=>e.event==='control_error'&&e.data?.action==='steer'&&!claimed.has(e.seq));
 if(rejected){claimed.add(rejected.seq);return {...s,delivery:'Not delivered: '+(rejected.data.message||'the agent rejected it'),resend:true}}
 if(after.some(e=>e.event==='done')||terminal(run?.status))return {...s,delivery:'Not delivered: the turn ended before the agent read it',resend:true};
 if(s.checked||(run?.provider!=='claude'&&now-Date.parse(s.ts)>60000))return {...s,delivery:'No receipt yet. Resend only if the agent missed it',resend:true};
 return {...s,delivery:sentLabel(run?.provider)};
}
// Messages of this run not yet in the transcript, in send order, each shown once.
export function submittedMessages(turns:any[],sent:any[],run:any,events:any[]=[],pending?:any,now=Date.now()){
 const claimed=new Set<number>(),queued=new Map<string,any>();
 const messages=sent.filter(s=>s.runId===run?.id&&!s.dismissed).map(s=>s.operation==='steer'&&s.requestId&&s.receiptSeq===undefined?steerDelivery(s,run,events,claimed,now):s);
 for(const event of events){
  if(event.event==='steered'&&event.data?.message&&!claimed.has(event.seq)&&!messages.some(s=>s.receiptSeq===event.seq)){
   // The receipt of a queued message sent with Steer now confirms that message, not a new one.
   const steered=messages.find(s=>s.queueSteer&&s.receiptSeq===undefined&&sameMessage(s,{text:event.data.message}));
   if(steered){steered.receiptSeq=event.seq;delete steered.delivery}
   else messages.push({text:event.data.message,runId:run.id,ts:event.ts,operation:'steer',receiptSeq:event.seq,requestId:event.data.request_id});
  }
  // A queued message the broker delivered at a turn end, or steered now, is a sent message until indexed.
  const change=event.event==='queue'&&event.data?.change,steer=change&&(change.state==='steered'||change.action==='steer'),item=change&&(steer||change.state==='delivered'||change.action==='delivered')&&(change.item||queued.get(change.item_id));
  // The broker before 2026-11-23 16:34 sends a steer change without its item; the item is in an earlier queue list.
  for(const queuedItem of (event.event==='queue'&&event.data?.items)||[])queued.set(queuedItem.id,queuedItem);
  const early=item&&steer&&messages.find(s=>s.operation==='steer'&&s.receiptSeq!==undefined&&!s.requestId&&!s.queueItem&&sameMessage(s,{text:item.text}));
  if(early)early.queueItem=item.id;
  else if(item?.text&&!messages.some(s=>s.queueItem===item.id))messages.push({text:item.text,runId:run.id,ts:event.ts,sentAt:item.created_at,operation:'queue',queueItem:item.id,...(steer?{queueSteer:true,delivery:sentLabel(run?.provider)}:{queueSeq:event.seq})});
 }
 const acknowledged=pending&&messages.some(s=>s.receiptSeq>(pending.cursor??-1)&&sameMessage(s,{requestId:pending.requestId,text:pending.message}));
 if(pending?.runId===run?.id&&!acknowledged)messages.push({text:pending.message,runId:run.id,operation:'steer',sentAt:pending.sentAt,delivery:pending.phase==='sending'?'Sending…':pending.phase==='unconfirmed'?'Confirmation pending':sentLabel(run?.provider)});
 if(run?.message&&!run?.quota_failover_from&&!['compact','remote_control'].includes(run.operation)&&!messages.some(s=>s.operation!=='steer'&&String(s.text||'').trim()===String(run.message).trim()))messages.unshift({text:run.message,runId:run.id,ts:run.created_at});
 return unrecorded(turns,messages,Date.parse(run?.created_at||''));
}
export function nativeRunError(error:any){
 const raw=typeof error==='string'?error:JSON.stringify(error);
 return /already has an active writer/i.test(raw||'')?'The native Codex client still owns this session. Release it there before retrying, or create an explicit fork. You can recover the submitted message below.':raw;
}
