import {describe,it,expect} from 'vitest';
import {summarizeActivity} from './activityPhase';
const e=(seq:number,event:string,data:any,ts?:string)=>({seq,event,data,ts});
const start='2026-11-14T10:00:00Z';
describe('activity summary',()=>{
 it('matches out-of-order tool results by ID and shows the remaining skill',()=>{
  const events=[e(1,'tool',{id:'a',name:'Skill',input:{skill:'review'}},start),e(2,'tool',{id:'b',name:'Edit'},start),e(3,'tool_result',{tool_use_id:'b'},start)];
  expect(summarizeActivity({status:'running',created_at:start},events,false,Date.parse(start)+60000)).toMatchObject({phase:'Running skill: review',elapsed:'1m 0s',toolCount:2});
 });
 it('does not infer an active stage from an unmatched call or an unidentified skill',()=>{
  expect(summarizeActivity({status:'running'},[e(1,'tool',{name:'Skill',input:{}})],false,0).phase).toBe('Working');
 });
 it('freezes elapsed time at completion and never counts unresolved tools as active after completion',()=>{
  const events=[e(1,'tool',{id:'a',name:'Edit'},start),e(2,'done',{status:'completed'},'2026-11-14T10:00:15Z')];
  expect(summarizeActivity({status:'completed',created_at:start},events,false,Date.parse(start)+200000)).toMatchObject({phase:'Finished',elapsed:'15s'});
 });
 it('does not invent elapsed time when a finished run lacks its end timestamp',()=>{
  expect(summarizeActivity({status:'completed',created_at:start},[],false,Date.parse(start)+200000).elapsed).toBeUndefined();
 });
 it('shows explicit thinking and waiting states',()=>{
  expect(summarizeActivity({status:'running'},[e(1,'provider_event',{event:{type:'assistant',message:{content:[{type:'thinking',thinking:'x'}]}}})],false,0).phase).toBe('Thinking');
  expect(summarizeActivity({status:'running'},[],true,0).phase).toBe('Waiting for you');
 });
});

it('uses the terminal status, not the presence of done, to stop the clock',()=>{for(const status of ['unknown','disconnected'])expect(summarizeActivity({status},[e(1,'done',{status})],false,0).finished).toBe(false);expect(summarizeActivity({status:'running'},[e(1,'done',{status:'stopped'})],false,0)).toMatchObject({finished:true,phase:'Stopped'})});

it('distinguishes streaming text, explicit thinking deltas, unknown and waiting activity',()=>{
 expect(summarizeActivity({status:'running'},[e(1,'delta',{text:'Hello'})],false,0)).toMatchObject({phase:'Responding',active:true});
 expect(summarizeActivity({status:'running'},[e(1,'provider_event',{event:{type:'stream_event',event:{type:'content_block_delta',delta:{type:'thinking_delta'}}}})],false,0).phase).toBe('Thinking');
 for(const status of ['unknown','disconnected'])expect(summarizeActivity({status},[],false,0)).toMatchObject({phase:'Activity unknown',active:false});
 expect(summarizeActivity({status:'running'},[],true,0)).toMatchObject({phase:'Waiting for you',active:false});
 expect(summarizeActivity({status:'completed'},[],false,0).active).toBe(false);
});
