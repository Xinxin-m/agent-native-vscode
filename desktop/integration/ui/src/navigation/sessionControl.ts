// Selection and stop flow for the Sessions tab, ported from web/live.js.
export type Session={id:string;session_id?:string;name?:string;node?:string;alive?:boolean;process_only?:boolean;last_msg_ts?:number;last_act_ts?:number;stop_allowed?:boolean;stop_reason?:string;[key:string]:any};
export type Outcome={id:string;session_id?:string;name?:string;state:string;error?:string;at:number};
export type StopPreview={session:Session;token?:string;error?:string};
type Api=(path:string,body?:any)=>Promise<any>;

export function visibleSessions(sessions:Session[]=[],outcomes:Record<string,Outcome>={}){
 const stopped=(s:Session)=>outcomes[s.id]?.session_id===s.session_id&&outcomes[s.id]?.state==='stopped';
 const rank=(s:Session)=>(s.alive===false?0:1e12)+(s.last_msg_ts||s.last_act_ts||0);
 return sessions.filter(s=>s.alive===true&&!s.process_only&&!stopped(s)).sort((a,b)=>rank(b)-rank(a));
}

export function groupByNode(nodes:any[]=[],sessions:Session[]){
 const all=[...nodes];
 for(const s of sessions)if(!all.some(n=>n.name===(s.node||'unknown')))all.push({name:s.node||'unknown',ok:true});
 return all.map(node=>({node,rows:sessions.filter(s=>(s.node||'unknown')===node.name)}));
}

// Click selects one row, Cmd/Ctrl or the checkbox toggles, Shift selects the range from the anchor.
export function select(selected:Set<string>,anchor:string|null,id:string,order:string[],e:{shift?:boolean;meta?:boolean;toggle?:boolean}){
 if(e.shift&&anchor&&order.includes(anchor)){
  const from=order.indexOf(anchor),to=order.indexOf(id),next=new Set(e.meta?selected:[]);
  order.slice(Math.min(from,to),Math.max(from,to)+1).forEach(key=>next.add(key));
  return {selected:next,anchor};
 }
 if(e.meta||e.toggle){const next=new Set(selected);if(next.has(id))next.delete(id);else next.add(id);return {selected:next,anchor:id};}
 return {selected:new Set([id]),anchor:id};
}

// Preview only issues one-use tokens; nothing stops until commitStops runs after the user confirms.
export async function previewStops(api:Api,sessions:Session[]){
 const previews:StopPreview[]=[];
 for(const session of sessions){
  if(session.stop_allowed===false&&session.stop_reason){previews.push({session,error:session.stop_reason});continue;}
  try{previews.push({session,token:(await api('/api/live/stop/preview',{id:session.id})).token});}
  catch(e:any){previews.push({session,error:e?.message||'Cannot verify this process'});}
 }
 return previews;
}

// Only sessions with a token will be stopped; the rest keep their reason and follow in the dialog.
export function stopPlan(previews:StopPreview[]){
 const stoppable=previews.filter(p=>p.token).map(p=>p.session);
 const blocked=previews.filter(p=>!p.token).map(p=>({session:p.session,reason:p.error||'Cannot verify this process'}));
 const title=stoppable.length?'Stop '+stoppable.length+(stoppable.length===1?' session?':' sessions?'):'Nothing to stop';
 return {stoppable,blocked,title};
}

export async function commitStops(api:Api,previews:StopPreview[],report:(o:Outcome)=>void){
 for(const {session,token} of previews){
  if(!token)continue;
  const base={id:session.id,session_id:session.session_id,name:session.name};
  report({...base,state:'stop_requested',at:Date.now()/1000});
  // Success keeps the earlier timestamp so the server's later "stopped" record replaces it.
  try{await api('/api/live/stop/commit',{token});}
  catch(e:any){report({...base,state:'error',error:e?.message||'Stop failed',at:Date.now()/1000});}
 }
}

// "3m", "1h20m", "2d04h"
export function duration(seconds?:number|null){
 if(seconds===null||seconds===undefined||!isFinite(seconds))return '';
 const s=Math.max(0,Math.round(seconds));if(s<60)return s+'s';
 const m=Math.floor(s/60);if(m<60)return m+'m';
 const h=Math.floor(m/60);if(h<24)return h+'h'+String(m%60).padStart(2,'0')+'m';
 return Math.floor(h/24)+'d'+String(h%24).padStart(2,'0')+'h';
}

// Slurm "19-03:11:02" / "03:11:02" / "11:02" -> seconds
export function slurmLeft(text:string){
 const m=String(text||'').match(/^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$/);
 return m?(+(m[1]||0))*86400+(+(m[2]||0))*3600+(+m[3])*60+(+m[4]):null;
}
