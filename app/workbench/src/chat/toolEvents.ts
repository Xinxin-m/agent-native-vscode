import type {RunEvent} from '../state/runs';
export interface ToolActivity {call?:RunEvent;results:RunEvent[]}
export function groupToolEvents(events:RunEvent[]):ToolActivity[]{
 const rows:ToolActivity[]=[],byId=new Map<string,ToolActivity>();
 for(const event of events){
  if(!['tool','tool_result'].includes(event.event))continue;
  const id=String(event.data?.tool_use_id||event.data?.call_id||event.data?.id||'');
  if(event.event==='tool'){const row={call:event,results:[]};rows.push(row);if(id)byId.set(id,row);}
  else {const row=id?byId.get(id):undefined;if(row)row.results.push(event);else rows.push({results:[event]});}
 }
 return rows;
}

export function activityEvents(events:RunEvent[],pending:RunEvent[]){return events.filter(e=>!['delta','start','done','state','request_resolved','provider_event','tool','tool_result'].includes(e.event)&&!pending.some(p=>p.seq===e.seq));}
export function sessionStatus(run:any,verifiedRunning:boolean,awaiting:boolean,transcriptStatus?:string,nativeStatus?:string){
 if(run.status==='remote_idle')return 'Remote Control connected';
 if(run.status==='queued')return 'Queued';
 if(run.status==='interrupting')return 'Stopping';
 if(awaiting||['awaiting_approval','awaiting_input','waiting_approval','waiting_input'].includes(run.status))return 'Waiting for you';
 if(run.status==='unknown'||run.status==='disconnected')return 'Live status unavailable';
 if(run.status==='failed'||run.status==='error')return 'Failed';
 if(['cancelled','canceled','stopped','interrupted'].includes(run.status))return 'Stopped';
 if(run.status&& !['completed','complete','failed','cancelled','canceled','stopped','interrupted','unknown','disconnected','error'].includes(run.status))return 'Agent running';
 if(nativeStatus==='waiting')return 'Waiting for you';
 if(nativeStatus==='idle')return 'Idle';
 if(verifiedRunning)return 'Agent running';
 if(run.status==='completed'||run.status==='complete'||transcriptStatus==='complete')return 'Finished';
 return 'Live status unavailable';
}
