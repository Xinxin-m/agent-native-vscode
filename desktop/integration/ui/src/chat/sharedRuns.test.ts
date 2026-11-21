import {describe,it,expect} from 'vitest';
import {sharedRunPatch,submittedMessages,nativeRunError} from './sharedRuns';
import {createConversationStore} from '../state/conversations';
describe('shared run following',()=>{
 it('adopts a new run after a terminal run while retaining unsent drafts and retries',()=>{
  const s=createConversationStore();s.get('x',{nativeKey:'native',runId:'old',text:'unsent'});s.prepare('x',{message:'unsent'});
  const next={id:'next',key:'native',message:'From VS Code',status:'running'};
  s.patch('x',sharedRunPatch(s.get('x'),{id:'old',status:'completed'},next));
  expect(s.get('x').runId).toBe('next');expect(s.get('x').text).toBe('unsent');expect(s.get('x').pending?.payload.message).toBe('unsent');
  expect(submittedMessages([],[],next)[0].text).toBe('From VS Code');
 });
 it('does not replace an active run, same run, or unsubmitted fork',()=>{
  expect(sharedRunPatch({runId:'old'},{id:'old',status:'running'},{id:'new'})).toEqual({});
  expect(sharedRunPatch({runId:'old'},{id:'old',status:'completed'},{id:'old'})).toEqual({});
  expect(sharedRunPatch({runId:'just-submitted'},{id:'old',status:'completed'},{id:'stale'})).toEqual({});
  expect(sharedRunPatch({forkSourceKey:'source'},{},{id:'new'})).toEqual({});
 });
 it('deduplicates submitted prompt against saved transcript and local sends',()=>{
  const r={id:'r',message:'hello'};
  expect(submittedMessages([{role:'user',text:'hello'}],[],r)).toEqual([]);
  expect(submittedMessages([],[{runId:'r',text:'hello'}],r)).toHaveLength(1);
 });
 it('shows a whitespace-normalized broker receipt only once before indexing',()=>{
  const run={id:'r',message:'hello',created_at:'2026-11-15T09:00:00Z'};
  const sent=[{runId:'r',text:'  hello  ',ts:'2026-11-15T09:00:01Z'}];
  expect(submittedMessages([],sent,run)).toHaveLength(1);
  expect(submittedMessages([{role:'user',text:'hello',ts:'2026-11-15T09:00:02Z'}],sent,run)).toHaveLength(0);
 });
 it('explains native writer collisions while raw error remains available',()=>{
  expect(nativeRunError('thread already has an active writer')).toContain('native Codex client');
  expect(nativeRunError('Something else')).toBe('Something else');
 });
});
