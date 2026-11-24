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
 return {tools:groupToolEvents(events),thinking:runThinking(events),answer:unrecordedOutput(turns,events,output)};
}
// A steered or queued run keeps one output across several turns. The transcript shows the replies it has indexed, so
// below it goes only the text streamed after its newest reply; the whole output when the loaded deltas do not reach back.
function unrecordedOutput(turns:any[],events:RunEvent[],output:string){
 if(!output||recordedOutputContains(turns,output))return '';
 const recorded=Date.parse(turns.filter(t=>t.role==='assistant').at(-1)?.ts||''),deltas=events.filter(e=>e.event==='delta');
 if(!(Date.parse(deltas[0]?.ts||'')<=recorded))return output;
 return deltas.filter(e=>!(Date.parse(e.ts||'')<=recorded)).map(e=>e.data?.text||'').join('');
}
// Where a sent prompt sits in the run's event order: the receipt that shows the agent read it, the queue event that
// delivered it at a turn end, or the start for the message that began the run. Not read yet: after all text so far.
const promptSeq=(s:any):number=>s.receiptSeq??s.queueSeq??(['steer','queue'].includes(s.operation)?Infinity:-1);
// The live answer as text and her prompts in event order, so a steered prompt sits where the indexed transcript will
// put it. Null when the streamed deltas do not add up to the shown answer (events cut off, or a different final text).
export function liveSegments(events:RunEvent[],answer:string,prompts:any[]){
 if(!answer)return null;
 // The answer is the text of the newest deltas: the whole run, or only what the transcript has not recorded yet.
 const all=events.filter(e=>e.event==='delta');let from=all.length,length=0;
 while(from>0&&length<answer.length)length+=String(all[--from].data?.text||'').length;
 const deltas=all.slice(from);if(deltas.map(e=>e.data?.text||'').join('')!==answer)return null;
 const order=prompts.map((p,i)=>({i,seq:promptSeq(p)})).sort((a,b)=>a.seq-b.seq||a.i-b.i);
 const parts:({text:string}|{prompt:number})[]=[];let next=0,text='';
 const flush=(seq:number)=>{while(next<order.length&&order[next].seq<seq){if(text)parts.push({text});text='';parts.push({prompt:order[next++].i})}};
 for(const d of deltas){flush(d.seq);text+=d.data?.text||''}
 flush(Infinity);if(text)parts.push({text});
 while(next<order.length)parts.push({prompt:order[next++].i});
 return parts;
}
