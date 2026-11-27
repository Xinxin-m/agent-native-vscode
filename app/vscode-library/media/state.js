"use strict";
(function(root){
 const normalized=text=>String(text||'').replace(/\s+/g,' ').trim();
 function appendRecovery(current,message){if(!message||current===message)return current;return current?current+'\n\n'+message:message;}
 // Consume transcript turns once, bounded by the send time so an old identical prompt cannot hide a new one.
 function unrecorded(turns,messages,runStart){const used=new Set();return messages.filter(message=>{const sent=Date.parse(message.sentAt||''),from=Number.isFinite(sent)?sent-60000:runStart-1000;const i=turns.findIndex((turn,j)=>!used.has(j)&&turn.role==='user'&&(normalized(turn.text)===normalized(message.text)||(message.claudeRunPrompt&&normalized(String(turn.text||'').replace(/^\s*\/compact[^\S\r\n]*\r?\n/,''))===normalized(message.text)))&&!(Date.parse(turn.ts)<from));if(i<0)return true;used.add(i);return false;});}
 // What the run received after its prompt, in order, one entry per message: direct steers, queued
 // messages sent with Steer now (queue event and receipt are one message) and queued messages
 // delivered at a turn end. Same rule as web/sent-echo.js.
 function sentFromEvents(events){
  const out=[],delivered=new Set(),listed=new Map();
  // A steer change from the broker before 2026-11-23 16:34 names only the item id; an earlier queue list has the item.
  for(const e of events||[]){const change=e.event==='queue'&&e.data?.change,item=change&&(change.item||listed.get(change.item_id));
   if(e.event==='queue')for(const listedItem of e.data?.items||[])listed.set(listedItem.id,listedItem);
   if(e.event==='steered'&&e.data?.message){const queued=out.find(o=>o.queued&&!o.receipt&&normalized(o.text)===normalized(e.data.message));if(queued)queued.receipt=true;else out.push({text:e.data.message,sentAt:e.ts||e.data.at,receipt:true,direct:Boolean(e.data.request_id)});}
   else if(item?.text&&(change.state==='steered'||change.action==='steer')){const early=out.find(o=>o.receipt&&!o.direct&&!o.queued&&normalized(o.text)===normalized(item.text));if(early)early.queued=true;else out.push({text:item.text,sentAt:item.updated_at||e.ts,queued:true});}
   else if(item?.text&&(change.state==='delivered'||change.action==='delivered')&&!delivered.has(item.id)){delivered.add(item.id);out.push({text:item.text,sentAt:item.updated_at||e.ts});}
  }
  return out.map(({text,sentAt})=>({text,sentAt}));
 }
 // One status per queued message: Sending, Queued (with when it goes) or Not delivered.
 function queueLabel(item,held,phase){
  if(item.state==='failed')return 'Not delivered'+(item.error?': '+item.error:'');
  if(item.state==='delivering'||item.state==='sending')return 'Sending…';
  return held?'Queued · no agent is running':phase==='background'?'Queued · sends when the background work ends':'Queued · sends when this turn ends';
 }
 function historyTurns(state){
  const rs=state.run,events=rs?.events||[],turns=[...(state.chat?.turns||[])],run=rs?.run||{};
  const messages=[];
  if(run.message)messages.push({text:run.message,sentAt:run.created_at||run.started_at,claudeRunPrompt:run.provider==='claude'});
  messages.push(...sentFromEvents(events));
  for(const message of unrecorded(turns,messages,Date.parse(run.created_at||run.started_at||'')||0))turns.push({role:'user',text:message.text});
  for(const message of state.steers||[])turns.push({role:'user',text:message.message,label:message.label});
  const output=typeof run.text==='string'?run.text:String([...events].reverse().find(e=>e.event==='done')?.data?.text||run.output||events.filter(e=>e.event==='delta').map(e=>e.data?.text||'').join('')||'');
  if(output&&!turns.some(t=>t.role==='assistant'&&String(t.text||'').trim()===output.trim()))turns.push({role:'assistant',text:output});
  return turns;
 }
 const api={appendRecovery,normalized,unrecorded,historyTurns,sentFromEvents,queueLabel};if(typeof module==='object')module.exports=api;else root.LibraryState=api;
})(globalThis);
