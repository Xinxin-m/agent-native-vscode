export function runPhase(run:any,events:any[]=[],awaiting=false,fallback='Ready'){
 const done=[...events].reverse().find(e=>e.event==='done');const status=done?.data?.status||(done&&!['completed','complete','failed','error','interrupted','cancelled','canceled','stopped'].includes(run?.status)?undefined:run?.status);
 const compacted=events.some(e=>e.event==='compacted'||(e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='compact_boundary'));
 if(['failed','error'].includes(status))return 'Failed';if(['interrupted','cancelled','canceled','stopped'].includes(status))return 'Stopped';
 if(['completed','complete'].includes(status))return compacted&&!events.some(e=>e.event==='auto_compact')?'Compacted':'Finished';
 if(awaiting||['awaiting_approval','awaiting_input','waiting_approval','waiting_input'].includes(status))return 'Waiting for you';
 if(status==='remote_idle')return 'Remote Control connected';if(status==='interrupting')return 'Stopping';if(status==='queued')return 'Queued';
 const native=[...events].reverse().find(e=>e.event==='provider_event'&&e.data?.event?.type==='system'&&e.data.event.subtype==='status')?.data?.event;
 const background=[...events].reverse().find(e=>e.event==='state'&&e.data?.phase)?.data;if(background?.phase==='background'){const n=background.background?.length||0;return n>1?n+' background tasks running':'Background task running'}if(background?.phase==='idle')return 'Finished';
 if(native?.status==='compacting'||(run?.operation==='compact'&&!compacted))return 'Compacting';
 if(['running','starting','waiting'].includes(status))return 'Thinking';
 return fallback;
}
