import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {ChevronLeft,ExternalLink,MoreHorizontal,X} from 'lucide-react';
import {api,query} from '../api/client';
import {StatusMark} from '../ui/StatusMark';
import {rowStatus} from '../navigation/chats';
import {firstLine,type UserPrompt} from './userPrompts';
import './agent-map.css';

export type AgentStatus='running'|'waiting'|'idle'|'finished'|'stopped'|'failed'|'unknown';
// The server's per-node `state` (same source as the sidebar rows), as a status.
const fromState:Record<string,AgentStatus>={waiting:'waiting',working:'running',idle:'idle',finished:'finished',stopped:'stopped',failed:'failed',unknown:'unknown'};
export type OpenWhere='preview'|'tab'|'current';
const waiting=['waiting','waiting_input','waiting_approval','waiting_for_input','waiting_for_permission','awaiting_input','awaiting_approval'];
const running=['queued','starting','running','interrupting'];
const finished=['completed','complete','failed','error','interrupted','cancelled','canceled'];
const failed=['failed','error'];
// Broker runs and verified live rows describe chats the Library launched or sees as processes.
// Subagents are neither, so the server adds `activity` from their transcripts (agent_activity.py).
export function agentMapStatus(node:any,runs:any[],live:any,now=Date.now()/1000):AgentStatus{
 const matches=runs.filter(r=>node.key&&[r.key,r.conversation_key].includes(node.key));
 const active=matches.find(r=>waiting.includes(r.status)||running.includes(r.status));
 if(active)return waiting.includes(active.status)?'waiting':'running';
 const row=(live?.sessions||[]).find((s:any)=>s.chat_key===node.key&&s.alive&&!s.process_only&&Number(s.observed_at)>0&&now-Number(s.observed_at)<150&&Number(s.observed_at)<=now+5&&!live?.nodes?.some((n:any)=>n.name===(s.node||'mac')&&(!n.ok||n.stale_since)));
 if(node.state&&fromState[node.state])return fromState[node.state];
 if(row){if(waiting.includes(row.status))return 'waiting';if(['running','busy','working'].includes(row.status))return 'running';return 'unknown';}
 if(node.live_status==='working'||node.activity==='running')return 'running';
 if(node.activity==='failed')return 'failed';
 if(['finished','stopped'].includes(node.activity))return 'finished';
 if(!node.activity&&(matches.some(r=>failed.includes(r.status))||failed.includes(node.status)))return 'failed';
 if(matches.some(r=>finished.includes(r.status))||finished.includes(node.status))return 'finished';
 return 'unknown';
}
export function agentStatusLabel(node:any,status:AgentStatus){
 if(status==='finished'&&node.activity==='stopped')return 'Stopped';
 if(status==='running')return 'Running';
 if(status==='idle')return 'Idle';
 return status[0].toUpperCase()+status.slice(1);
}
const rank=(s:AgentStatus)=>s==='running'?0:s==='waiting'?1:2;
// Same vocabulary as the sidebar: running, waiting, failed, finished and not seen; everything else has no mark.
const markOf=(node:any,status:AgentStatus)=>rowStatus(status==='running'?'working':status==='waiting'?'waiting':null,!!node.unseen&&(status==='idle'||status==='finished'),{failed:status==='failed'});
const newest=(n:any)=>String(n.ts_last||n.ts_first||'');
export interface AgentItem{node:any;status:AgentStatus;rank:number;children:AgentItem[]}
// Running branches first, then waiting, then the rest; newest first within a group.
// A parent takes the most active rank of its subtree so running children stay visible at the top.
export function orderAgentTree(nodes:any[],edges:any[],statusOf:(node:any)=>AgentStatus):AgentItem[]{
 const byKey=new Map(nodes.map(n=>[n.key,n])),childKeys=new Set(edges.map(e=>e.child_key));
 const build=(node:any,seen:Set<string>):AgentItem=>{
  const next=new Set([...seen,node.key]);
  const children=edges.filter(e=>e.parent_key===node.key&&!next.has(e.child_key)&&byKey.has(e.child_key)).map(e=>build(byKey.get(e.child_key),next));
  const status=statusOf(node);
  return {node,status,rank:Math.min(rank(status),...children.map(c=>c.rank)),children:sort(children)};
 };
 const sort=(items:AgentItem[])=>items.sort((a,b)=>a.rank-b.rank||newest(b.node).localeCompare(newest(a.node))||String(a.node.key).localeCompare(String(b.node.key)));
 return sort(nodes.filter(n=>!childKeys.has(n.key)).map(n=>build(n,new Set())));
}
export function agentCounts(items:AgentItem[]){
 const counts:Record<string,number>={};const walk=(list:AgentItem[])=>list.forEach(i=>{counts[i.status]=(counts[i.status]||0)+1;walk(i.children)});walk(items);return counts;
}
const ago=(ts:string)=>{const t=Date.parse(ts);if(!t)return '';const s=Math.max(0,(Date.now()-t)/1000);return s<60?'just now':s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' d ago'};
const chatResource=(node:any)=>({kind:'chat' as const,key:node.key,title:node.title,meta:node});
type ChatSlot={lead:React.ReactNode;actions:React.ReactNode;status:React.ReactNode};
export function AgentMap({sessionKey,selected,onOpen,onClose,renderChat,mode='popup',prompts,onPrompt}:{sessionKey:string;selected:string;onOpen:(node:any,where:Exclude<OpenWhere,'preview'>)=>void;onClose:()=>void;renderChat?:(resource:any,slot:ChatSlot)=>React.ReactNode;mode?:'popup'|'inline';prompts?:UserPrompt[];onPrompt?:(index:number)=>void}){
 const [tree,setTree]=useState<any>(null),[live,setLive]=useState<any>(null),[runs,setRuns]=useState<any[]>([]),[error,setError]=useState('');
 const [retry,setRetry]=useState(0),[preview,setPreview]=useState<any>(null),[menu,setMenu]=useState<{node:any;x:number;y:number;from:HTMLElement|null}|null>(null),[focusKey,setFocusKey]=useState(selected);
 const [tab,setTab]=useState<'tree'|'prompts'>('tree'),promptList=useRef<HTMLOListElement>(null);
 const titleId=useId(),dialog=useRef<HTMLDivElement>(null),menuRef=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;
 const state=useRef({preview,menu});state.current={preview,menu};
 useEffect(()=>{
  let alive=true,busy=false;
  const controllers=new Set<AbortController>();
  setTree(null);setLive(null);setRuns([]);setError('');
  async function request(path:string){
   const controller=new AbortController();controllers.add(controller);
   let timer:ReturnType<typeof setTimeout>;
   try{return await Promise.race([api(path,undefined,controller.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('The agent tree request timed out. Try again.'));},12000)})]);}
   finally{clearTimeout(timer!);controllers.delete(controller);}
  }
  async function refresh(){
   if(busy)return;busy=true;
   try{await Promise.all([
    request(query('/api/session_tree',{key:sessionKey})).then(t=>{if(alive){setTree(t);setError('')}}).catch(e=>{if(alive)setError(e.message||'The agent tree did not refresh.')}),
    Promise.allSettled([request('/api/live'),request('/api/runs')]).then(([l,r])=>{if(alive){setLive(l.status==='fulfilled'?l.value:null);setRuns(r.status==='fulfilled'?r.value.runs||[]:[])}})
   ]);}finally{busy=false;}
  }
  void refresh();const timer=window.setInterval(refresh,15000);
  return()=>{alive=false;clearInterval(timer);controllers.forEach(c=>c.abort())};
 },[sessionKey,retry]);
 const focusNode=(key:string)=>requestAnimationFrame(()=>dialog.current?.querySelector<HTMLElement>(`.agent-map-node[data-key="${CSS.escape(key)}"]`)?.focus());
 const closePreview=()=>{const key=state.current.preview?.key;setPreview(null);if(key)focusNode(key)};
 const closeMenu=(refocus=true)=>{const from=state.current.menu?.from;setMenu(null);if(refocus)requestAnimationFrame(()=>from?.focus())};
 // Escape peels one layer: the menu, then the agent chat, then the popup.
 useEffect(()=>{const key=(e:KeyboardEvent)=>{
  if(e.key==='Escape'&&!e.defaultPrevented){
   if(state.current.menu){e.preventDefault();closeMenu();return}
   if(state.current.preview){e.preventDefault();closePreview();return}
   if(mode==='popup'){e.preventDefault();close.current()}return;
  }
  if(mode!=='popup'||e.key!=='Tab'||!dialog.current)return;const items=Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')).filter(el=>el.getClientRects().length);if(!items.length){e.preventDefault();return}const first=items[0],last=items[items.length-1];if(e.shiftKey&&(document.activeElement===first||document.activeElement===dialog.current)){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key)},[mode]);
 useEffect(()=>{if(mode!=='popup')return;const previous=document.activeElement as HTMLElement|null;dialog.current?.focus();return()=>{previous?.focus()}},[mode]);
 // Escape is handled above, so a host's Escape-driven dismissal must not close the whole popup.
 useEffect(()=>{if(mode!=='popup')return;const dismiss=(e:Event)=>{if((e as CustomEvent).detail!=='escape')close.current()};window.addEventListener('workbench-dismiss-popovers',dismiss);return()=>window.removeEventListener('workbench-dismiss-popovers',dismiss)},[mode]);
 useLayoutEffect(()=>{if(!menu||!menuRef.current)return;const box=menuRef.current.getBoundingClientRect();if(box.right>innerWidth-8||box.bottom>innerHeight-8)setMenu(m=>m&&{...m,x:Math.max(8,Math.min(m.x,innerWidth-box.width-8)),y:Math.max(8,Math.min(m.y,innerHeight-box.height-8))});menuRef.current.querySelector<HTMLElement>('[role=menuitem]')?.focus()},[menu?.node?.key,menu?.x,menu?.y]);
 useEffect(()=>{if(!menu)return;const down=(e:PointerEvent)=>{if(!menuRef.current?.contains(e.target as Node))closeMenu(false)};document.addEventListener('pointerdown',down,true);return()=>document.removeEventListener('pointerdown',down,true)},[menu]);
 // My prompts opens at the newest prompt, at the bottom.
 useLayoutEffect(()=>{if(tab==='prompts'&&promptList.current)promptList.current.scrollTop=promptList.current.scrollHeight},[tab,prompts?.length]);
 const nodes=tree?.nodes||[],edges=tree?.edges||[];
 const items=orderAgentTree(nodes,edges,node=>agentMapStatus(node,runs,live)),counts=agentCounts(items);
 const flat:any[]=[];const walk=(list:AgentItem[])=>list.forEach(i=>{flat.push(i.node);walk(i.children)});walk(items);
 const tabStop=flat.some(n=>n.key===focusKey&&!n.missing)?focusKey:flat.find(n=>!n.missing)?.key;
 function choose(node:any){if(node.missing)return;if(node.key===selected){setPreview(null);if(mode==='popup')onClose();return}if(!renderChat){onOpen(node,'tab');return}setPreview(node)}
 function act(node:any,where:Exclude<OpenWhere,'preview'>){setMenu(null);onOpen(node,where)}
 function openMenu(node:any,e:React.MouseEvent|React.KeyboardEvent,from?:HTMLElement|null){e.preventDefault();e.stopPropagation();const target=(from||e.currentTarget) as HTMLElement,box=target.getBoundingClientRect();const pointer='clientX' in e&&e.clientX?{x:e.clientX,y:e.clientY}:{x:box.left+12,y:box.bottom+2};setMenu({node,...pointer,from:target})}
 function rowKey(e:React.KeyboardEvent<HTMLButtonElement>,node:any){
  if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){openMenu(node,e);return}
  const enabled=flat.filter(n=>!n.missing),i=enabled.findIndex(n=>n.key===node.key);
  const next=e.key==='ArrowDown'?enabled[i+1]:e.key==='ArrowUp'?enabled[i-1]:e.key==='Home'?enabled[0]:e.key==='End'?enabled[enabled.length-1]:null;
  if(next){e.preventDefault();setFocusKey(next.key);focusNode(next.key)}
 }
 function branch(item:AgentItem,depth:number):React.ReactNode{
  const {node,status,children}=item,label=agentStatusLabel(node,status),current=node.key===selected,previewed=node.key===preview?.key;
  const meta=[current?'This chat':'',node.agent_label&&node.agent_label!==node.title?node.agent_label:'',node.source==='codex'?'Codex':node.source==='claude'?'Claude':node.source,node.missing?'Transcript unavailable':ago(newest(node))].filter(Boolean).join(' · ');
  return <li key={node.key}><div className={'agent-map-row'+(previewed?' is-selected':'')} style={{'--depth':depth} as React.CSSProperties}>
   <button type="button" className="agent-map-node" data-key={node.key} tabIndex={node.key===tabStop?0:-1} disabled={node.missing} aria-current={current?'page':undefined} aria-pressed={renderChat?previewed:undefined} onFocus={()=>setFocusKey(node.key)} onClick={()=>choose(node)} onContextMenu={e=>{if(!node.missing&&!current)openMenu(node,e)}} onKeyDown={e=>rowKey(e,node)}>
    <span title={node.activity_evidence||label}><StatusMark compact status={markOf(node,status)}/></span><span className="agent-map-text"><strong>{node.title||node.key}</strong><small><span className={'agent-map-state '+status}>{label}</span>{meta?' · '+meta:''}</small></span>
   </button>
   {!node.missing&&!current&&<button type="button" className="agent-map-more" tabIndex={-1} aria-label={'More actions for '+(node.title||node.key)} aria-haspopup="menu" onClick={e=>openMenu(node,e)}><MoreHorizontal size={14}/></button>}
  </div>{children.length>0&&<ul>{children.map(c=>branch(c,depth+1))}</ul>}</li>;
 }
 const sc:Record<string,number>=tree?.counts?{running:tree.counts.working,waiting:tree.counts.waiting,idle:tree.counts.idle,finished:tree.counts.finished,stopped:tree.counts.stopped,failed:tree.counts.failed,unknown:tree.counts.unknown}:counts;
 const summary=[sc.running&&sc.running+' working',sc.waiting&&sc.waiting+' waiting',sc.idle&&sc.idle+' idle',sc.finished&&sc.finished+' finished',sc.stopped&&sc.stopped+' stopped',sc.failed&&sc.failed+' failed',sc.unknown&&sc.unknown+' unknown'].filter(Boolean).join(', ');
 const treeColumn=<div className="agent-map-tree">
  <header><div>{prompts?<div className="agent-map-tabs" role="tablist" aria-label="Agent tree views"><button type="button" role="tab" id={titleId} aria-selected={tab==='tree'} onClick={()=>setTab('tree')}>Agent tree</button><button type="button" role="tab" aria-selected={tab==='prompts'} onClick={()=>setTab('prompts')}>My prompts</button></div>:<h2 id={titleId}>Agent tree</h2>}<p>{tab==='prompts'?`${prompts?.length||0} ${prompts?.length===1?'prompt':'prompts'} in this chat`:tree?summary||'No agents':'Loading…'}</p></div>{mode==='popup'&&<button type="button" className="agent-map-icon" aria-label="Close agent tree" title="Close (Esc)" onClick={onClose}><X size={16}/></button>}</header>
  {tab==='prompts'?(prompts?.length?<ol className="agent-map-scroll agent-map-prompts" ref={promptList} aria-label="My prompts">{prompts.map((p,i)=><li key={i}><button type="button" title={p.text.slice(0,600)} onClick={()=>onPrompt?.(i)}><span>{firstLine(p.text,200)}</span>{p.ts&&Date.parse(p.ts)?<small>{new Date(p.ts).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</small>:null}</button></li>)}</ol>:<p className="agent-map-note">No prompts in this chat yet.</p>):<>
  {error&&<p role="alert" className="agent-map-note">{error} <button type="button" onClick={()=>setRetry(v=>v+1)}>Retry</button></p>}
  {!tree&&!error&&<p role="status" className="agent-map-note">Loading agent tree…</p>}
  {tree&&<div className="agent-map-scroll">{items.length?<ul className="agent-map-roots" aria-labelledby={titleId}>{items.map(i=>branch(i,0))}</ul>:<p className="agent-map-note">No explicit agent lineage found.</p>}</div>}
  </>}
 </div>;
 const previewStatus=preview&&(flat.find(n=>n.key===preview.key)?agentMapStatus(flat.find(n=>n.key===preview.key),runs,live):'unknown');
 const chat=preview&&renderChat?<div className="agent-map-chat" key={preview.key}>{renderChat(chatResource(preview),{
  status:<span className={'agent-map-chat-status '+previewStatus} title={preview.activity_evidence||''}><StatusMark compact status={markOf(preview,previewStatus as AgentStatus)}/>{agentStatusLabel(preview,previewStatus as AgentStatus)}</span>,
  lead:<button type="button" className="agent-map-icon agent-map-back" aria-label="Back to agent tree" title="Back to agent tree" onClick={closePreview}><ChevronLeft size={16}/></button>,
  actions:<span className="agent-map-chat-actions"><button type="button" className="agent-map-open" aria-label="Open in tab" title="Open in a new tab in this pane" onClick={()=>act(preview,'tab')}><ExternalLink size={13}/><span>Open in tab</span></button><button type="button" className="agent-map-icon" aria-label="More ways to open this agent" aria-haspopup="menu" onClick={e=>openMenu(preview,e)}><MoreHorizontal size={15}/></button><button type="button" className="agent-map-icon" aria-label="Close agent chat" title="Close agent chat (Esc)" onClick={closePreview}><X size={16}/></button></span>})}</div>:null;
 const menuNode=menu&&<div ref={menuRef} className="context-menu agent-map-menu" role="menu" aria-label={'Open '+(menu.node.title||'agent')} style={{left:menu.x,top:menu.y}} onKeyDown={e=>{const list=Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role=menuitem]')||[]),i=list.indexOf(document.activeElement as HTMLElement);if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();list[(i+(e.key==='ArrowDown'?1:list.length-1))%list.length]?.focus()}else if(e.key==='Tab'){e.preventDefault();closeMenu()}}}>
  <button type="button" role="menuitem" onClick={()=>act(menu.node,'tab')}>Open in new tab</button>
  <button type="button" role="menuitem" onClick={()=>act(menu.node,'current')}>Open in current tab</button>
 </div>;
 const content=<div className={'agent-map'+(mode==='inline'?' is-inline':'')+(chat?' has-chat':'')} role={mode==='popup'?'dialog':'region'} aria-modal={mode==='popup'?true:undefined} aria-label="Agent tree" ref={dialog} tabIndex={-1} onPointerDown={mode==='inline'?e=>e.stopPropagation():undefined}>{treeColumn}{chat}{menuNode}</div>;
 return mode==='popup'?createPortal(<div className="agent-map-overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}>{content}</div>,document.body):content;
}
