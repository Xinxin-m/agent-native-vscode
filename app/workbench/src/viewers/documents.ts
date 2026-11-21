import {useSyncExternalStore,useCallback} from 'react';
import {createDraftRepository,draftEventIdentity,type FileDraft,type DraftContent} from './drafts';
import type {Resource} from '../state/types';
import {api,query,ApiError} from '../api/client';
import {contentUrl,coveringRoot,decodeText,fileIdentity,viewerKind,type FileRecord} from './helpers';
export interface DocumentState {file?:FileRecord;text:string;baseText:string;baseVersion?:string;dirty:boolean;loading:boolean;saving:boolean;error?:string;conflict?:{text:string;version:string};persistenceError?:string;refreshError?:string;recoveries?:FileDraft[];unassignedRecoveries?:FileDraft[];recoveryError?:string}
const records=new Map<string,DocumentState>(),listeners=new Map<string,Set<()=>void>>();
const EMPTY:DocumentState={text:'',baseText:'',dirty:false,loading:true,saving:false};
const releasedDocuments=new Set<string>(),releaseQueued=new Set<string>();
function releaseDocumentId(id:string){
 releasedDocuments.add(id);if(releaseQueued.has(id))return;releaseQueued.add(id);
 queueMicrotask(()=>{releaseQueued.delete(id);const state=records.get(id);if(listeners.get(id)?.size||watched.get(id)?.views.size||pendingLoads.has(id)||refreshing.has(id)||state?.loading||state?.saving||state?.dirty||state?.conflict||state?.persistenceError||state?.recoveryError)return;records.delete(id);listeners.delete(id);releasedDocuments.delete(id);});
}
// Drop clean, closed text buffers. Dirty/recovery buffers and other views of the same file stay resident.
export function releaseDocument(resource:Resource){releaseDocumentId(documentKey(resource));}
if(typeof window!=='undefined'&&new URLSearchParams(location.search).has('document-profile'))(window as any).__documentStats=()=>[...records].map(([id,d])=>({id,text:d.text.length,base:d.baseText.length,dirty:d.dirty,subscribers:listeners.get(id)?.size||0,views:watched.get(id)?.views.size||0,released:releasedDocuments.has(id)}));

const fallbackWriter=globalThis.crypto?.randomUUID?.()||'writer-'+Date.now();
function drafts(){let writer=fallbackWriter;try{writer=new URLSearchParams(location.search).get('window')||writer;}catch{}return createDraftRepository(localStorage,writer);}
function availableRecoveries(id:string){const repo=drafts();return repo.list(id).filter(d=>d.writer!==repo.writer);}
export function documentKey(r:Resource){return r.path?fileIdentity(r.host_id||'mac',r.path):r.file_id?JSON.stringify([r.host_id||'mac',{file_id:r.file_id}]):fileIdentity(r.host_id||'mac','');}
export function unassignedDrafts(r:Resource,repo:{list:(id:string)=>FileDraft[]}){return !r.path&&r.file_id?repo.list(fileIdentity(r.host_id||'mac','')):[];}
export function snapshot(id:string){return records.get(id)||EMPTY;}
function update(id:string,patch:Partial<DocumentState>){records.set(id,{...snapshot(id),...patch});listeners.get(id)?.forEach(fn=>fn());if(releasedDocuments.has(id))releaseDocumentId(id);}
function persist(id:string){const d=snapshot(id);try{const repo=drafts();if(d.dirty)repo.save(id,{text:d.text,baseText:d.baseText,version:d.baseVersion});else repo.clearOwn(id);update(id,{persistenceError:undefined,recoveries:availableRecoveries(id)});}catch{update(id,{persistenceError:'Browser storage is full. Keep this window open and save or download your draft.'});}}
export function restoreDraft(resource:Resource,key:string){const id=documentKey(resource),d=snapshot(id);try{const repo=drafts(),saved=repo.list(id).find(r=>r.storageKey===key);if(!saved)return;if(d.dirty&&d.text!==saved.text)repo.archive(id,{text:d.text,baseText:d.baseText,version:d.baseVersion});const disk=d.conflict?.text??d.baseText,diskVersion=d.file?.version||d.baseVersion;update(id,{text:saved.text,baseText:saved.baseText,baseVersion:saved.version,dirty:saved.text!==disk,conflict:saved.version!==diskVersion?{text:disk,version:diskVersion||''}:undefined,recoveryError:undefined});persist(id);}catch{update(id,{recoveryError:'The current draft could not be backed up. Nothing was replaced. Download it before restoring another draft.'});}}
export function discardDraft(resource:Resource,key:string){const id=documentKey(resource);try{drafts().discard(id,key);update(id,{recoveries:availableRecoveries(id),recoveryError:undefined});}catch{update(id,{recoveryError:'The saved draft could not be removed.'});}}
export function useFileDocument(resource:Resource){const id=documentKey(resource);const subscribe=useCallback((fn:()=>void)=>{releasedDocuments.delete(id);if(!listeners.has(id))listeners.set(id,new Set());listeners.get(id)!.add(fn);return()=>{listeners.get(id)?.delete(fn);releaseDocumentId(id);};},[id]);return useSyncExternalStore(subscribe,()=>snapshot(id));}
// A moved file keeps its buffer: unsaved text is saved under the new path, then the view reloads there.
export function moveDocument(from:Resource,to:Resource){const old=documentKey(from),id=documentKey(to),d=records.get(old);if(!d)return;if(d.dirty){const repo=drafts();repo.save(id,{text:d.text,baseText:d.baseText,version:d.baseVersion});repo.clearOwn(old);}records.delete(old);records.delete(id);}
export function changeText(resource:Resource,text:string){const id=documentKey(resource),d=snapshot(id);update(id,{text,dirty:text!==d.baseText});persist(id);}
export async function resolveFile(resource:Resource):Promise<FileRecord>{
 const host=resource.host_id||'mac',chatKey=resource.key||resource.meta?.chat_key;let root=resource.root_id,relative:string|undefined;
 if(resource.file_id&&!root){
  try{const result=await api(query('/api/files/stat',{file_id:resource.file_id}));return result.resource;}
  catch(error){if(!(error instanceof ApiError)||error.status!==404||!chatKey||!resource.path)throw error;const result=await api('/api/files/reference',{chat_key:chatKey,path:resource.path});if(!result.resource?.file_id||(result.resource.host_id||'mac')!==host)throw new Error('The refreshed file reference belongs to a different host.');return result.resource;}
 }
 const data=await api(query('/api/files/roots',{host_id:host,chat_key:chatKey}));
 const roots=data.roots||[];const match=root?roots.find((r:any)=>(r.root_id||r.id)===root):coveringRoot(roots,host,resource.path||'');
 if(match){root=match.root_id||match.id;relative=(resource.path||'').slice(match.path.replace(/\/$/,'').length).replace(/^\//,'')||'.';}
 if(!root){if(chatKey){const result=await api('/api/files/reference',{chat_key:chatKey,path:resource.path});return result.resource;}throw new Error('This file is outside the available folders. Add its folder in Files to view it.');}
 if(relative===undefined)throw new Error('The folder is unavailable. Reopen this file from Files.');
 const result=await api(query('/api/files/stat',{root_id:root,path:relative}));
 return result.resource;
}
async function textFor(file:FileRecord){
 if(file.size>8*1024*1024)throw new Error('This text file exceeds the 8 MB editor limit. Download it or open it in your editor.');
 const response=await fetch(contentUrl(file));if(!response.ok)throw new Error(`Cannot read file (HTTP ${response.status}).`);
 const length=Number(response.headers.get('content-length')||0);if(length>8*1024*1024)throw new Error('This text file exceeds the 8 MB editor limit.');
 const reader=response.body?.getReader();if(!reader)return decodeText(await response.arrayBuffer());
 const chunks:Uint8Array[]=[];let total=0;while(true){const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>8*1024*1024){await reader.cancel();throw new Error('This text file exceeds the 8 MB editor limit.');}chunks.push(value);}
 const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return decodeText(bytes.buffer);
}
const pendingLoads=new Map<string,Promise<void>>();
export function loadDocument(resource:Resource,force=false):Promise<void>{const id=documentKey(resource),pending=pendingLoads.get(id);if(pending)return pending;if(records.has(id)&&!force)return Promise.resolve();const task=hydrateDocument(resource,force).finally(()=>{if(pendingLoads.get(id)===task)pendingLoads.delete(id);if(releasedDocuments.has(id))releaseDocumentId(id);});pendingLoads.set(id,task);return task;}
async function hydrateDocument(resource:Resource,force=false){
 const id=documentKey(resource);
 const previous=snapshot(id);if(force&&previous.dirty)throw new Error('Save or compare your unsaved changes before refreshing.');
 update(id,{loading:true,error:undefined});
 try{const file=await resolveFile(resource),kind=viewerKind(file.name,file.mime);update(id,{file});let text='';if(['source','markdown','html'].includes(kind))text=await textFor(file);
  let saved:FileDraft|undefined,recoveries:FileDraft[]=[],unassignedRecoveries:FileDraft[]=[];try{const repo=drafts(),all=repo.list(id);unassignedRecoveries=unassignedDrafts(resource,repo);if(!resource.path&&resource.file_id&&file.path)unassignedRecoveries=[...unassignedRecoveries,...repo.list(fileIdentity(file.host_id,file.path))];saved=all.find(d=>d.writer===repo.writer)||(all.length===1&&all[0].legacy?all[0]:undefined);recoveries=all.filter(d=>d.writer!==repo.writer);}catch{}
  const restored=Boolean(saved);
  update(id,{file,text:restored?saved.text:text,baseText:restored?saved.baseText:text,baseVersion:restored?saved.version:file.version,dirty:restored&&saved.text!==text,loading:false,error:undefined,recoveries,unassignedRecoveries,conflict:restored&&saved.version!==file.version?{text,version:file.version}:undefined});
  if(restored&&saved.text!==text)persist(id);
 }catch(e){update(id,{loading:false,error:(e as Error).message});}
}
export async function saveDocument(resource:Resource){const id=documentKey(resource),d=snapshot(id);if(!d.file||!d.file.editable||d.saving||d.conflict)return;
 update(id,{saving:true,error:undefined});const sent=d.text;
 try{const result=await api('/api/files/save',{root_id:d.file.root_id,path:d.file.relative_path||'.',base_version:d.baseVersion,text:sent});const file=result.resource||result;const current=snapshot(id);update(id,{file:{...d.file,...file},baseText:sent,baseVersion:file.version,dirty:current.text!==sent,saving:false,conflict:undefined});persist(id);
 }catch(e){if(e instanceof ApiError&&e.status===409){try{const file=await resolveFile(resource),text=await textFor(file);update(id,{saving:false,conflict:{text,version:file.version},error:undefined});}catch(f){update(id,{saving:false,error:(f as Error).message});}}else update(id,{saving:false,error:(e as Error).message});}
}
export function reloadConflict(resource:Resource){const id=documentKey(resource),d=snapshot(id);if(!d.conflict)return;update(id,{text:d.conflict.text,baseText:d.conflict.text,baseVersion:d.conflict.version,dirty:false,conflict:undefined});persist(id);}
export async function saveCopy(resource:Resource,destination:string){const d=snapshot(documentKey(resource));if(!d.file)throw new Error('File is unavailable.');const result=await api('/api/files/copy',{root_id:d.file.root_id,path:d.file.relative_path||'.',destination,text:d.text,base_version:d.baseVersion});return result.resource as FileRecord;}
if(typeof window!=='undefined')window.addEventListener('beforeunload',e=>{if([...records.values()].some(d=>d.dirty)){e.preventDefault();e.returnValue='';}});
if(typeof window!=='undefined')window.addEventListener('storage',event=>{if(!event.key)return;const id=draftEventIdentity(event.key);if(!id||!records.has(id))return;try{update(id,{recoveries:availableRecoveries(id)});}catch{update(id,{recoveryError:'Saved drafts could not be refreshed. Your open buffer is preserved.'});}});
export async function loadGitDiff(resource:Resource){const file=snapshot(documentKey(resource)).file||await resolveFile(resource);if(!file.root_id)throw new Error('Git comparison needs a registered workspace folder.');return api<{original:string;modified:string;version:string;label:string}>(query('/api/files/diff',{root_id:file.root_id,path:file.relative_path||'.'}));}

export async function acceptTransferredDraft(resource:Resource,incoming:DraftContent):Promise<void>{
 if(!incoming||typeof incoming.text!=='string'||typeof incoming.baseText!=='string'||typeof incoming.version!=='string')throw new Error('The transferred file draft is incomplete. The source tab remains open.');
 const source={text:incoming.text,baseText:incoming.baseText,version:incoming.version};
 await loadDocument(resource);const id=documentKey(resource),loaded=snapshot(id);
 if(loaded.loading||loaded.error||!loaded.file)throw new Error(loaded.error||'The destination file is not ready. The source tab remains open.');
 // Read disk again: an existing destination view can predate the transfer.
 const file=await resolveFile(resource),diskText=await textFor(file),current=snapshot(id);
 if(current.saving)throw new Error('The destination is saving this file. Try again after it finishes.');
 try{const repo=drafts();if(current.dirty&&current.text!==source.text)repo.archive(id,{text:current.text,baseText:current.baseText,version:current.baseVersion});
  // Persist before publishing or acknowledging. A quota error leaves the prior view intact.
  repo.save(id,source);const saved=repo.list(id),own=saved.find(d=>d.writer===repo.writer);
  if(!own||own.text!==source.text||own.baseText!==source.baseText||own.version!==source.version)throw new Error('Draft storage verification failed.');
  update(id,{file,text:source.text,baseText:source.baseText,baseVersion:source.version,dirty:source.text!==diskText,conflict:source.version!==file.version?{text:diskText,version:file.version}:undefined,persistenceError:undefined,recoveryError:undefined,recoveries:saved.filter(d=>d.writer!==repo.writer)});
 }catch(error){update(id,{persistenceError:'The transferred draft could not be stored safely. The source tab must stay open.'});throw new Error('The transferred draft could not be stored safely. The source tab remains open.',{cause:error});}
}

const refreshing=new Set<string>();
const watched=new Map<string,{resource:Resource;views:Set<()=>boolean>}>();
let watchTimer:ReturnType<typeof setInterval>|undefined;
const liveText=(file?:FileRecord)=>file&&file.host_id==='mac'&&file.size<=8*1024*1024&&['source','markdown','html'].includes(viewerKind(file.name,file.mime));
async function statKnownFile(file:FileRecord):Promise<FileRecord>{const result=await api(query('/api/files/stat',file.file_id&&!file.root_id?{file_id:file.file_id}:{root_id:file.root_id,path:file.relative_path||'.'}));return result.resource;}
export async function refreshLocalDocument(resource:Resource):Promise<void>{
 const id=documentKey(resource),before=snapshot(id);if(resource.host_id&&resource.host_id!=='mac'||!liveText(before.file)||before.loading||before.saving||pendingLoads.has(id)||refreshing.has(id))return;
 refreshing.add(id);
 try{const file=await statKnownFile(before.file);if(file.version===before.file.version){if(snapshot(id).refreshError)update(id,{refreshError:undefined});return;}
  if(!liveText(file)){update(id,{refreshError:'The file changed beyond live text viewing limits. Use Refresh or open it in your editor.'});return;}
  const text=await textFor(file),verified=await statKnownFile(file);if(verified.version!==file.version)return;
  const current=snapshot(id);if(current.loading||current.saving||pendingLoads.has(id)||current.file?.version!==before.file.version||current.baseVersion!==before.baseVersion)return;
  if(current.dirty||current.text!==before.text||current.baseText!==before.baseText){update(id,{file,conflict:{text,version:file.version},refreshError:undefined});}
  else{update(id,{file,text,baseText:text,baseVersion:file.version,dirty:false,conflict:undefined,refreshError:undefined});persist(id);}
 }catch(error){update(id,{refreshError:'Could not refresh file: '+(error as Error).message});}finally{refreshing.delete(id);if(releasedDocuments.has(id))releaseDocumentId(id);}
}
export async function pollWatchedDocuments():Promise<void>{if(typeof document!=='undefined'&&document.visibilityState==='hidden')return;await Promise.all([...watched.values()].filter(entry=>[...entry.views].some(visible=>{try{return visible();}catch{return false;}})).map(entry=>refreshLocalDocument(entry.resource)));}
export function watchDocument(resource:Resource,visible:()=>boolean):()=>void{
 if(resource.kind!=='file'||resource.host_id&&resource.host_id!=='mac')return()=>{};
 const id=documentKey(resource);releasedDocuments.delete(id);let entry=watched.get(id);if(!entry){entry={resource,views:new Set()};watched.set(id,entry);}entry.views.add(visible);
 if(!watchTimer)watchTimer=setInterval(()=>{void pollWatchedDocuments();},8000);
 let released=false;return()=>{if(released)return;released=true;entry.views.delete(visible);if(!entry.views.size){watched.delete(id);releaseDocumentId(id);}if(!watched.size&&watchTimer){clearInterval(watchTimer);watchTimer=undefined;}};
}
