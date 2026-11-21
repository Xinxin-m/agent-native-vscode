import {describe,it,expect} from 'vitest';
import {chimeForAttention,soundEnabled} from './approvalSound';
import {openDecisions,decisionLine} from '../chat/openDecisions';
const item=(request_id:string,human_required=true)=>({run_id:'r1',request_id,kind:'approval',payload:{human_required}});
describe('approval chime',()=>{
 it('plays once per request that needs the user, never for auto-approved ones',()=>{
  let plays=0;const play=()=>{plays++};
  expect(chimeForAttention([item('a',false)],true,play)).toBe(false);
  expect(chimeForAttention([item('a')],true,play)).toBe(true);
  expect(chimeForAttention([item('a')],true,play)).toBe(false);
  expect(chimeForAttention([item('a'),item('b')],true,play)).toBe(true);
  expect(plays).toBe(2);
 });
 it('stays silent when the setting is off and does not chime later for the same request',()=>{
  let plays=0;const play=()=>{plays++};
  chimeForAttention([item('c')],false,play);chimeForAttention([item('c')],true,play);
  expect(plays).toBe(0);
 });
});
describe('one chime source',()=>{
 it('leaves the sound to the watcher while it runs and chimes itself otherwise',()=>{
  expect(soundEnabled({attention_sound:true},{watcher_sound:true})).toBe(false);
  expect(soundEnabled({attention_sound:true},{watcher_sound:false})).toBe(true);
  expect(soundEnabled({attention_sound:true},{})).toBe(true);
  expect(soundEnabled({attention_sound:false},{watcher_sound:false})).toBe(false);
 });
});
describe('open decisions',()=>{
 it('lists only open items and reads them once per run',async()=>{
  let calls=0;const request=async()=>{calls++;return {ok:true,items:[{seq:1,kind:'approval',resolution:'auto_accepted',open:false},{seq:2,kind:'question',resolution:'deferred',open:true,questions:[{question:'Which branch?'}]}]}};
  const items=await openDecisions('run-a',request as any);await openDecisions('run-a',request as any);
  expect(items.map(decisionLine)).toEqual(['Decided without asking: Which branch?']);expect(calls).toBe(1);
 });
 it('goes quiet on a server without the route',async()=>{
  let calls=0;const missing=async()=>{calls++;throw Object.assign(Error('HTTP 404'),{status:404})};
  expect(await openDecisions('run-b',missing as any)).toEqual([]);expect(await openDecisions('run-c',missing as any)).toEqual([]);expect(calls).toBe(1);
 });
});
