"use strict";
(function(root){
 const normalized=text=>String(text||'').replace(/\s+/g,' ').trim();
 function appendRecovery(current,message){if(!message||current===message)return current;return current?current+'\n\n'+message:message;}
 // Consume transcript turns once, bounded by the send time so an old identical prompt cannot hide a new one.
 function unrecorded(turns,messages,runStart){const used=new Set();return messages.filter(message=>{const sent=Date.parse(message.sentAt||''),from=Number.isFinite(sent)?sent-60000:runStart-1000;const i=turns.findIndex((turn,j)=>!used.has(j)&&turn.role==='user'&&normalized(turn.text)===normalized(message.text)&&!(Date.parse(turn.ts)<from));if(i<0)return true;used.add(i);return false;});}
 function historyTurns(state){
  const rs=state.run,events=rs?.events||[],turns=[...(state.chat?.turns||[])],run=rs?.run||{};
  const messages=[];
  if(run.message)messages.push({text:run.message,sentAt:run.created_at||run.started_at});
  for(const event of events)if(event.event==='steered'&&event.data?.message)messages.push({text:event.data.message,sentAt:event.ts||event.data.at});
  const delivered=new Set();for(const event of events){const change=event.event==='queue'&&event.data?.change,item=change?.item;if(change?.action==='delivered'&&item?.text&&!delivered.has(item.id)){delivered.add(item.id);messages.push({text:item.text,sentAt:item.updated_at||event.ts});}}
  for(const message of unrecorded(turns,messages,Date.parse(run.created_at||run.started_at||'')||0))turns.push({role:'user',text:message.text});
  for(const message of state.steers||[])turns.push({role:'user',text:message.message,label:message.label});
  const output=typeof run.text==='string'?run.text:String([...events].reverse().find(e=>e.event==='done')?.data?.text||run.output||events.filter(e=>e.event==='delta').map(e=>e.data?.text||'').join('')||'');
  if(output&&!turns.some(t=>t.role==='assistant'&&String(t.text||'').trim()===output.trim()))turns.push({role:'assistant',text:output});
  return turns;
 }
 const api={appendRecovery,normalized,unrecorded,historyTurns};if(typeof module==='object')module.exports=api;else root.LibraryState=api;
})(globalThis);
