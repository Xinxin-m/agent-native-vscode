import {describe,it,expect} from 'vitest';
import {quotaHandoffNotice,showPreviousConversation} from './quotaHandoff';
import {sharedRunPatch,submittedMessages} from './sharedRuns';
describe('quota handoff',()=>{
 it('reports switching, blocked and continued truthfully',()=>{
  expect(quotaHandoffNotice({quota_failover:{status:'switching',from_account_id:'a',to_account_id:'b'}})).toContain('Switching');
  expect(quotaHandoffNotice({quota_failover:{status:'blocked',reason:'unsafe'}})).toContain('unsafe');
  expect(quotaHandoffNotice({quota_failover_from:{account_id:'a'},account_id:'b'})).toContain('b');
 });
 it('marks a provider change inline in the same chat',()=>{
  expect(quotaHandoffNotice({status:'failed',provider_switch:{status:'offered',to:'codex',automatic:false,reason:'Every Claude account has reached its usage limit.'}}))
   .toBe('Every Claude account has reached its usage limit. Use Continue in Codex below to keep going in this chat.');
  expect(quotaHandoffNotice({provider_switch:{status:'offered',to:'claude',automatic:true,reason:'Every Codex account has reached its usage limit.'}})).toContain('Continuing this chat in Claude.');
  expect(quotaHandoffNotice({provider:'codex',provider_switch_from:{provider:'claude',run_id:'old'}})).toBe('Continued in Codex after every Claude account reached its usage limit.');
 });
 it('shows the user message of a provider continuation',()=>{
  expect(submittedMessages([],[],{id:'new',message:'Now the table',provider_switch_from:{run_id:'old'}}).map((m:any)=>m.text)).toEqual(['Now the table']);
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
