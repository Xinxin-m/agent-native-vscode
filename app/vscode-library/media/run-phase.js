function runPhase(run,events=[],awaiting=false,fallback='Ready'){
 const done=[...events].reverse().find(e=>e.event==='done');const status=done?.data?.status||run?.status;
 const compacted=events.some(e=>e.event==='compacted'||(e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='compact_boundary'));
 if(['failed','error'].includes(status))return 'Failed';if(['interrupted','cancelled','canceled'].includes(status))return 'Stopped';
 if(['completed','complete'].includes(status)||done)return compacted?'Compacted':'Finished';
 if(awaiting||['awaiting_approval','awaiting_input','waiting_approval','waiting_input'].includes(status))return 'Waiting for you';
 if(status==='remote_idle')return 'Remote Control connected';if(status==='interrupting')return 'Stopping';if(status==='queued')return 'Queued';
 const native=[...events].reverse().find(e=>e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='status')?.data?.event;
 const background=[...events].reverse().find(e=>e.event==='state'&&e.data?.phase)?.data;if(background?.phase==='background'){const n=background.background?.length||0;return n>1?n+' background tasks running':'Background task running'}if(background?.phase==='idle')return 'Finished';
 if(native?.status==='compacting'||(run?.operation==='compact'&&!compacted))return 'Compacting';
 if(['running','starting','waiting'].includes(status))return 'Thinking';
 return fallback;
}

if(typeof module!=="undefined")module.exports={runPhase};else globalThis.LibraryRunPhase=runPhase;
