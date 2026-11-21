import {describe,it,expect,vi} from 'vitest';
import {createConversationStore} from '../state/conversations';
import {submittedMessages} from './sharedRuns';
const disk=()=>{const m=new Map<string,string>();return {getItem:(k:string)=>m.get(k)||null,setItem:(k:string,v:string)=>{m.set(k,v)}}};
describe('steering never blocks the composer',()=>{
 const handed=(text:string,draft=text)=>{const s=createConversationStore(disk());s.get('a');s.patch('a',{text});vi.useFakeTimers({now:Date.parse('2026-11-15T17:29:59Z')});const p=s.prepareSteering('a','r',5);vi.useRealTimers();s.patch('a',{text:draft});s.steeringHanded('a',p,'2026-11-15T17:30:00Z');return s.get('a')};
 it('frees the composer once the broker accepts a steer and keeps a newer draft',()=>{const v=handed('not CMX','Remote file access');expect(v.steering).toBeUndefined();expect(v.text).toBe('Remote file access');expect(v.sent[0]).toMatchObject({text:'not CMX',operation:'steer',cursor:5});expect(handed('Go').text).toBe('')});
 it('reads delivery from the journal: steering, receipt, rejection',()=>{const v=handed('Go'),run={id:'r',provider:'codex',status:'running'},now=Date.parse('2026-11-15T17:30:10Z');
  expect(submittedMessages([],v.sent,run,[],undefined,now)[0].delivery).toBe('Sent · Codex reads it when the current tool call finishes');
  expect(submittedMessages([],v.sent,run,[{seq:6,event:'steered',data:{message:'Go'}}],undefined,now)).toEqual([expect.objectContaining({receiptSeq:6})]);
  expect(submittedMessages([],v.sent,run,[{seq:6,event:'control_error',data:{action:'steer',message:'busy'}}],undefined,now)[0]).toMatchObject({delivery:'Not delivered: busy',resend:true})});
 it('offers Resend when the turn ended first, after a timeout, or after Check delivery',()=>{const v=handed('not CMX'),now=Date.parse('2026-11-15T17:30:10Z');
  expect(submittedMessages([],v.sent,{id:'r',status:'completed'},[{seq:9,event:'done',data:{}}],undefined,now)[0]).toMatchObject({resend:true,delivery:expect.stringMatching(/turn ended/)});
  expect(submittedMessages([],v.sent,{id:'r',provider:'codex',status:'running'},[],undefined,now+61000)[0].resend).toBe(true);
  expect(submittedMessages([],[{...v.sent[0],checked:true}],{id:'r',provider:'claude',status:'running'},[],undefined,now)[0].resend).toBe(true);
  expect(submittedMessages([],[{...v.sent[0],dismissed:true}],{id:'r',status:'completed'},[{seq:9,event:'done'}],undefined,now)).toEqual([])});
 it('treats the steer as delivered when the transcript has it',()=>{const v=handed('not CMX');expect(submittedMessages([{role:'user',text:'not CMX',ts:'2026-11-15T17:30:02Z'}],v.sent,{id:'r',status:'completed'},[{seq:9,event:'done'}])).toEqual([])});
});
