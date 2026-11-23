import React,{createContext,useContext,useEffect,useMemo,useState,type ReactNode} from 'react';
import {api} from '../api/client';
import {Check} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {effectiveWorkspaceChoice,newerOutsideWorkspace,savedWorkspaceChoice,workspaceKey,workspaceRoots,type WorkspaceRoot} from './workspaceScope';
import {directoryChats} from './directoryScope';
import './sidebar-actions.css';

// v2 holds choices made after the November 20, 2026 switch to All chats; the old key is read once.
const legacyKey='workbench-chat-workspace',storageKey='workbench-chat-workspace-v2';
const readSaved=()=>{try{return savedWorkspaceChoice(localStorage.getItem(storageKey),localStorage.getItem(legacyKey));}catch{return 'entire';}};
const choiceEvent='library-workspace-choice';
type Selection={roots:WorkspaceRoot[];choice:string;choose:(value:string)=>void;error:string};
const WorkspaceContext=createContext<Selection>({roots:[],choice:'entire',choose:()=>{},error:''});

export function WorkspaceScopeProvider({children,enabled=true}:{children:ReactNode;enabled?:boolean}){
 const rpc=(window as any).libraryRpc;
 const [roots,setRoots]=useState<WorkspaceRoot[]>([]);
 const [saved,setSaved]=useState<string>(()=>rpc?(window as any).__LIBRARY_BOOT__?.workspaceChoice||'entire':readSaved());
 const [rootError,setRootError]=useState(''),[choiceError,setChoiceError]=useState('');
 const choice=effectiveWorkspaceChoice(saved,roots);
 // Index new transcripts when the page opens; the revision poll then refetches the list. The native app asks from its extension host.
 useEffect(()=>{if(!rpc)void api('/api/sync',{}).catch(()=>{});},[rpc]);
 useEffect(()=>{
  if(!enabled)return;
  let alive=true,epoch=0;
  const load=async()=>{
   const request=++epoch;
   try{
    const value=rpc?await rpc('workspaceRoots'):await api('/api/files/roots');
    if(!alive||request!==epoch)return;
    const rows=value.roots||[],next=workspaceRoots(rows);
    setRoots(previous=>JSON.stringify(previous)===JSON.stringify(next)?previous:next);
    setRootError(rows.some((root:WorkspaceRoot)=>root.scope_known===false)?'Some workspace folders could not be identified.':'');
   }catch(error){if(alive&&request===epoch)setRootError(error.message);}
  };
  const receive=(e:Event)=>{setSaved((e as CustomEvent<string>).detail);setChoiceError('');};
  const sync=(e:StorageEvent)=>{if(e.key===storageKey)setSaved(e.newValue||'entire');};
  const events=['library-refresh','library-workspaces-changed','focus'];
  void load();
  for(const event of events)window.addEventListener(event,load);
  window.addEventListener(choiceEvent,receive);
  if(!rpc)window.addEventListener('storage',sync);
  return ()=>{
   alive=false;
   for(const event of events)window.removeEventListener(event,load);
   window.removeEventListener(choiceEvent,receive);
   if(!rpc)window.removeEventListener('storage',sync);
  };
 },[enabled,rpc]);
 const choose=(value:string)=>{
  setChoiceError('');
  if(rpc){void rpc('setWorkspaceChoice',{choice:value}).catch((error:Error)=>setChoiceError(error.message));return;}
  setSaved(value);
  try{localStorage.setItem(storageKey,value);}catch(error){setChoiceError(error.message);}
 };
 return <WorkspaceContext.Provider value={{roots,choice,choose,error:choiceError||rootError}}>{children}</WorkspaceContext.Provider>;
}

export const useWorkspaceScope=()=>useContext(WorkspaceContext);

const rootName=(root:WorkspaceRoot)=>root.label||root.path.split('/').filter(Boolean).at(-1)||root.path;

export function WorkspaceSelector({label='Conversation workspace',className}: {label?:string;className?:string}){
 const {roots,choice,choose,error}=useWorkspaceScope();
 const wb=useWorkbench(),hosts=wb?.hosts||[],chats=wb?.data?.chats;
 const hidden=useMemo(()=>choice==='entire'||!chats?0:newerOutsideWorkspace(directoryChats(chats,{roots,choice:'entire'}),roots,choice),[chats,roots,choice]);
 const optionLabel=(root:WorkspaceRoot)=>{
  const host=hosts.find(h=>h.id===root.host_id)?.label||(root.host_id==='mac'?'This Mac':root.host_id);
  const name=rootName(root),withHost=name.endsWith(' · '+host)?name:name+' · '+host;
  const duplicate=roots.some(other=>other!==root&&other.host_id===root.host_id&&rootName(other)===name);
  return duplicate?withHost+' · '+root.path:withHost;
 };
 return <div className={className}>
  <select aria-label={label} value={choice} onChange={e=>choose(e.target.value)}>
   <option value="entire">All chats</option>
   {roots.length>0&&<option value="added">All added workspaces</option>}
   {roots.map(root=><option key={workspaceKey(root)} value={workspaceKey(root)}>{optionLabel(root)}</option>)}
  </select>
  {hidden>0&&<p className="workspace-hidden-note">{hidden} newer {hidden===1?'chat':'chats'} outside this workspace · <button type="button" className="text-button" onClick={()=>choose('entire')}>Show all</button></p>}
  {error&&<p className="sidebar-workspace-error" role="status">{error}</p>}
 </div>;
}

// A project's own workspace: a saved folder or the cluster, set without opening it. Choices come from the server.
export function ProjectWorkspacePicker({project,x,y,onClose}:{project:any;x:number;y:number;onClose:()=>void}){
 const wb=useWorkbench(),hosts=wb?.hosts||[];
 const [info,setInfo]=useState<any>(null),[error,setError]=useState(''),[typed,setTyped]=useState(''),[host,setHost]=useState('mac'),[busy,setBusy]=useState(false);
 const ref=React.useRef<HTMLDivElement>(null);
 useEffect(()=>{let alive=true;api('/api/project_workspace?id='+encodeURIComponent(project.id)+'&t='+Date.now()).then(v=>{if(alive)setInfo(v);}).catch(e=>{if(alive)setError(e.status===404?'Choosing a workspace needs a newer Library server.':e.message);});return()=>{alive=false;};},[project.id]);
 useEffect(()=>{const outside=(e:PointerEvent)=>{if(!ref.current?.contains(e.target as Node))onClose();},key=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();};document.addEventListener('pointerdown',outside,true);window.addEventListener('keydown',key);return()=>{document.removeEventListener('pointerdown',outside,true);window.removeEventListener('keydown',key);};},[onClose]);
 const hostName=(id:string)=>hosts.find((h:any)=>h.id===id)?.label||(id==='mac'?'This Mac':id);
 // The saved choice always appears and is checked, even if the list came from an older answer.
 // The Library state is the fresher source: the choices answer can lag a save by a few seconds.
 const current=('default_workspace' in project?project.default_workspace:info?.current)||null;
 const choices:any[]=info?[...(info.choices||[])]:[];
 if(current&&!choices.some(c=>c.host_id===current.host_id&&c.path===current.path))choices.unshift({host_id:current.host_id,path:current.path,source:'saved',label:'Saved workspace'});
 async function save(body:any,done:string){setBusy(true);try{await api('/api/project_catalog',{action:'workspace',id:project.id,...body});await wb.refresh();wb.notify(done);onClose();}catch(e:any){setError(e.message);setBusy(false);}}
 const hostOptions=[...new Set(['mac',...hosts.map((h:any)=>h.id),...(info?.choices||[]).map((c:any)=>c.host_id)])].filter(Boolean) as string[];
 return <div ref={ref} className="context-menu sidebar-chat-menu workspace-picker" role="dialog" aria-label={'Workspace for '+project.name} style={{left:Math.max(8,Math.min(x,innerWidth-Math.min(324,innerWidth-16)-8)),top:Math.max(8,Math.min(y,innerHeight-420)),width:Math.min(324,innerWidth-16)}}>
  <div className="menu-heading">Workspace for {project.name}</div>
  {!info&&!error&&<p className="empty-note">Loading choices…</p>}
  {error&&<p className="inline-error" role="status">{error}</p>}
  {info&&<div className="move-list">{choices.map((c:any)=>{const on=current&&current.host_id===c.host_id&&current.path===c.path;return <button key={c.host_id+':'+c.path} role="menuitemradio" aria-checked={!!on} disabled={busy} title={c.path} onClick={()=>void save({host_id:c.host_id,path:c.path},'Workspace set to '+(c.label||c.path))}><span className="menu-check">{on&&<Check size={14}/>}</span><span className="menu-label">{c.label||c.path.split('/').filter(Boolean).at(-1)||c.path}<small>{hostName(c.host_id)} · {c.path}{c.n_chats?' · '+c.n_chats+(c.n_chats===1?' chat':' chats'):''}</small></span></button>;})}
   <button role="menuitemradio" aria-checked={!current} disabled={busy||!current} onClick={()=>void save({clear:true},'Workspace cleared')}><span className="menu-check">{!current&&<Check size={14}/>}</span><span className="menu-label">None</span></button></div>}
  <form className="workspace-typed" onSubmit={e=>{e.preventDefault();const path=typed.trim();if(!path.startsWith('/')){setError('Type an absolute path that starts with /.');return;}void save({host_id:host,path},'Workspace set to '+path);}}>
   <select aria-label="Host" value={host} onChange={e=>setHost(e.target.value)}>{hostOptions.map(id=><option key={id} value={id}>{hostName(id)}</option>)}</select>
   <input aria-label="Folder path" placeholder="/path/to/folder" value={typed} onChange={e=>setTyped(e.target.value)}/>
   <button type="submit" className="text-button" disabled={busy||!typed.trim()}>Set</button>
  </form>
 </div>;
}
