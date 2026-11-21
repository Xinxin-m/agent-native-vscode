import {describe,it,expect} from 'vitest';
import {quotaHandoffNotice,showPreviousConversation} from './quotaHandoff';
import {sharedRunPatch,submittedMessages} from './sharedRuns';
describe('quota handoff',()=>{
 it('reports switching, blocked and continued truthfully',()=>{
  expect(quotaHandoffNotice({quota_failover:{status:'switching',from_account_id:'a',to_account_id:'b'}})).toContain('Switching');
  expect(quotaHandoffNotice({quota_failover:{status:'blocked',reason:'unsafe'}})).toContain('unsafe');
  expect(quotaHandoffNotice({quota_failover_from:{account_id:'a'},account_id:'b'})).toContain('b');
 });
 it('does not present the continuation directive as a user message',()=>{
  expect(submittedMessages([],[],{id:'new',message:'internal continuation',quota_failover_from:{run_id:'old'}})).toEqual([]);
 });
 it('never links native resume to its own history',()=>{
  expect(showPreviousConversation({key:'claude-s',source_key:'claude-s',quota_failover_from:{mode:'native_resume'}})).toBe(false);
  expect(showPreviousConversation({key:'claude-b',source_key:'claude-a',quota_failover_from:{mode:'linked_handoff'}})).toBe(true);
 });
 it('follows only after current terminal and retains draft',()=>{
  const state={runId:'old',text:'draft',attachments:['a']};
  expect(sharedRunPatch(state,{id:'old',status:'running'},{id:'new',key:'k'})).toEqual({});
  expect(sharedRunPatch(state,{id:'old',status:'failed'},{id:'new',key:'k'})).toEqual({runId:'new',nativeKey:'k',nativeProvider:undefined});
 });
});
