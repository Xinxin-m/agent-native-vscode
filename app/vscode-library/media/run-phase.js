function runPhase(run,events=[],awaiting=false,fallback='Ready'){
 const done=[...events].reverse().find(e=>e.event==='done');const status=done?.data?.status||(done&&!['completed','complete','failed','error','interrupted','cancelled','canceled','stopped'].includes(run?.status)?undefined:run?.status);
 const compacted=events.some(e=>e.event==='compacted'||(e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='compact_boundary'));
 if(['failed','error'].includes(status))return 'Failed';if(['interrupted','cancelled','canceled','stopped'].includes(status))return 'Stopped';
 if(['completed','complete'].includes(status))return compacted?'Compacted':'Finished';
 if(awaiting||['awaiting_approval','awaiting_input','waiting_approval','waiting_input'].includes(status))return 'Waiting for you';
 if(['unknown','disconnected'].includes(status))return 'Activity unknown';
 if(status==='remote_idle')return 'Remote Control connected';if(status==='interrupting')return 'Stopping';if(status==='queued')return 'Queued';
 const stall=[...events].reverse().find(e=>e.event==='state'&&typeof e.data?.stalled==='boolean');
 if((stall&&(!Number.isFinite(run?.last_seq)||stall.seq>run.last_seq)?stall.data.stalled:run?.stalled)===true)return 'Stalled';
 const native=[...events].reverse().find(e=>e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='status')?.data?.event;
 const background=[...events].reverse().find(e=>e.event==='state'&&e.data?.phase)?.data;if(background?.phase==='background'){const n=background.background?.length||0;return n>1?n+' background tasks running':'Background task running'}if(background?.phase==='idle')return 'Finished';
 if(native?.status==='compacting'||(run?.operation==='compact'&&!compacted))return 'Compacting';
 if(['running','starting','waiting'].includes(status))return 'Thinking';
 return fallback;
}

function runActive(run,events=[],awaiting=false){
 const done=[...events].reverse().find(e=>e.event==='done');
 const status=done?.data?.status||(done?undefined:run?.status);
 return !awaiting&&['starting','running','waiting','interrupting'].includes(status)&&runPhase(run,events,awaiting)!=='Stalled';
}
if(typeof module!=="undefined")module.exports={runPhase,runActive};else {globalThis.LibraryRunPhase=runPhase;globalThis.LibraryRunActive=runActive;}

function runThinking(events,rootId){
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
if(typeof window!=="undefined")window.LibraryRunProgress=runThinking;

// Group same-named siblings while keeping every original transcript addressable.
function agentBatches(siblings,selected,hasChildren){
 const groups=new Map(),result=[];
 for(const node of siblings){
  const name=node.agent_label||node.title;
  if(!name||node.key===selected||hasChildren(node)){
   result.push([node]);continue;
  }
  const key=JSON.stringify([node.source,name]);
  if(!groups.has(key)){const batch=[];groups.set(key,batch);result.push(batch);}
  groups.get(key).push(node);
 }
 return result;
}
if(typeof window!=="undefined")window.LibraryAgentBatches=agentBatches;
