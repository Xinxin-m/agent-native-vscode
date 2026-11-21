import type {RunEvent} from '../state/runs';
import {groupToolEvents} from './toolEvents';
import {recordedOutputContains} from './recordedOutput';
// Reasoning the provider streamed: Claude thinking blocks or Codex reasoning summaries.
export function runThinking(events:RunEvent[]){
 const full:string[]=[],partial:string[]=[];
 for(const e of events){
  if(e.event!=='provider_event')continue;
  const ev=e.data?.event||{},body=ev.type==='stream_event'?ev.event:ev;
  if(Array.isArray(ev.message?.content))for(const c of ev.message.content)if(c?.type==='thinking'&&c.thinking)full.push(String(c.thinking));
  if(body?.type==='content_block_delta'&&body.delta?.type==='thinking_delta')partial.push(String(body.delta.thinking||''));
  if(ev.method==='item/reasoning/summaryTextDelta')partial.push(String(ev.params?.delta||''));
  if(ev.method==='item/completed'&&ev.params?.item?.type==='reasoning')full.push(...(ev.params.item.summary||[]).map((s:any)=>String(s?.text??s)));
 }
 return (full.length?full.join('\n\n'):partial.join('')).trim();
}
// What a run adds below the transcript. Tool calls fold into one group; the answer is never inside it.
export function runView(turns:any[],events:RunEvent[],output:string){
 return {tools:groupToolEvents(events),thinking:runThinking(events),answer:output&&!recordedOutputContains(turns,output)?output:''};
}
