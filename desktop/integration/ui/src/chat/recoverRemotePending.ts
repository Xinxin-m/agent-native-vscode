import type {ConversationState} from '../state/conversations';
type Store={get:(id:string)=>ConversationState;patch:(id:string,changes:Partial<ConversationState>)=>ConversationState};
export async function recoverRemotePending(store:Store,id:string,flush:()=>Promise<void>){
 const current=store.get(id),pending=current.remotePending;
 if(!pending)throw Error('No unconfirmed message is available to recover.');
 const conflicts=current.conflicts.some(c=>c.recoveryId===pending.requestId)?current.conflicts:[...current.conflicts,{text:pending.text,attachments:[],at:Date.now(),recoveryId:pending.requestId,source:'Unconfirmed Remote Control message from run '+pending.runId+'. Check delivery before sending again.'}];
 const saved=store.patch(id,{conflicts});
 if(saved.storageError)throw Error('Could not save the recovered draft. The unconfirmed message is retained.');
 await flush();
 const latest=store.get(id);
 if(latest.remotePending?.requestId!==pending.requestId||latest.remotePending?.runId!==pending.runId)throw Error('The pending message changed during recovery. Review it again.');
 const cleared=store.patch(id,{remotePending:undefined});
 const restore=()=>{const now=store.get(id).remotePending;if(!now||(now.runId===pending.runId&&now.requestId===pending.requestId))store.patch(id,{remotePending:pending});};
 if(cleared.storageError){restore();throw Error('Could not finish saving the recovered draft. The unconfirmed message is retained.');}
 try{await flush()}catch(error){restore();throw error}
}
