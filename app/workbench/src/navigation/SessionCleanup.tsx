import {useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {createSerialWriter,policyDurations} from '../state/workspace';

export const durationLabel=(n:number)=>n===12?'12 hours':n===24?'1 day':n%24===0?n/24+' days':n+' hours';
const SLOW_MS=3000,GIVE_UP_MS=20000;

// The stop rule lives on the Library server (GET/POST /api/live/policy). A busy server
// can answer slowly, so this section loads on its own and says what it is doing.
export function SessionCleanup(){
 const [policy,setPolicy]=useState<any>(null),[load,setLoad]=useState<'loading'|'slow'|'failed'|'done'>('loading'),[status,setStatus]=useState('');
 const policyRef=useRef<any>(null),mounted=useRef(true),writer=useRef<any>(null);
 if(!writer.current)writer.current=createSerialWriter((value:any)=>api('/api/live/policy',{enabled:value.enabled,basis:value.basis,hours:value.hours}),(state,value)=>{if(!mounted.current)return;if(state==='saving')setStatus('Saving…');else if(state==='saved'){policyRef.current=value.policy||value;setPolicy(policyRef.current);setStatus('Saved');}else setStatus('Not saved: '+value.message);});
 function fetchPolicy(){
  setLoad('loading');const controller=new AbortController();
  const slow=setTimeout(()=>{if(mounted.current)setLoad(s=>s==='loading'?'slow':s)},SLOW_MS),stop=setTimeout(()=>controller.abort(),GIVE_UP_MS);
  api('/api/live/policy',undefined,controller.signal).then(d=>{if(!mounted.current)return;policyRef.current=d.policy||d;setPolicy(policyRef.current);setLoad('done');}).catch(()=>{if(mounted.current)setLoad('failed')}).finally(()=>{clearTimeout(slow);clearTimeout(stop)});
 }
 useEffect(()=>{mounted.current=true;fetchPolicy();return()=>{mounted.current=false};},[]);
 function change(patch:any){const next={...policyRef.current,...patch};policyRef.current=next;setPolicy(next);writer.current.submit(next);}
 if(!policy)return <div className="settings-pending" role="status">{load==='failed'?<>The Library server did not answer. The saved rule still applies. <button className="text-button" onClick={fetchPolicy}>Retry</button></>:load==='slow'?'Still loading. The Library server is busy.':'Loading…'}</div>;
 const last=(policy.outcomes||[]).filter((o:any)=>o.source==='automatic'&&o.state!=='error').at(-1);
 return <>
  <label className="setting-row"><span>Stop agents automatically<small>Ends Claude Code agents that match the rule below. The chat and its transcript stay and can be resumed.</small></span><input type="checkbox" checked={!!policy.enabled} onChange={e=>change({enabled:e.target.checked})}/></label>
  {policy.enabled&&<>
   <label className="setting-row"><span>Stop when<small>{policy.basis==='age'?'Running time counts busy agents too, so it can stop one mid-task.':'Idle means the agent finished its turn and is waiting for you.'}</small></span><select aria-label="Stop when" value={policy.basis} onChange={e=>change({basis:e.target.value})}><option value="idle">Idle for longer than</option><option value="age">Running for longer than</option></select></label>
   <label className="setting-row"><span>Limit<small>Checked every minute.</small></span><select aria-label="Limit" value={policy.hours} onChange={e=>change({hours:+e.target.value})}>{policyDurations(policy.hours).map(n=><option key={n} value={n}>{durationLabel(n)}</option>)}</select></label>
  </>}
  <p className="settings-note">Claude Code only. Never Codex, shared or unverified processes. Never deletes transcripts, tmux sessions or cluster jobs.{last?` Last automatic stop: ${last.name||'an agent'}, ${new Date(last.at*1000).toLocaleString()}.`:''}</p>
  <small className="settings-status" role="status">{status}</small>
 </>;
}
