import type {ConversationState} from '../state/conversations';
type Store={get:(id:string)=>ConversationState;patch:(id:string,changes:Partial<ConversationState>)=>ConversationState};
type Pending=NonNullable<ConversationState['remotePending']>;
const same=(a:Pending|undefined,b:Pending)=>a?.runId===b.runId&&a?.requestId===b.requestId;
export async function sendRemoteFollowup(store:Store,id:string,runId:string,text:string,consumeDraft:boolean,control:(runId:string,action:string,body:any)=>Promise<any>,flush:()=>Promise<void>=async()=>{}){
 const current=store.get(id),pending=current.remotePending||{runId,requestId:crypto.randomUUID(),text};
 if(pending.runId!==runId)throw Error('The previous message belongs to a different remote connection. Your draft is retained.');
 const saved=store.patch(id,{remotePending:pending});
 if(saved.storageError)throw Error('Could not save the message for delivery. Your draft is retained.');
 await flush();
 if(!same(store.get(id).remotePending,pending))throw Error('The pending message changed before delivery. Review it again.');
 await control(pending.runId,'message',{message:pending.text,request_id:pending.requestId});
 const latest=store.get(id);
 // A receipt belongs only to the request it acknowledged, never to a newer one.
 if(!same(latest.remotePending,pending))return;
 const cleared=store.patch(id,{remotePending:undefined,error:'',...(consumeDraft&&latest.text===pending.text?{text:''}:{})});
 try{
  if(cleared.storageError)throw Error('Could not save the delivery receipt.');
  await flush();
 }catch(error){
  const now=store.get(id);
  if(!now.remotePending||same(now.remotePending,pending)){
   store.patch(id,{remotePending:pending,...(now.revision===cleared.revision&&now.text===cleared.text?{text:latest.text}:{})});
   try{await flush()}catch{/* The recoverable in-memory draft remains visible. */}
  }
  throw error;
 }
}
