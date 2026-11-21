import {describe,it,expect} from 'vitest';
import {runView,runThinking} from './runView';
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
});
it('collects streamed thinking from Claude and Codex events',()=>{
 expect(runThinking([e(1,'provider_event',{event:{type:'assistant',message:{content:[{type:'thinking',thinking:'Plan it.'},{type:'text',text:'Hi'}]}}})])).toBe('Plan it.');
 expect(runThinking([e(1,'provider_event',{event:{type:'stream_event',event:{type:'content_block_delta',delta:{type:'thinking_delta',thinking:'Plan '}}}}),e(2,'provider_event',{event:{type:'stream_event',event:{type:'content_block_delta',delta:{type:'thinking_delta',thinking:'it.'}}}})])).toBe('Plan it.');
 expect(runThinking([e(1,'provider_event',{event:{method:'item/completed',params:{item:{type:'reasoning',summary:['Check the logs.']}}}})])).toBe('Check the logs.');
 expect(runThinking([e(1,'delta',{text:'Hi'})])).toBe('');
});
