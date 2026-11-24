import {describe,it,expect} from 'vitest';
import {submittedMessages} from './sharedRuns';
import {pendingItems,queueLabel,withSending,withoutItem} from './runQueue';
// NAV-24: every message she sends shows at once as a sent message, in order, and once.
const run={id:'r',provider:'claude',status:'running',phase:'turn',message:'Start the audit.',created_at:'2026-11-23T20:00:00Z'};
const queueSteer=(seq:number,id:string,text:string)=>({seq,event:'queue',ts:'2026-11-23T20:05:00Z',data:{key:'k',change:{action:'steer',item_id:id,state:'steered',item:{id,text,state:'steered',created_at:'2026-11-23T20:01:00Z'}}}});
const receipt=(seq:number,text:string)=>({seq,event:'steered',ts:'2026-11-23T20:05:01Z',data:{message:text}});
describe('sent messages',()=>{
 it('a queued message shows at once as Sending, then Queued with when it goes',()=>{
  const q=withSending(null,'k','sending:1','Check the logs too');
  expect(pendingItems(q).map(i=>i.text)).toEqual(['Check the logs too']);
  expect(queueLabel(q.items[0])).toBe('Sending…');
  expect(queueLabel({id:'q1',key:'k',text:'x',state:'queued'},false,'background')).toBe('Queued · sends when the background work ends');
  expect(withoutItem(q,'sending:1')!.items).toEqual([]);
 });
 it('steer during a run: a queued message sent with Steer now stays visible until its receipt, then shows once',()=>{
  const before=submittedMessages([],[],run,[queueSteer(5,'q1','Also check B')]);
  expect(before.map(m=>[m.text,m.delivery])).toEqual([['Start the audit.',undefined],['Also check B','Sent · Claude reads it when the current tool call finishes']]);
  const after=submittedMessages([],[],run,[queueSteer(5,'q1','Also check B'),receipt(6,'Also check B')]);
  expect(after.map(m=>[m.text,m.delivery])).toEqual([['Start the audit.',undefined],['Also check B',undefined]]);
  // A receipt that lands before the queue event is still one message.
  expect(submittedMessages([],[],run,[receipt(4,'Also check B'),queueSteer(5,'q1','Also check B')]).map(m=>m.text)).toEqual(['Start the audit.','Also check B']);
 });
 it('Steer now on a queued message keeps it visible when the steer change carries only the item id',()=>{
  // Live broker shape (server started 2026-11-22): the item is listed by the enqueue event, the steer change names only its id.
  const enqueue={seq:5,event:'queue',ts:'2026-11-23T20:04:00Z',data:{key:'k',items:[{id:'q1',text:'Also check B',state:'queued',created_at:'2026-11-23T20:04:00Z'}],change:{action:'enqueue',item_id:'q1',state:'queued'}}};
  const steerNow={seq:6,event:'queue',ts:'2026-11-23T20:05:00Z',data:{key:'k',items:[],change:{action:'steer',item_id:'q1',state:'steered'}}};
  expect(submittedMessages([],[],run,[enqueue,steerNow]).map(m=>[m.text,m.delivery])).toEqual([['Start the audit.',undefined],['Also check B','Sent · Claude reads it when the current tool call finishes']]);
  expect(submittedMessages([],[],run,[enqueue,steerNow,receipt(7,'Also check B')]).map(m=>[m.text,m.delivery])).toEqual([['Start the audit.',undefined],['Also check B',undefined]]);
 });
 it('follow-up after a run: the delivered queued message shows until indexed, then never twice',()=>{
  const delivered={seq:9,event:'queue',ts:'2026-11-23T20:10:00Z',data:{key:'k',change:{action:'delivered',state:'delivered',item:{id:'q2',text:'Now write it up',created_at:'2026-11-23T20:09:00Z'}}}};
  expect(submittedMessages([],[],run,[delivered]).map(m=>m.text)).toEqual(['Start the audit.','Now write it up']);
  const indexed=[{role:'user',text:'Start the audit.',ts:'2026-11-23T20:00:01Z'},{role:'assistant',text:'Done.',ts:'2026-11-23T20:08:00Z'},{role:'user',text:'Now write it up',ts:'2026-11-23T20:10:01Z'}];
  expect(submittedMessages(indexed,[],run,[delivered])).toEqual([]);
 });
 it('order: prompt, then steers in send order, each removed only by its own transcript turn',()=>{
  const events=[queueSteer(5,'q1','first steer'),receipt(6,'first steer'),queueSteer(7,'q2','second steer'),receipt(8,'second steer')];
  expect(submittedMessages([],[],run,events).map(m=>m.text)).toEqual(['Start the audit.','first steer','second steer']);
  // The indexer now records each mid-turn message as its own user turn (queued_command).
  const indexed=[{role:'user',text:'Start the audit.',ts:'2026-11-23T20:00:01Z'},{role:'user',text:'first steer',ts:'2026-11-23T20:05:02Z'}];
  expect(submittedMessages(indexed,[],run,events).map(m=>m.text)).toEqual(['second steer']);
 });
});
