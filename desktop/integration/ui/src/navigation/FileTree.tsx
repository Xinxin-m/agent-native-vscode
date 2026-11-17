import React,{useState,useEffect,useRef} from 'react';
import {Folder,File,ChevronRight,ChevronDown,RefreshCw,Search,X,PanelRight} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {api,query,ApiError} from '../api/client';
import {coveringRoot} from '../viewers/helpers';
import {relativeFilePath} from '../state/workspace';
import {parseFileLink} from '../open/links';
import {refusal,moving,confirmation,parentPath,place,type DragItem,type Target,type Moved} from './fileMoves';
import type {Resource} from '../state/types';
import './file-tree.css';

const keyOf=(rid:string,path:string)=>rid+'\n'+path;
type Row={key:string,rid:string,path:string,name:string,type:string,depth:number};
type Ask={message:string,buttons:string[],resolve:(choice:string)=>void};
// A Files tab shows one workspace (or 'all'); '' means none chosen yet.
type Tab={root:string,storageKey:string,choose:(root:string,title:string)=>void};
// Shared by the side menu and every Files tab, so a drag can end in another tree.
const dragging:{current:DragItem[]|null}={current:null};
const announce=(moves:Moved[],touched:string[])=>window.dispatchEvent(new CustomEvent('library-files-moved',{detail:{moves,touched}}));

// All workspace roots in one tree. Drag moves files and folders within and across roots and trees; Alt copies.
export function FileTree({onContext,tab}:{onContext:any,tab?:Tab}){
 const {hosts,open,preferences}=useWorkbench(),hidden=(path:string)=>!preferences.showHidden&&/(^|\/)\./.test(path);
 const [roots,setRoots]=useState<any[]>([]),[host,setHost]=useState('mac'),[input,setInput]=useState(''),[error,setError]=useState('');
 const [expanded,setExpanded]=useState<Set<string>>(()=>{try{return new Set(JSON.parse(localStorage.getItem(tab?.storageKey||'workbench-file-tree')||'[]'));}catch{return new Set();}});
 const [children,setChildren]=useState<Record<string,{entries:any[],next:string,error:string,busy:boolean}>>({});
 const [selected,setSelected]=useState<string[]>([]),[anchor,setAnchor]=useState(''),[dropKey,setDropKey]=useState(''),[dropBad,setDropBad]=useState(false);
 const [fileSearch,setFileSearch]=useState(''),[searchOpen,setSearchOpen]=useState(false),[results,setResults]=useState<any[]|null>(null),[truncated,setTruncated]=useState(false),[focusRoot,setFocusRoot]=useState('');
 const [ask,setAsk]=useState<Ask|null>(null),[toast,setToast]=useState<{text:string,token?:string,refresh?:string[]}|null>(null);
 const lastMove=useRef<{token:string,refresh:string[],moves:Moved[]}|null>(null),spring=useRef<{key:string,timer:any}|null>(null);
 const rootOf=(rid:string)=>roots.find(r=>(r.id||r.root_id)===rid);
 const hostLabel=(id:string)=>hosts.find((h:any)=>h.id===id)?.label||id;
 const rootLabel=(rid:string)=>rootOf(rid)?.label||'workspace';

 async function load(key:string,cursor=''){const [rid,path]=key.split('\n');setChildren(c=>({...c,[key]:{entries:cursor?c[key]?.entries||[]:c[key]?.entries||[],next:'',error:'',busy:true}}));
  try{const d=await api(query('/api/files/list',{root_id:rid,path,cursor}));const items=Array.isArray(d.entries)?d.entries:[];setChildren(c=>({...c,[key]:{entries:cursor?[...(c[key]?.entries||[]),...items]:items,next:d.next_cursor??'',error:'',busy:false}}));}
  catch(e){setChildren(c=>({...c,[key]:{entries:[],next:'',error:e.message,busy:false}}));}}
 async function loadRoots(){try{const d=await api('/api/files/roots');const list=Array.isArray(d.roots)?d.roots:[];setRoots(list);setFocusRoot(f=>f||list[0]?.id||'');setExpanded(v=>v.size?v:new Set(list.filter((r:any)=>r.host_id==='mac').map((r:any)=>keyOf(r.id,''))));}catch(e){setError(e.message);}}
 useEffect(()=>{void loadRoots();},[]);
 useEffect(()=>{localStorage.setItem(tab?.storageKey||'workbench-file-tree',JSON.stringify([...expanded]));for(const key of expanded)if(!children[key]&&rootOf(key.split('\n')[0]))void load(key);},[expanded,roots]);
 useEffect(()=>{if(!focusRoot||!fileSearch.trim()){setResults(null);setTruncated(false);return;}let live=true;const timer=setTimeout(async()=>{try{const r=await api(query('/api/files/search',{root_id:focusRoot,q:fileSearch.trim()}));if(live){setResults(r.entries||[]);setTruncated(!!r.truncated);}}catch(e){if(live){setResults([]);setError(e.message);}}},180);return()=>{live=false;clearTimeout(timer);};},[focusRoot,fileSearch]);
 const toggle=(key:string,force?:boolean)=>setExpanded(v=>{const n=new Set(v);(force??!n.has(key))?n.add(key):n.delete(key);return n;});
 const refreshAll=()=>{void loadRoots();for(const key of expanded)void load(key);};
 const latestRefresh=useRef(refreshAll);latestRefresh.current=refreshAll;
 useEffect(()=>{const moved=(e:any)=>e.detail.touched.forEach((k:string)=>void load(k)),refresh=()=>latestRefresh.current();
  window.addEventListener('library-files-moved',moved);window.addEventListener('library-refresh',refresh);return()=>{window.removeEventListener('library-files-moved',moved);window.removeEventListener('library-refresh',refresh);};},[]);

 // A tab showing one workspace names it in the chooser, so its tree starts at the workspace's contents.
 const single=tab?.root&&tab.root!=='all'?tab.root:'';
 useEffect(()=>{if(single)toggle(keyOf(single,''),true);},[single]);
 const rows:Row[]=[];
 if(results){for(const e of results.filter(e=>!hidden(e.path)))rows.push({key:keyOf(focusRoot,e.path),rid:focusRoot,path:e.path,name:e.path,type:e.type,depth:0});}
 else{const walk=(rid:string,path:string,depth:number)=>{for(const e of (children[keyOf(rid,path)]?.entries||[]).filter((e:any)=>!hidden(e.path))){const key=keyOf(rid,e.path);rows.push({key,rid,path:e.path,name:e.name,type:e.type,depth});if(e.type==='directory'&&expanded.has(key))walk(rid,e.path,depth+1);}};
  for(const r of roots){const rid=r.id||r.root_id;if(single){if(rid===single)walk(rid,'',0);continue;}rows.push({key:keyOf(rid,''),rid,path:'',name:r.label||r.path,type:'root',depth:0});if(expanded.has(keyOf(rid,''))&&tab?.root!=='')walk(rid,'',1);}}
 const resource=(row:Row):Resource=>{const r=rootOf(row.rid);return {kind:'file',host_id:r?.host_id||'mac',root_id:row.rid,path:(r?.path||'').replace(/\/$/,'')+'/'+row.path,title:row.name.split('/').at(-1)};};
 const folderOf=(row:Row):Target=>{const path=row.type==='file'?parentPath(row.path):row.path;return {rid:row.rid,path,host:rootOf(row.rid)?.host_id||'mac',label:rootLabel(row.rid)};};

 function click(e:React.MouseEvent,row:Row){setFocusRoot(row.rid);
  if(tab&&!tab.root){tab.choose(row.rid,row.name);return;}
  if(e.metaKey||e.ctrlKey){setSelected(s=>s.includes(row.key)?s.filter(k=>k!==row.key):[...s,row.key]);setAnchor(row.key);return;}
  if(e.shiftKey&&anchor){const a=rows.findIndex(r=>r.key===anchor),b=rows.findIndex(r=>r.key===row.key);if(a>=0&&b>=0){setSelected(rows.slice(Math.min(a,b),Math.max(a,b)+1).filter(r=>r.type!=='root').map(r=>r.key));return;}}
  setSelected([row.key]);setAnchor(row.key);
  if(row.type==='file')open(resource(row));else toggle(row.key);}

 const choose=(message:string,buttons:string[])=>new Promise<string>(resolve=>setAsk({message,buttons,resolve:choice=>{setAsk(null);resolve(choice);}}));
 const say=(text:string,token?:string,refresh?:string[])=>setToast({text,token,refresh});
 useEffect(()=>{if(toast){const t=setTimeout(()=>setToast(null),toast.token?12000:6000);return()=>clearTimeout(t);}},[toast]);

 async function drop(items:DragItem[],target:Target,copy:boolean){
  const why=refusal(items,target,hostLabel);if(why){say(why);return;}
  const list=moving(items,target,copy);if(!list.length)return;
  const question=confirmation(list,target,rootLabel,copy,preferences.confirmMoves);if(question&&await choose(question,['Move','Cancel'])!=='Move')return;
  const touched=[...new Set([keyOf(target.rid,target.path),...list.map(i=>keyOf(i.rid,parentPath(i.path)))])];
  const body={items:list.map(i=>({root_id:i.rid,path:i.path})),dest_root_id:target.rid,dest_dir:target.path,copy};
  try{let result;
   try{result=await api('/api/files/move',{...body,conflict:'ask'});}
   catch(e){if(e?.code!=='destination_exists')throw e;const names:string[]=e.data?.names||[];
    const choice=await choose(`${names.length===1?`"${names[0]}" already exists`:`${names.length} items already exist`} in ${place(target.label,target.path)}.`,['Keep both','Replace','Cancel']);
    if(choice==='Cancel')return;result=await api('/api/files/move',{...body,conflict:choice==='Replace'?'replace':'keep_both'});}
   const abs=(rid:string,path:string)=>(rootOf(rid)?.path||'').replace(/\/$/,'')+(path?'/'+path:'');
   const moves:Moved[]=copy?[]:result.moved.map((m:any)=>({from:abs(m.root_id,m.path),to:abs(m.dest_root_id,m.dest_path),root_id:m.dest_root_id}));
   announce(moves,touched);toggle(keyOf(target.rid,target.path),true);setSelected(result.moved.map((m:any)=>keyOf(m.dest_root_id,m.dest_path)));
   const what=list.length===1?`"${list[0].name}"`:list.length+' items';
   lastMove.current=result.token?{token:result.token,refresh:touched,moves:moves.map(m=>({from:m.to,to:m.from,root_id:list.find(i=>abs(i.rid,i.path)===m.from)?.rid||m.root_id}))}:null;
   say(`${copy?'Copied':'Moved'} ${what} to ${place(target.label,target.path)}.`,result.token||undefined,touched);
  }catch(e){say(e?.status===404&&!e.code?'Moving files needs a restart of the Library server.':e.message);touched.forEach(k=>void load(k));}}
 async function undo(){if(!lastMove.current)return;const {token,refresh,moves}=lastMove.current;lastMove.current=null;try{await api('/api/files/move/undo',{token});say('Undone.');announce(moves,refresh);}catch(e){say(e.message);announce([],refresh);}}

 function dragStart(e:React.DragEvent,row:Row){
  const picked=(selected.includes(row.key)?rows.filter(r=>selected.includes(r.key)):[row]).filter(r=>r.type!=='root');
  if(!picked.length){e.preventDefault();return;}
  if(!selected.includes(row.key))setSelected([row.key]);
  dragging.current=picked.map(r=>({rid:r.rid,path:r.path,name:r.name.split('/').at(-1)||r.name,type:r.type,host:rootOf(r.rid)?.host_id||'mac'}));
  e.dataTransfer.setData('application/library-files',JSON.stringify(dragging.current));
  if(picked.length===1&&picked[0].type==='file')e.dataTransfer.setData('application/library-resource',JSON.stringify(resource(picked[0])));
  e.dataTransfer.effectAllowed='copyMove';}
 // Another webview's drag only shows its data on drop, so accept it by type until then.
 const foreign=(e:React.DragEvent)=>!dragging.current&&['application/library-files','codefiles'].some(t=>[...e.dataTransfer.types].map(x=>x.toLowerCase()).includes(t));
 function transferred(e:React.DragEvent):DragItem[]{try{const raw=e.dataTransfer.getData('application/library-files');if(raw)return JSON.parse(raw);
  return (JSON.parse(e.dataTransfer.getData('CodeFiles')||'[]') as string[]).flatMap(p=>{const r=coveringRoot(roots.filter(r=>r.host_id==='mac'),'mac',p);return r?[{rid:r.id||r.root_id,path:relativeFilePath(p,r.path),name:p.split('/').at(-1)||p,type:'file',host:'mac'}]:[];});}catch{return [];}}
 function dragOver(e:React.DragEvent,row:Row){if(!dragging.current&&!foreign(e))return;e.preventDefault();e.stopPropagation();
  const target=folderOf(row),key=keyOf(target.rid,target.path);e.dataTransfer.dropEffect=e.altKey?'copy':'move';setDropKey(key);setDropBad(!!dragging.current&&!!refusal(dragging.current,target,hostLabel));
  if((row.type==='directory'||row.type==='root')&&!expanded.has(row.key)&&spring.current?.key!==row.key){clearTimeout(spring.current?.timer);spring.current={key:row.key,timer:setTimeout(()=>toggle(row.key,true),600)};}}
 function dragEnd(){dragging.current=null;setDropKey('');clearTimeout(spring.current?.timer);spring.current=null;}
 function dropOn(e:React.DragEvent,row:Row){if(!dragging.current&&!foreign(e))return;e.preventDefault();e.stopPropagation();const items=dragging.current||transferred(e);dragEnd();if(items.length)void drop(items,folderOf(row),e.altKey);}

 async function openPath(e:any){e.preventDefault();if(!input.trim())return;const focus=rootOf(focusRoot),candidate=parseFileLink(input.trim(),focus?.path||'',host);if(!candidate?.path){setError('Choose a file or folder path.');return;}setError('');const match=coveringRoot(roots.filter(r=>r.host_id===host),host,candidate.path);try{
  if(match){const rid=match.id||match.root_id,relative=relativeFilePath(candidate.path,match.path);const reveal=(path:string)=>{const parts=path.split('/');setExpanded(v=>{const n=new Set(v);n.add(keyOf(rid,''));parts.forEach((_,i)=>n.add(keyOf(rid,parts.slice(0,i+1).join('/'))));return n;});setSelected([keyOf(rid,path)]);setFocusRoot(rid);setInput('');};
   if(relative==='.'){reveal('');return;}
   try{const result=await api(query('/api/files/stat',{root_id:rid,path:relative}));open({...candidate,root_id:rid,file_id:result.resource.file_id});setInput('');return;}
   catch(error){if(!(error instanceof ApiError)||error.status!==415)throw error;await api(query('/api/files/list',{root_id:rid,path:relative}));reveal(relative);return;}}
  const d=await api('/api/files/roots',{host_id:host,path:candidate.path,access:host==='mac'?'write':'read'});setRoots(v=>[...v.filter(r=>r.id!==d.root.id),d.root]);toggle(keyOf(d.root.id,''),true);setFocusRoot(d.root.id);setInput('');
 }catch(error){setError(error.message);}}

 return <>
  {tab?<div className="file-location"><select aria-label="Workspace" value={tab.root} onChange={e=>tab.choose(e.target.value,e.target.value==='all'?'Files':rootLabel(e.target.value))}>{!tab.root&&<option value="">Choose a workspace</option>}{roots.map(r=><option key={r.id||r.root_id} value={r.id||r.root_id}>{r.label||r.path}</option>)}<option value="all">All workspaces</option></select></div>:<>
  <div className="file-location"><select aria-label="File location" value={host} onChange={e=>setHost(e.target.value)}>{hosts.filter(h=>h.configured!==false).map(h=><option key={h.id} value={h.id}>{h.label||h.id}</option>)}</select><button className="icon-button" title="Find filenames" aria-label={searchOpen?'Close filename search':'Find filenames in workspace'} onClick={()=>{setSearchOpen(v=>!v);setFileSearch('');}}><Search size={14}/></button><button className="icon-button" title="Refresh files" aria-label="Refresh files" onClick={refreshAll}><RefreshCw size={14}/></button></div>
  <form onSubmit={openPath} className="search-field"><Folder size={14}/><input aria-label="Open file or folder path" placeholder="Open a file or folder…" value={input} onChange={e=>setInput(e.target.value)}/></form>
  {searchOpen&&<div className="file-breadcrumb"><input autoFocus aria-label="Find filenames in workspace" placeholder={'Find filenames in '+rootLabel(focusRoot)+'…'} value={fileSearch} onChange={e=>setFileSearch(e.target.value)} onKeyDown={e=>{if(e.key==='Escape'){setSearchOpen(false);setFileSearch('');}}}/></div>}</>}
  <div className={'sidebar-scroll file-tree'+(tab?' files-tab':'')} role="tree" aria-label="Files" aria-multiselectable="true" onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDropKey('');}} onDragOver={e=>single&&dragOver(e,{key:keyOf(single,''),rid:single,path:'',name:'',type:'root',depth:0})} onDrop={e=>single&&dropOn(e,{key:keyOf(single,''),rid:single,path:'',name:'',type:'root',depth:0})} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'&&!e.shiftKey&&lastMove.current){e.preventDefault();void undo();}}}>
   {error&&<p className="inline-error">{error}<button onClick={()=>{setError('');refreshAll();}}>Retry</button></p>}
   {rows.map(row=>{const info=children[row.key],folder=row.type==='directory'||row.type==='root',open_=expanded.has(row.key)&&!(tab&&!tab.root),inDrop=row.type!=='root'&&!!dropKey&&dropKey===keyOf(row.rid,parentPath(row.path));
    const line=<button role="treeitem" aria-selected={selected.includes(row.key)} aria-expanded={folder?open_:undefined} className={'file-row'+(row.type==='root'?' file-root':'')+(selected.includes(row.key)?' is-selected':'')+(dropKey===row.key?(dropBad?' is-drop-bad':' is-drop'):'')+(inDrop?' in-drop':'')} style={{paddingLeft:8+row.depth*14}} draggable={row.type!=='root'} onDragStart={e=>dragStart(e,row)} onDragOver={e=>dragOver(e,row)} onDrop={e=>dropOn(e,row)} onDragEnd={dragEnd} onClick={e=>click(e,row)} onDoubleClick={()=>row.type==='file'&&open(resource(row),'tab')} onContextMenu={ev=>row.type!=='root'&&onContext(ev,resource(row))} title={row.type==='root'?rootOf(row.rid)?.path+(rootOf(row.rid)?.host_id!=='mac'?' · '+hostLabel(rootOf(row.rid)?.host_id)+' · read-only':''):row.name}>
     {folder?(open_?<ChevronDown size={13}/>:<ChevronRight size={13}/>):<i className="file-row-spacer"/>}{folder?<Folder size={15}/>:<File size={15}/>}<span>{row.name}</span>{row.type==='root'&&rootOf(row.rid)?.host_id!=='mac'&&<small>{hostLabel(rootOf(row.rid)?.host_id)}</small>}</button>;
    return <React.Fragment key={row.key}>{row.type==='root'&&!tab?<div className="file-root-line">{line}<button className="icon-button file-root-open" title="Open in pane" aria-label={'Open '+row.name+' in pane'} onClick={()=>open({kind:'files',root_id:row.rid,title:row.name},'tab')}><PanelRight size={13}/></button></div>:line}
     {folder&&open_&&info?.busy&&!info.entries.length&&<p className="empty-note" style={{paddingLeft:30+row.depth*14}}>Loading…</p>}
     {folder&&open_&&info?.error&&<p className="inline-error">{info.error}<button onClick={()=>void load(row.key)}>Retry</button></p>}
     {folder&&open_&&info?.next&&<button className="text-button" style={{marginLeft:30+row.depth*14}} onClick={()=>void load(row.key,info.next)}>Load more</button>}
    </React.Fragment>;})}
   {results&&!results.length&&<p className="empty-note">No matching filenames.</p>}{truncated&&<p className="empty-note">Showing a bounded result set. Refine the filename to find more.</p>}
   {!roots.length&&!error&&<p className="empty-note">No workspace folders yet.{tab?'':' Open a folder above.'}</p>}
  </div>
  {ask&&<div className="dialog-backdrop" onPointerDown={()=>ask.resolve('Cancel')}><div className="history-dialog file-move-dialog" role="alertdialog" aria-modal="true" aria-label={ask.message} onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Escape')ask.resolve('Cancel');}}><p>{ask.message}</p><div>{ask.buttons.map((b,i)=><button key={b} autoFocus={i===0} className={i===0?'primary':''} onClick={()=>ask.resolve(b)}>{b}</button>)}</div></div></div>}
  {toast&&<div className="toast file-move-toast" role="status"><span>{toast.text}</span>{toast.token&&<button className="text-button" onClick={()=>void undo()}>Undo</button>}<button className="icon-button" aria-label="Dismiss notification" title="Dismiss notification" onClick={()=>setToast(null)}><X size={14}/></button></div>}
 </>;
}

// A Files tab in a pane: the side menu's tree for one workspace, with a workspace chooser.
// The chosen workspace is kept in the tab's storage so the tab restores with it.
export function FilesTab({resource,onContext}:{resource:any,onContext:any}){
 const [root,setRoot]=useState<string>(resource.root_id||'');
 const choose=(root_id:string,title:string)=>{setRoot(root_id);localStorage.setItem('library.files.root',JSON.stringify({root_id,title}));void (window as any).libraryRpc('filesRoot',{root_id,title});};
 return <div className="files-tab-view"><FileTree onContext={onContext} tab={{root,storageKey:'workbench-file-tree',choose}}/></div>;
}
