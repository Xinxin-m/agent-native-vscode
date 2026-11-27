import type {RunEvent} from '../state/runs';
import {groupToolEvents} from './toolEvents';
import {recordedOutputContains} from './recordedOutput';
// Reasoning the provider streamed: Claude thinking blocks or Codex reasoning summaries.
export function runThinking(events:RunEvent[],rootId?:string){
 const blocks=new Map();
 let claudeId='',anonymous=0;
 for(const entry of events){
  if(entry.event!=='provider_event')continue;
  const ev=entry.data?.event||{},params=ev.params||{},body=ev.type==='stream_event'?ev.event:ev;
  if(ev.parent_tool_use_id||(rootId&&params.threadId&&params.threadId!==rootId))continue;
  if(body?.type==='message_start')claudeId=body.message?.id||'stream-'+(++anonymous);
  if(body?.type==='content_block_delta'&&body.delta?.type==='thinking_delta'){
   const key='claude:'+claudeId+':'+(body.index||0);
   blocks.set(key,(blocks.get(key)||'')+(body.delta.thinking||''));
  }
  if(ev.type==='assistant'&&Array.isArray(ev.message?.content)){
   ev.message.content.forEach((block,index)=>{
    if(block.type==='thinking'&&block.thinking)blocks.set('claude:'+(ev.message.id||claudeId)+':'+index,block.thinking);
   });
  }
  const key='codex:'+(params.itemId||params.item?.id||'current');
  if(ev.method==='item/reasoning/summaryTextDelta'){
   const parts=blocks.get(key)||[];
   const i=params.summaryIndex||0;
   parts[i]=(parts[i]||'')+(params.delta||'');blocks.set(key,parts);
  }
  if(ev.method==='item/completed'&&params.item?.type==='reasoning'){
   const summary=(params.item.summary||[]).map(part=>typeof part==='string'?part:part?.text||'').filter(Boolean);
   if(summary.length)blocks.set(key,summary);
  }
 }
 return Array.from(blocks.values(),value=>Array.isArray(value)?value.join('\n\n'):value).filter(Boolean).join('\n\n').trim();
}
// What a run adds below the transcript. Tool calls fold into one group; the answer is never inside it.
export function runView(turns:any[],events:RunEvent[],output:string,runStart?:string,rootId?:string){
 return {tools:groupToolEvents(events),thinking:runThinking(events,rootId),answer:unrecordedOutput(turns,events,output,runStart)};
}
// Match saved text, never cut deltas at a transcript timestamp: provider timestamps and delivery times differ,
// and reasoning can be recorded while an answer is still streaming. Keep the snapshot when events are paginated.
function unrecordedOutput(turns:any[],events:RunEvent[],output:string,runStart?:string){
 if(!output||recordedOutputContains(turns,output))return '';
 const since=Date.parse(runStart||events.find(e=>e.event==='delta')?.ts||'');
 const lastUser=turns.map(t=>t.role).lastIndexOf('user');
 const replies=turns.filter((t,i)=>t.role==='assistant'&&t.channel!=='analysis'&&
  (Number.isFinite(since)?!(Date.parse(t.ts||'')<since-1000):i>lastUser));
 // Index non-whitespace characters back to their original offsets so Markdown is never reformatted.
 const chars=[...output.matchAll(/\S/g)],compact=chars.map(c=>c[0]).join('');let consumed=0;
 for(const turn of replies){
  const saved=String(turn.text||'').replace(/\s/g,'');if(!saved)continue;
  const rest=compact.slice(consumed);
  if(saved===rest)return '';
  if(rest.startsWith(saved)){consumed+=saved.length;continue;}
  // The index may already contain the completed version of the message whose deltas are still arriving.
  if(saved.startsWith(rest)&&rest)return '';
 }
 return consumed?output.slice(chars[consumed-1].index!+1):output;
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
