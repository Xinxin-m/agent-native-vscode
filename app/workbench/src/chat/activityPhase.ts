import type {RunEvent} from '../state/runs';
import {groupToolEvents} from './toolEvents';
import {runPhase} from './runPhase';

const activeStatuses=new Set(['running','starting','waiting','interrupting']);
const terminal=new Set(['completed','complete','failed','error','interrupted','cancelled','canceled','stopped']);
const time=(value:unknown)=>{if(typeof value!=='string'&&typeof value!=='number')return undefined;const n=typeof value==='number'?(value<1e12?value*1000:value):Date.parse(value);return Number.isFinite(n)?n:undefined};
export function formatElapsed(ms:number){const seconds=Math.max(0,Math.floor(ms/1000)),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;return hours?`${hours}h ${minutes}m ${rest}s`:minutes?`${minutes}m ${rest}s`:`${rest}s`}
function callPhase(data:any){
 const name=String(data?.name||'').toLowerCase(),input=data?.input||{};
 if(name==='skill'){const skill=typeof input.skill==='string'?input.skill.trim():'';return skill?`Running skill: ${skill}`:undefined}
 if(['edit','write','multiedit','apply_patch','filechange'].includes(name))return 'Editing';
 if(['bash','commandexecution','exec_command','shell'].includes(name))return 'Running command';
 if(['read','glob','grep','websearch','webfetch','web_search'].includes(name))return 'Gathering information';
 if(name==='task'||name==='agent')return 'Running subagent';
 return name?'Running tool':undefined;
}
function thinkingSignal(event:any){
 const body=event?.type==='stream_event'?event.event:event;
 return body?.type==='content_block_start'&&body.content_block?.type==='thinking'||body?.type==='content_block_delta'&&body.delta?.type==='thinking_delta'||Array.isArray(event?.message?.content)&&event.message.content.some((c:any)=>c.type==='thinking')||String(event?.method||'').startsWith('item/reasoning/');
}
function isGenerationSignal(event:any){return thinkingSignal(event)||event?.type==='assistant'||event?.type==='stream_event'&&['content_block_start','content_block_delta'].includes(event.event?.type)}
export function summarizeActivity(run:any,events:RunEvent[],awaiting:boolean,now:number){
 const rows=groupToolEvents(events),done=[...events].reverse().find(e=>e.event==='done'),status=done?.data?.status||(done&&!terminal.has(run?.status)?undefined:run?.status),finished=terminal.has(status),base=runPhase(run,events,awaiting,'Activity unknown');
 const open=rows.filter(row=>row.call&&row.results.length===0&&String(row.call.data?.id||row.call.data?.call_id||row.call.data?.tool_use_id||''));
 let phase=['unknown','disconnected'].includes(status)?'Activity unknown':base;
 if(!finished&&!awaiting&&['running','starting','waiting'].includes(status)&&base==='Thinking'){
  const latest=open.at(-1)?.call;
  if(latest)phase=callPhase(latest.data)||'Working';
  else {const signal=[...events].reverse().find(e=>['delta','tool','tool_result'].includes(e.event)||(e.event==='provider_event'&&isGenerationSignal(e.data?.event)));phase=signal?.event==='delta'?'Responding':thinkingSignal(signal?.data?.event)?'Thinking':'Working'}
 }
 const start=time(run?.created_at)??time(events.find(e=>e.event==='start')?.ts);
 const end=finished?(time(run?.finished_at)??time(done?.ts)):now;
 return {phase,active:activeStatuses.has(status)&&!awaiting,elapsed:start!==undefined&&end!==undefined&&end>=start?formatElapsed(end-start):undefined,startedAt:start,toolCount:rows.filter(row=>row.call).length,finished};
}
