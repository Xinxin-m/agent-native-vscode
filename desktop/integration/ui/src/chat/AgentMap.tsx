import React,{useEffect,useRef,useState} from 'react';
import {api,query} from '../api/client';
import './agent-map.css';

type Status='running'|'waiting'|'finished'|'unknown';
const waiting=['waiting','waiting_input','waiting_approval','waiting_for_input','waiting_for_permission','awaiting_input','awaiting_approval'];
const running=['queued','starting','running','interrupting'];
const finished=['completed','complete','failed','error','interrupted','cancelled','canceled'];
export function agentMapStatus(node:any,runs:any[],live:any,now=Date.now()/1000):Status{
 const matches=runs.filter(r=>node.key&&[r.key,r.conversation_key].includes(node.key));
 const active=matches.find(r=>waiting.includes(r.status)||running.includes(r.status));
 if(active)return waiting.includes(active.status)?'waiting':'running';
 const row=(live?.sessions||[]).find((s:any)=>s.chat_key===node.key&&s.alive&&!s.process_only&&Number(s.observed_at)>0&&now-Number(s.observed_at)<150&&Number(s.observed_at)<=now+5&&!live?.nodes?.some((n:any)=>n.name===(s.node||'mac')&&(!n.ok||n.stale_since)));
 if(row){if(waiting.includes(row.status))return 'waiting';if(['running','busy','working'].includes(row.status))return 'running';return 'unknown';}
 if(matches.some(r=>finished.includes(r.status))||finished.includes(node.status))return 'finished';
 return 'unknown';
}
export function AgentMap({sessionKey,selected,onOpen,onClose,mode='popup'}:{sessionKey:string;selected:string;onOpen:(node:any)=>void;onClose:()=>void;mode?:'popup'|'inline'}){
 const [tree,setTree]=useState<any>(null),[live,setLive]=useState<any>(null),[runs,setRuns]=useState<any[]>([]),[error,setError]=useState('');
 const [retry,setRetry]=useState(0);
 const dialog=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;
 useEffect(()=>{
  let alive=true,busy=false;
  const controllers=new Set<AbortController>();
  setTree(null);setLive(null);setRuns([]);setError('');
  async function request(path:string){
   const controller=new AbortController();controllers.add(controller);
   let timer:ReturnType<typeof setTimeout>;
   try{return await Promise.race([api(path,undefined,controller.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('The subagent tree request timed out. Try again.'));},12000)})]);}
   finally{clearTimeout(timer!);controllers.delete(controller);}
  }
  async function refresh(){
   if(busy)return;busy=true;
   try{await Promise.all([
    request(query('/api/session_tree',{key:sessionKey})).then(t=>{if(alive){setTree(t);setError('')}}).catch(e=>{if(alive)setError(e.message||'The subagent tree did not refresh.')}),
    Promise.allSettled([request('/api/live'),request('/api/runs')]).then(([l,r])=>{if(alive){setLive(l.status==='fulfilled'?l.value:null);setRuns(r.status==='fulfilled'?r.value.runs||[]:[])}})
   ]);}finally{busy=false;}
  }
  void refresh();const timer=window.setInterval(refresh,15000);
  return()=>{alive=false;clearInterval(timer);controllers.forEach(c=>c.abort())};
 },[sessionKey,retry]);
 useEffect(()=>{if(mode!=='popup')return;const previous=document.activeElement as HTMLElement|null;dialog.current?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();close.current();return}if(e.key!=='Tab'||!dialog.current)return;const items=Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'));if(!items.length){e.preventDefault();return}const first=items[0],last=items[items.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===dialog.current)){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}};document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus()}},[mode]);
 useEffect(()=>{if(mode!=='popup')return;const dismiss=()=>close.current();window.addEventListener('workbench-dismiss-popovers',dismiss);return()=>window.removeEventListener('workbench-dismiss-popovers',dismiss)},[mode]);
 const nodes=tree?.nodes||[],edges=tree?.edges||[];const children=new Set(edges.map((e:any)=>e.child_key));const roots=nodes.filter((n:any)=>!children.has(n.key));
 function branch(node:any,seen:Set<string>):React.ReactNode{if(seen.has(node.key))return null;const next=new Set([...seen,node.key]);const kids=edges.filter((e:any)=>e.parent_key===node.key).map((e:any)=>nodes.find((n:any)=>n.key===e.child_key)).filter(Boolean);const status=agentMapStatus(node,runs,live);return <li key={node.key}><div className="agent-map-branch"><button type="button" className="agent-map-node" disabled={node.missing} aria-current={node.key===selected?'page':undefined} onClick={()=>onOpen(node)}><span className={'agent-map-dot '+status}/><span><strong>{node.title||node.key}</strong><small>{[node.source,status,node.missing?'Transcript unavailable':''].filter(Boolean).join(' · ')}</small></span></button>{kids.length>0&&<ul>{kids.map((child:any)=>branch(child,next))}</ul>}</div></li>}
 const content=<div className={'agent-map '+(mode==='inline'?'is-inline':'')} role={mode==='popup'?'dialog':'region'} aria-modal={mode==='popup'?true:undefined} aria-label="Subagent tree" ref={dialog} tabIndex={-1}><header><div><h2>Subagent tree</h2><p>{nodes.length} {nodes.length===1?'agent':'agents'} · select an agent to open its conversation</p></div><button type="button" className="agent-map-close" aria-label="Close subagent tree" onClick={onClose}>×</button></header>{error&&<p role="alert">{error} <button type="button" onClick={()=>setRetry(v=>v+1)}>Retry</button></p>}{!tree&&!error&&<p role="status">Loading subagent tree…</p>}{tree&&<div className="agent-map-scroll">{roots.length?<ul className="agent-map-roots">{roots.map((node:any)=>branch(node,new Set()))}</ul>:<p>No explicit agent lineage found.</p>}</div>}<footer>Statuses use broker runs, recent verified live observations and recorded completion. Unknown means no current evidence.</footer></div>;
 return mode==='popup'?<div className="agent-map-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>{content}</div>:content;
}
