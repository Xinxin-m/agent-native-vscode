import {useEffect,useRef,useState} from 'react';
import {RotateCw,Square,ArrowUpRight,Terminal,Link,RefreshCw} from 'lucide-react';
import {api} from '../api/client';
import {useWorkbench} from '../state/context';
import {visibleSessions,groupByNode,select,previewStops,commitStops,stopPlan,duration,slurmLeft,type Session,type Outcome,type StopPreview} from './sessionControl';
import './sessions.css';

const WHERE:Record<string,string>={tmux:'tmux',vscode:'VS Code',desktop:'Desktop app',terminal:'Terminal',other:'Other',dead:'Gone'};
const STATUS:Record<string,string>={busy:'Busy',idle:'Idle',shell:'Shell',gone:'Gone','?':'Unknown'};
const now=()=>Date.now()/1000;
const stamp=(ts?:number)=>ts?new Date(ts*1000).toLocaleString('en-US',{dateStyle:'medium',timeStyle:'short'}):'';
const modelName=(id?:string)=>{const m=String(id||'').toLowerCase().match(/^(?:claude-)?(fable|opus|sonnet|haiku)\b|^gpt-.*-(astra|sol|luna|terra)\b/);const key=m?.[1]||m?.[2];return key?key[0].toUpperCase()+key.slice(1):id||'';};
const hostLabel=(id:string)=>['mac','local'].includes(id)?'This Mac':id;
const copy=(text:string)=>(window as any).libraryRpc?(window as any).libraryRpc('copy',{text}):navigator.clipboard.writeText(text);

export function SessionsView(){
 const wb=useWorkbench(),root=useRef<HTMLElement>(null);
 const [data,setData]=useState<any>(null),[hosts,setHosts]=useState<any[]|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [outcomes,setOutcomes]=useState<Record<string,Outcome>>({}),[selected,setSelected]=useState<Set<string>>(new Set()),anchor=useRef<string|null>(null);
 const [dialog,setDialog]=useState<{previews:StopPreview[]|null;sessions:Session[]}|{host:any}|null>(null);
 const chats=new Map<string,any>((wb.data?.chats||[]).map((c:any)=>[c.key,c]));

 async function load(refresh=false){
  try{setData(await api(refresh?'/api/live/refresh':'/api/live',refresh?{}:undefined));setError('');}catch(e:any){setError(e.message);}
  await Promise.all([api('/api/hosts').then(v=>setHosts(v.hosts||[])).catch(()=>{}),api('/api/live/policy').then(v=>setOutcomes(o=>{const next={...o};for(const item of v.outcomes||[])if(!next[item.id]||item.at>=next[item.id].at)next[item.id]=item;return next;})).catch(()=>{})]);
 }
 const visible=()=>!document.hidden&&!!root.current?.getClientRects().length;
 useEffect(()=>{
  void (async()=>{
   const list=(await api('/api/hosts').catch(()=>({hosts:[]}))).hosts||[];setHosts(list);
   await Promise.all(list.filter((h:any)=>h.transport==='ssh'&&h.configured&&(!h.observed_at||now()-h.observed_at>150)).map((h:any)=>api('/api/hosts/probe',{id:h.id}).catch(()=>{})));
   await load();
  })();
 },[]);
 useEffect(()=>{const every=wb.preferences.liveRefresh??15;if(!every)return;const timer=setInterval(()=>{if(visible())void load();},every*1000);return()=>clearInterval(timer);},[wb.preferences.liveRefresh]);

 const sessions=visibleSessions(data?.sessions,outcomes);
 const groups=groupByNode(data?.nodes,sessions),order=groups.flatMap(g=>g.rows.map(r=>r.id));
 const chosen=new Set([...selected].filter(id=>order.includes(id)));
 const pick=(id:string,e:{shift?:boolean;meta?:boolean;toggle?:boolean})=>setSelected(current=>{const next=select(new Set([...current].filter(key=>order.includes(key))),anchor.current,id,order,e);anchor.current=next.anchor;return next.selected;});

 async function refreshNow(){setBusy(true);await load(true);setBusy(false);wb.notify('Sessions refreshed');}
 async function probe(id:string){try{await api('/api/hosts/probe',{id});await load();}catch(e:any){wb.notify(e.message);}}
 async function preview(rows:Session[]){setDialog({previews:null,sessions:rows});const previews=await previewStops(api,rows);setDialog(d=>d&&'sessions' in d&&d.sessions===rows?{...d,previews}:d);}
 async function confirm(previews:StopPreview[]){
  setDialog(null);
  await commitStops(api,previews,o=>{setOutcomes(all=>({...all,[o.id]:o}));if(o.state!=='error')setSelected(s=>{const n=new Set(s);n.delete(o.id);return n;});});
  setTimeout(()=>void load(),1500);
 }
 useEffect(()=>{if(!dialog)return;const key=(e:KeyboardEvent)=>{if(e.key==='Escape')setDialog(null);};addEventListener('keydown',key);return()=>removeEventListener('keydown',key);},[dialog]);

 function row(s:Session){
  const chat=chats.get(s.chat_key),status=STATUS[s.status]?s.status:'?',outcome=outcomes[s.id]?.session_id===s.session_id?outcomes[s.id]:null;
  const pending=outcome?.state==='stop_requested',fresh=s.observed_at&&now()-s.observed_at>=0&&now()-s.observed_at<150,text=String(s.last_text||'').trim();
  return <tr key={s.id} className={chosen.has(s.id)?'is-selected':''} onClick={e=>{if(!(e.target as HTMLElement).closest('a,button,input'))pick(s.id,{shift:e.shiftKey,meta:e.metaKey||e.ctrlKey});}}>
   <td><input type="checkbox" aria-label={'Select '+(s.name||s.id)} checked={chosen.has(s.id)} onChange={()=>{}} onClick={e=>pick(s.id,{shift:e.shiftKey,meta:e.metaKey||e.ctrlKey,toggle:true})}/></td>
   <td><div className="sessions-name"><strong title={[s.name||s.session_id||s.id,s.cwd].filter(Boolean).join(' · ')}>{s.name||s.session_id||s.id}</strong><span>{hostLabel(s.host||s.node||'')}</span></div></td>
   <td className="col-where">{WHERE[s.where||'other']||s.where}</td>
   <td><span className={'sessions-status '+status}><i/>{STATUS[status]}</span></td>
   <td>{s.last_msg_ts?<span title={stamp(s.last_msg_ts)}>{duration(now()-s.last_msg_ts)} ago</span>:<span className="faint">none</span>}</td>
   <td className="col-idle">{s.last_act_ts?<span title={'Last activity '+stamp(s.last_act_ts)}>{duration(now()-s.last_act_ts)}</span>:<span className="faint">none</span>}</td>
   <td className="col-conn">{s.attached===true?'Attached':s.attached===false?<span className="faint">Detached</span>:<span className="faint">n/a</span>}{s.remote_control&&<a className="sessions-icon" href={s.remote_control} target="_blank" rel="noopener" title="Open Remote Control in a new tab" aria-label="Open Remote Control"><Link size={13}/></a>}</td>
   <td className="col-model">{chat?.model_main?<span title={chat.model_main}>{modelName(chat.model_main)}</span>:<span className="faint">unknown</span>}</td>
   <td className="sessions-said"><span title={text}>{text||<span className="faint">none</span>}</span></td>
   <td className="sessions-actions">
    {chat&&<button className="sessions-act" title="Open transcript" aria-label="Open transcript" onClick={()=>wb.open({kind:'chat',key:chat.key,title:chat.title},'tab')}><ArrowUpRight size={13}/></button>}
    {s.attach_cmd&&<button className="sessions-act" title={'Copy attach command: '+s.attach_cmd} aria-label="Copy attach command" onClick={()=>void copy(s.attach_cmd)}><Terminal size={13}/></button>}
    <button className="sessions-act sessions-stop" disabled={!(s.stop_allowed&&fresh)||pending} title={s.stop_allowed&&fresh?'Stop this agent':s.stop_reason||'A fresh verified process identity is required'} aria-label="Stop session" onClick={()=>void preview([s])}><Square size={11}/>{pending?'Stopping…':'Stop'}</button>
    {s.restart_cmd&&<button className="sessions-act" title={'Copy restart command: '+s.restart_cmd} aria-label="Copy restart command" onClick={()=>void copy(s.restart_cmd)}><RefreshCw size={13}/></button>}
   </td>
  </tr>;
 }

 const results=Object.values(outcomes).filter(o=>now()-o.at<3600).slice(-8);
 return <section className="sessions-view" ref={root} aria-label="Sessions"><div className="sessions-page">
  <header><div><h1>Sessions</h1><p>{data?<>{sessions.length} running{data.fetched_at&&<>, checked <span title={stamp(data.fetched_at)}>{duration(now()-data.fetched_at)} ago</span></>}</>:''}</p></div><button className="chat-icon" title="Refresh now" aria-label="Refresh now" disabled={busy} onClick={()=>void refreshNow()}><RotateCw size={16}/></button></header>
  {hosts&&<div className="sessions-hosts">{hosts.map(h=><div className="sessions-host" key={h.id}><strong>{h.label||hostLabel(h.id)}</strong><span className="faint">{h.state||'unknown'}</span>
   {h.transport==='ssh'&&h.configured&&<button className="sessions-button" onClick={()=>void probe(h.id)}>Refresh status</button>}
   {h.id==='second-mac'&&!h.configured&&<button className="sessions-button" onClick={()=>setDialog({host:h})}>Configure</button>}
   {h.error&&<small className="sessions-error">{h.error}</small>}</div>)}</div>}
  {error?<p className="sessions-state">Could not reach the server. {error}</p>:!data?<p className="sessions-state">Loading sessions</p>:<>
   {results.length>0&&<div className="sessions-results" aria-live="polite">{results.map(o=><div key={o.id} className={o.state}><strong>{o.name||o.id}</strong><span>{o.state==='stopped'?'Stopped':o.state==='stop_requested'?'Stopping… waiting for process to exit':o.error||'Stop failed'}</span></div>)}</div>}
   {!groups.length?<p className="sessions-state">No sessions are running on any node.</p>:<>
    <div className="sessions-selection"><label><input type="checkbox" checked={!!order.length&&chosen.size===order.length} onChange={e=>setSelected(e.target.checked?new Set(order):new Set())}/>Select all</label><span className={chosen.size?'has-selection':''}>{chosen.size?chosen.size+' selected':'Click a row to select · ⌘ / Ctrl to add · Shift for a range'}</span><button className="sessions-button" disabled={!chosen.size} onClick={()=>void preview(sessions.filter(s=>chosen.has(s.id)))}><Square size={11}/>Stop selected</button></div>
    {groups.map(({node,rows})=><section className="sessions-node" key={node.name}>
     <div className="sessions-node-head"><strong>{hostLabel(node.name)}</strong><span>{rows.length} session{rows.length===1?'':'s'}</span>
      {(node.jobs||[]).map((j:any)=>{const left=slurmLeft(j.left);return <span key={j.id} title={'Slurm job '+j.id}>{j.name||j.id} on {j.node||'?'}{j.left?', '+(left===null?j.left:duration(left))+' left':''}</span>;})}
      {node.stale_since?<span>Cached {stamp(node.stale_since)}</span>:node.ok===false?<span className="sessions-error">Unreachable: {node.error||'no answer'}</span>:null}</div>
     {rows.length?<table className="sessions-table"><colgroup><col className="c-select"/><col className="c-name"/><col className="c-where col-where"/><col className="c-status"/><col className="c-msg"/><col className="c-idle col-idle"/><col className="c-conn col-conn"/><col className="c-model col-model"/><col/><col className="c-act"/></colgroup>
      <thead><tr><th><span className="sr-only">Select</span></th><th>Name</th><th className="col-where">Where</th><th>Status</th><th title="Time of the last message">Messaged</th><th className="col-idle">Idle</th><th className="col-conn">Connected</th><th className="col-model">Model</th><th>Last thing I said</th><th><span className="sr-only">Actions</span></th></tr></thead>
      <tbody>{rows.map(row)}</tbody></table>:node.ok===false?null:<p className="sessions-empty">No sessions on this node.</p>}
    </section>)}
   </>}
  </>}
  {dialog&&<div className="dialog-backdrop" onPointerDown={()=>setDialog(null)}><div className="sessions-dialog" role="dialog" aria-modal="true" onPointerDown={e=>e.stopPropagation()}>
   {'host' in dialog?<><h2>Connect {dialog.host.label||'second Mac'}</h2><p>{dialog.host.setup_status||dialog.host.error||'SSH access and a host connector are required.'}</p><ol><li>Enable Remote Login in System Settings on the second Mac.</li><li>Authorize this Mac’s SSH key for the remote account.</li><li>Verify the SSH alias {dialog.host.ssh_host||dialog.host.ssh_alias||'library-second-mac'} and install the Library host connector.</li></ol><p>Connection status: {dialog.host.state||'Not configured'}</p><div className="sessions-dialog-buttons"><button className="sessions-button" autoFocus onClick={()=>setDialog(null)}>Done</button></div></>
   :(()=>{const plan=dialog.previews&&stopPlan(dialog.previews);return <><h2>{plan?plan.title:'Checking sessions…'}</h2>{(!plan||plan.stoppable.length>0)&&<p>Stops the selected agent processes. Transcripts and terminal sessions remain. Each process is verified again before stopping.</p>}
    <ul>{plan?<>{plan.stoppable.map(s=><li key={s.id}>{s.name||s.id}</li>)}{plan.blocked.map(b=><li key={b.session.id} className="sessions-blocked">{b.session.name||b.session.id} · {b.reason}</li>)}</>:dialog.sessions.map(s=><li key={s.id}>{s.name||s.id}</li>)}</ul>
    <div className="sessions-dialog-buttons">{plan&&!plan.stoppable.length?<button className="sessions-button" autoFocus onClick={()=>setDialog(null)}>Close</button>:<><button className="sessions-button" autoFocus onClick={()=>setDialog(null)}>Cancel</button><button className="sessions-button sessions-confirm" disabled={!plan} onClick={()=>void confirm(dialog.previews!)}><Square size={11}/>Stop</button></>}</div></>;})()}
  </div></div>}
 </div></section>;
}
export default SessionsView;
