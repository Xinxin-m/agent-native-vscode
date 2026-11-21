// A message sent while a run works (steer, queued delivery) shows until the transcript has it.
// The broker's `steered` receipt can arrive minutes late; the transcript decides.
export const normalized=(text:any)=>String(text||'').replace(/\s+/g,' ').trim();
const SLACK=60_000; // clock skew between this client and a remote host's transcript
export const sentLabel=(provider?:string)=>`Sent · ${provider==='codex'?'Codex':'Claude'} reads it when the current tool call finishes`;
// Same message: by request id when both sides carry one (broker after 2026-11-15), else by text.
export function sameMessage(a:{requestId?:string;text?:string},b:{requestId?:string;text?:string}){
 return a.requestId&&b.requestId?a.requestId===b.requestId:normalized(a.text)===normalized(b.text);
}
export const isReceipt=(event:any,s:{requestId?:string;text?:string})=>event.event==='steered'&&sameMessage({requestId:event.data?.request_id,text:event.data?.message},s);
// Drops each message found in the transcript, one-to-one in order: a user turn with the same
// text stamped no earlier than the send. Without a send time, the run's start bounds it.
export function unrecorded<T extends {text:string;sentAt?:string}>(turns:any[],messages:T[],runStart:number):T[]{
 const used=new Set<number>();
 return messages.filter(m=>{
  const text=normalized(m.text),sent=Date.parse(m.sentAt||''),from=Number.isFinite(sent)?sent-SLACK:runStart-1000;
  const i=turns.findIndex((t,j)=>!used.has(j)&&t.role==='user'&&normalized(t.text)===text&&!(Date.parse(t.ts)<from));
  if(i<0)return true;used.add(i);return false;
 });
}
// One request per draft: a second Enter or click while it is in flight is ignored, and a retry
// of the same text reuses its request id, so the broker returns the existing item.
export function draftRequests(){
 const ids=new Map<string,string>(),open=new Set<string>();
 return {
  begin(text:string){const key=normalized(text);if(open.has(key))return '';open.add(key);if(!ids.has(key))ids.set(key,crypto.randomUUID());return ids.get(key)!},
  end(text:string,sent:boolean){const key=normalized(text);open.delete(key);if(sent)ids.delete(key)},
 };
}
