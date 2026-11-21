import React,{createContext,useContext,useEffect,useMemo,useState,type ReactNode} from 'react';
import {api} from '../api/client';
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
