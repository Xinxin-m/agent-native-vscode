import {terminal} from '../state/runs';
// Patching execution only keeps a local unsent draft, attachments and retry ID.
export function sharedRunPatch(state:any,current:any,next:any){
 if(!next?.id||next.id===state.runId||state.forkSourceKey||(state.runId&&(current?.id!==state.runId||!terminal(current?.status))))return {};
 return {runId:next.id,...(next.key?{nativeKey:next.key,nativeProvider:next.provider}:{}),...(next.account_id?{nativeAccount:next.account_id}:{})};
}
export function submittedMessages(turns:any[],sent:any[],run:any,events:any[]=[],pending?:any){
 const messages=sent.filter(s=>s.runId===run?.id);
 for(const event of events)if(event.event==='steered'&&event.data?.message&&!messages.some(s=>s.receiptSeq===event.seq))messages.push({text:event.data.message,runId:run.id,ts:event.ts,operation:'steer',receiptSeq:event.seq});
 const acknowledged=pending&&messages.some(s=>s.receiptSeq>(pending.cursor??-1)&&s.text.trim()===pending.message.trim());
 if(pending?.runId===run?.id&&!acknowledged)messages.push({text:pending.message,runId:run.id,operation:'steer',delivery:pending.phase==='sending'?'Sending…':pending.phase==='unconfirmed'?'Confirmation pending':'Sent · awaiting agent'});
 if(run?.message&&!['compact','remote_control'].includes(run.operation)&&!messages.some(s=>s.operation!=='steer'&&s.text===run.message))messages.unshift({text:run.message,runId:run.id,ts:run.created_at});
 // Match each indexed message once, preserving repeated instructions.
 const matched=new Set<number>(),started=Date.parse(run?.created_at||'');
 return messages.filter(s=>{
  if(s.delivery)return true;
  const index=turns.findIndex((t,i)=>!matched.has(i)&&t.role==='user'&&String(t.text).trim()===s.text.trim()&&!(Number.isFinite(started)&&Date.parse(t.ts)<started-1000));
  if(index<0)return true;matched.add(index);return false;
 });
}
export function nativeRunError(error:any){
 const raw=typeof error==='string'?error:JSON.stringify(error);
 return /already has an active writer/i.test(raw||'')?'The native Codex client still owns this session. Release it there before retrying, or create an explicit fork. You can recover the submitted message below.':raw;
}
