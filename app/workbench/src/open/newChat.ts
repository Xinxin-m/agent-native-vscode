import type {Resource} from '../state/types';
import type {ConversationState} from '../state/conversations';

// A plain new chat that has not started a run. "New chat" focuses it in the current pane instead of opening another.
export const isPlainNewChat=(r:Resource)=>r.kind==='chat'&&!!r.draftId&&!r.key&&!r.runId&&Object.keys(r.meta||{}).every(k=>k==='source');
export const hasRun=(state:Pick<ConversationState,'runId'|'nativeKey'|'sent'|'pending'>)=>Boolean(state.runId||state.nativeKey||state.pending||state.sent.length);
export function unusedNewChat<T extends {params?:any}=any>(panels:readonly T[],stateOf:(r:Resource)=>Pick<ConversationState,'runId'|'nativeKey'|'sent'|'pending'>){
  return panels.find(p=>{const r:Resource|undefined=p.params?.resource;return !!r&&isPlainNewChat(r)&&!hasRun(stateOf(r));});
}
