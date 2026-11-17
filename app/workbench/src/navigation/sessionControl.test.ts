import {describe,expect,it} from 'vitest';
import {visibleSessions,groupByNode,select,previewStops,commitStops,stopPlan,type Outcome} from './sessionControl';

const order=['a','b','c','d'];
describe('session selection',()=>{
 it('click selects one row and sets the anchor',()=>{
  const r=select(new Set(['a','b']),'a','c',order,{});
  expect([...r.selected]).toEqual(['c']);expect(r.anchor).toBe('c');
 });
 it('Cmd/Ctrl and the checkbox toggle without clearing',()=>{
  expect([...select(new Set(['a']),'a','c',order,{meta:true}).selected]).toEqual(['a','c']);
  expect([...select(new Set(['a','c']),'a','c',order,{toggle:true}).selected]).toEqual(['a']);
 });
 it('Shift selects the range from the anchor in either direction, keeping the anchor',()=>{
  const r=select(new Set(['d']),'b','d',order,{shift:true});
  expect([...r.selected].sort()).toEqual(['b','c','d']);expect(r.anchor).toBe('b');
  expect([...select(new Set(),'c','a',order,{shift:true}).selected].sort()).toEqual(['a','b','c']);
 });
 it('Shift with Cmd adds the range to the selection',()=>{
  expect([...select(new Set(['a']),'c','d',order,{shift:true,meta:true}).selected].sort()).toEqual(['a','c','d']);
 });
 it('hides app services, dead rows and stopped sessions; groups by node in node order',()=>{
  const outcomes:Record<string,Outcome>={x:{id:'x',session_id:'s1',state:'stopped',at:1}};
  const rows=visibleSessions([{id:'svc',alive:true,process_only:true},{id:'old',alive:false},{id:'x',session_id:'s1',alive:true},{id:'m',alive:true,node:'mac',last_msg_ts:5},{id:'r',alive:true,node:'sc',last_msg_ts:9},{id:'n',alive:true,node:'mac',last_msg_ts:7}],outcomes);
  expect(rows.map(r=>r.id)).toEqual(['r','n','m']);
  expect(groupByNode([{name:'mac'}],rows).map(g=>[g.node.name,g.rows.map(r=>r.id)])).toEqual([['mac',['n','m']],['sc',['r']]]);
 });
});

describe('stop flow',()=>{
 it('preview never commits; commit runs only for previewed tokens',async()=>{
  const calls:any[]=[];
  const api=async(path:string,body:any)=>{calls.push([path,body]);if(path.endsWith('/preview')){if(body.id==='bad')throw Error('Process identity is ambiguous');return {token:'t-'+body.id};}return {state:'stop_requested'};};
  const previews=await previewStops(api,[{id:'a',name:'A'},{id:'bad',name:'Bad'}]);
  expect(calls.every(([p])=>p==='/api/live/stop/preview')).toBe(true);
  expect(previews.map(p=>p.token||p.error)).toEqual(['t-a','Process identity is ambiguous']);
  const outcomes:Outcome[]=[];
  await commitStops(api,previews,o=>outcomes.push(o));
  expect(calls.filter(([p])=>p==='/api/live/stop/commit')).toEqual([['/api/live/stop/commit',{token:'t-a'}]]);
  expect(outcomes.map(o=>[o.id,o.state])).toEqual([['a','stop_requested']]);
 });
 it('counts only stoppable sessions in the plan and lists the blocked ones after them',()=>{
  const A={id:'a',name:'A'},B={id:'b',name:'B'},C={id:'c',name:'C'};
  expect(stopPlan([{session:A,token:'t'}]).title).toBe('Stop 1 session?');
  const plan=stopPlan([{session:B,error:'Process identity changed'},{session:A,token:'ta'},{session:C,token:'tc'}]);
  expect(plan.title).toBe('Stop 2 sessions?');
  expect(plan.stoppable).toEqual([A,C]);
  expect(plan.blocked).toEqual([{session:B,reason:'Process identity changed'}]);
 });
 it('says there is nothing to stop when every preview failed',()=>{
  const plan=stopPlan([{session:{id:'a'},error:'Ambiguous'},{session:{id:'b'}}]);
  expect(plan.title).toBe('Nothing to stop');
  expect(plan.stoppable).toEqual([]);
  expect(plan.blocked.map(b=>b.reason)).toEqual(['Ambiguous','Cannot verify this process']);
 });
 it('reports a failed commit as an error outcome',async()=>{
  const outcomes:Outcome[]=[];
  await commitStops(async()=>{throw Error('Stop preview expired or was already used');},[{session:{id:'a'},token:'t'}],o=>outcomes.push(o));
  expect(outcomes.at(-1)).toMatchObject({id:'a',state:'error',error:'Stop preview expired or was already used'});
 });
});
