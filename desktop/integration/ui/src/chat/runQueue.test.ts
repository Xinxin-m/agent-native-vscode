import {describe,it,expect,beforeEach} from 'vitest';
import {fetchQueue,queueAction,pendingItems,queueFromEvents,queueSupported,resetQueueSupport,queueLabel} from './runQueue';
// In-memory backend following the queue contract: items are delivered in order at each turn end.
function fakeServer(){
 let items:any[]=[],n=0;const delivered:string[]=[];
 const queue=()=>({key:'k',revision:String(n),active_run_id:'r1',held:false,items:pendingItems({key:'k',revision:'',items})});
 const reply=(body:any,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const handler=async(url:string,init?:any)=>{
  if(!url.startsWith('/api/run/queue'))return reply({error:'Not found'},404);
  if(!init?.body)return reply({ok:true,queue:queue()});
  const b=JSON.parse(init.body),item=items.find(i=>i.id===b.item_id);n++;
  if(b.action==='enqueue'){const added={id:'q'+n,key:'k',text:b.message,state:'queued'};items.push(added);return reply({ok:true,queue:queue(),item:added})}
  if(item?.state!=='queued')return reply({ok:false,code:'queue_item_resolved',error:'That message was already handled.'},409);
  if(b.action==='steer')item.state='steered';
  if(b.action==='cancel')item.state='cancelled';
  return reply({ok:true,queue:queue(),item})};
 const turnEnds=()=>{const next=items.find(i=>i.state==='queued');if(next){next.state='delivered';delivered.push(next.text)}n++;return {seq:n,event:'queue',data:{key:'k',revision:String(n),items:queue().items,change:{action:'delivered',item_id:next?.id,state:'delivered'}}}};
 return {handler,turnEnds,delivered};
}
beforeEach(()=>{resetQueueSupport();(globalThis as any).window={};});
describe('queued messages',()=>{
 it('queues, promotes, cancels, survives a reload and delivers the next item when the turn ends',async()=>{
  const server=fakeServer();(globalThis as any).fetch=server.handler;
  expect((await fetchQueue('k'))?.items).toEqual([]);expect(queueSupported()).toBe(true);
  const first=(await queueAction('enqueue',{key:'k',message:'first'}))!.item,second=(await queueAction('enqueue',{key:'k',message:'second'}))!.item;
  await queueAction('enqueue',{key:'k',message:'third'});
  const promoted=await queueAction('steer',{key:'k',item_id:second.id});expect(promoted!.item.state).toBe('steered');
  const cancelled=await queueAction('cancel',{key:'k',item_id:first.id});expect(cancelled!.item.text).toBe('first');
  await expect(queueAction('steer',{key:'k',item_id:first.id})).rejects.toMatchObject({code:'queue_item_resolved'});
  resetQueueSupport();const reloaded=await fetchQueue('k');expect(reloaded!.items.map(i=>i.text)).toEqual(['third']);
  const event=server.turnEnds();expect(server.delivered).toEqual(['third']);
  expect(queueFromEvents([event as any],'k')!.items).toEqual([]);
 });
 it('falls back quietly when the server has no queue routes',async()=>{
  (globalThis as any).fetch=async()=>new Response('<html>404</html>',{status:404});
  expect(await fetchQueue('k')).toBeNull();expect(queueSupported()).toBe(false);
  expect(await queueAction('enqueue',{key:'k',message:'x'})).toBeNull();
 });
 it('labels each pending state',()=>{
  expect(queueLabel({id:'a',key:'k',text:'x',state:'queued'})).toBe('Queued · sends when this turn ends');
  expect(queueLabel({id:'a',key:'k',text:'x',state:'failed',error:'provider exited'})).toBe('Not delivered: provider exited');
 });
});
