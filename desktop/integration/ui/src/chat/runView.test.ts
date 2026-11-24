import {describe,it,expect} from 'vitest';
import {runView,runThinking,liveSegments} from './runView';
import {submittedMessages} from './sharedRuns';
import {runOutput} from '../state/runs';
const e=(seq:number,event:string,data:any)=>({seq,event,data});
const answerOf=(events:any[],turns:any[]=[{role:'user',text:'go'}])=>runView(turns,events,runOutput({id:'r',run:{status:'completed'},events,cursor:0} as any));
describe('final answer stays outside the folded tool group',()=>{
 it('shows the answer when the run ends with a tool group followed by the answer',()=>{
  const view=answerOf([e(1,'tool',{id:'a',name:'Bash'}),e(2,'tool_result',{tool_use_id:'a'}),e(3,'tool',{id:'b',name:'Read'}),e(4,'tool_result',{tool_use_id:'b'}),e(5,'delta',{text:'The answer.'}),e(6,'done',{status:'completed',text:'The answer.'})]);
  expect(view.tools).toHaveLength(2);expect(view.answer).toBe('The answer.');
  expect(view.tools.flatMap(r=>[r.call,...r.results]).some(x=>x?.event==='delta')).toBe(false);
 });
 it('shows the answer when trailing tool output follows it',()=>{
  const view=answerOf([e(1,'delta',{text:'The answer.'}),e(2,'tool',{id:'a',name:'Bash'}),e(3,'tool_result',{tool_use_id:'a'}),e(4,'done',{status:'completed'})]);
  expect(view.tools).toHaveLength(1);expect(view.answer).toBe('The answer.');
 });
 it('leaves the answer to the transcript once it is recorded there',()=>{
  const turns=[{role:'user',text:'go'},{role:'assistant',text:'The answer.'}];
  expect(answerOf([e(1,'tool',{id:'a',name:'Bash'}),e(2,'tool_result',{tool_use_id:'a'}),e(3,'delta',{text:'The answer.'}),e(4,'done',{status:'completed'})],turns).answer).toBe('');
 });
 it('shows only the text streamed after the newest recorded reply when one run spans several turns',()=>{
  // A steered run keeps one output across turns; the transcript already shows the replies it has indexed.
  const t=(m:number)=>`2026-11-23T10:0${m}:00.000Z`,d=(seq:number,text:string,m:number)=>({seq,event:'delta',data:{text},ts:t(m)});
  const events=[d(1,'First reply.',1),e(2,'steered',{message:'Also B'}),d(3,'Second ',4),d(4,'reply.',5)];
  const turns=[{role:'user',text:'go',ts:t(0)},{role:'assistant',text:'First reply.',ts:t(2)},{role:'user',text:'Also B',ts:t(3)}];
  const output='First reply.Second reply.';
  expect(runView(turns,events,output).answer).toBe('Second reply.');
  expect(liveSegments(events,'Second reply.',[])).toEqual([{text:'Second reply.'}]);
  expect(runView(turns.slice(0,1),events,output).answer).toBe(output);
 });
});
it('collects streamed thinking from Claude and Codex events',()=>{
 expect(runThinking([e(1,'provider_event',{event:{type:'assistant',message:{content:[{type:'thinking',thinking:'Plan it.'},{type:'text',text:'Hi'}]}}})])).toBe('Plan it.');
 expect(runThinking([e(1,'provider_event',{event:{type:'stream_event',event:{type:'content_block_delta',delta:{type:'thinking_delta',thinking:'Plan '}}}}),e(2,'provider_event',{event:{type:'stream_event',event:{type:'content_block_delta',delta:{type:'thinking_delta',thinking:'it.'}}}})])).toBe('Plan it.');
 expect(runThinking([e(1,'provider_event',{event:{method:'item/completed',params:{item:{type:'reasoning',summary:['Check the logs.']}}}})])).toBe('Check the logs.');
 expect(runThinking([e(1,'delta',{text:'Hi'})])).toBe('');
});
describe('live answer split at each steered prompt',()=>{
 const run={id:'r',provider:'claude',status:'running',message:'Audit the table.',created_at:'2026-11-23T10:00:00Z'};
 const queue=(seq:number,change:any,items:any[])=>e(seq,'queue',{key:'k',items,change});
 const item={id:'q1',text:'Also check B',state:'queued'};
 const enqueue=queue(2,{action:'enqueue',item_id:'q1',state:'queued'},[item]);
 const oldSteer=queue(3,{action:'steer',item_id:'q1',state:'steered'},[]),newSteer=queue(3,{action:'steer',item_id:'q1',state:'steered',item},[]);
 const layout=(events:any[])=>{const submitted=submittedMessages([],[],run,events),answer=runOutput({id:'r',run,events,cursor:0} as any);
  return liveSegments(events,answer,submitted)?.map(p=>'text' in p?'T:'+p.text:'P:'+submitted[p.prompt].text)};
 for(const [shape,steer] of [['old broker (id only)',oldSteer],['new broker (item)',newSteer]] as const){
  it(`${shape}: before Claude reads it, the steered prompt follows all text so far`,()=>{
   expect(layout([e(1,'delta',{text:'Before. '}),enqueue,steer,e(4,'delta',{text:'Still before. '})])).toEqual(['P:Audit the table.','T:Before. Still before. ','P:Also check B']);
  });
  it(`${shape}: after the receipt, text streamed later goes below it`,()=>{
   expect(layout([e(1,'delta',{text:'Before. '}),enqueue,steer,e(4,'delta',{text:'Still before. '}),e(5,'steered',{message:'Also check B'}),e(6,'delta',{text:'\n\nAfter.'})])).toEqual(['P:Audit the table.','T:Before. Still before. ','P:Also check B','T:\n\nAfter.']);
  });
 }
 it('two steers in one turn each sit at their own receipt',()=>{
  const two={id:'q2',text:'And C',state:'queued'};
  expect(layout([e(1,'delta',{text:'One. '}),enqueue,newSteer,e(4,'steered',{message:'Also check B'}),e(5,'delta',{text:'Two. '}),queue(6,{action:'enqueue',item_id:'q2',state:'queued'},[two]),queue(7,{action:'steer',item_id:'q2',state:'steered'},[]),e(8,'steered',{message:'And C'}),e(9,'delta',{text:'Three.'})]))
   .toEqual(['P:Audit the table.','T:One. ','P:Also check B','T:Two. ','P:And C','T:Three.']);
 });
 it('keeps one block when the streamed text does not add up to the shown answer',()=>{
  expect(liveSegments([e(1,'delta',{text:'Part'}),e(2,'done',{text:'Different final text'})],'Different final text',[{text:'x'}])).toBeNull();
 });
});
