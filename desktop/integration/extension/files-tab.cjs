"use strict";
// The Files tab's file API, served from the open workspace folders with the same routes and
// answers as the Library server, so the shared tree component runs unchanged in the webview.
// Moves go through a WorkspaceEdit, so open editors follow the file and keep unsaved edits.
const fail=(code,message,status=400,extra={})=>Object.assign(new Error(message),{status,data:{code,error:message,...extra}});

// Keep both uses VS Code's naming: 'name copy.ext', then 'name copy 2.ext'.
function freeName(taken,name,isDirectory){
 let stem=name,ext='';const dot=name.lastIndexOf('.');
 if(!isDirectory&&dot>0){stem=name.slice(0,dot);ext=name.slice(dot);}
 while(taken.has(stem+ext)){const copy=/^(.+ copy)( \d+)?$/.exec(stem);stem=copy?`${copy[1]} ${Number(copy[2]||1)+1}`:stem+' copy';}
 return stem+ext;
}

function create(vscode,{api,backups}){
 const moves=new Map(),fs=vscode.workspace.fs;
 const parts=path=>{const list=String(path||'').split('/').filter(Boolean);if(list.some(p=>p==='..'||p==='.')||String(path||'').startsWith('/'))throw fail('outside_root','The path is outside the workspace',403);return list;};
 const exists=async uri=>{try{await fs.stat(uri);return true;}catch{return false;}};
 async function roots(){
  const folders=vscode.workspace.workspaceFolders||[],remote=new Map();
  if(folders.some(f=>f.uri.scheme!=='file'))try{for(const r of (await api('/api/files/roots')).roots||[])remote.set(r.id||r.root_id,r);}catch{}
  return folders.map(f=>f.uri.scheme==='file'?{id:f.uri.toString(),host_id:'mac',path:f.uri.fsPath,label:f.name,access:'write',uri:f.uri}
   :{id:f.uri.authority,host_id:remote.get(f.uri.authority)?.host_id||f.name.split(' · ').pop(),path:remote.get(f.uri.authority)?.path||'/',label:f.name,access:'read',uri:f.uri});
 }
 const find=(all,id)=>all.find(r=>r.id===id)||(()=>{throw fail('not_found','This workspace is no longer open',404);})();
 const at=(root,path)=>{const list=parts(path);return list.length?vscode.Uri.joinPath(root.uri,...list):root.uri;};
 async function list(id,path){
  const root=find(await roots(),id),excluded=vscode.workspace.getConfiguration('files').get('exclude')||{};
  const entries=(await fs.readDirectory(at(root,path))).filter(([name])=>excluded['**/'+name]!==true)
   .map(([name,type])=>({name,path:[...parts(path),name].join('/'),type:type&vscode.FileType.Directory?'directory':'file'}))
   .sort((a,b)=>(a.type==='directory'?0:1)-(b.type==='directory'?0:1)||a.name.localeCompare(b.name));
  return {entries,next_cursor:''};
 }
 async function rename(from,to,overwrite=false){const edit=new vscode.WorkspaceEdit();edit.renameFile(from,to,{overwrite});if(!await vscode.workspace.applyEdit(edit))throw fail('move_failed','The move was not applied',500);}
 async function move({items,dest_root_id,dest_dir='',copy=false,conflict='ask'}={}){
  if(!['ask','replace','keep_both'].includes(conflict)||!Array.isArray(items)||!items.length||items.length>500)throw fail('invalid_request','Invalid move request');
  const all=await roots(),dest=find(all,dest_root_id),sources=items.map(i=>({root:find(all,i.root_id),path:parts(i.path)}));
  const hosts=new Set([dest.host_id,...sources.map(s=>s.root.host_id)]),other=[...hosts].find(h=>h!=='mac');
  if(other)throw hosts.has('mac')?fail('cross_host',`Moving between this Mac and ${other} is not supported yet`,501):fail('read_only','Remote files are read-only',403);
  const folder=at(dest,dest_dir),folderKey=folder.toString();
  if(!await exists(folder))throw fail('not_found','Destination folder no longer exists',404);
  const plan=[],names=new Set();
  for(const {root,path} of sources){
   if(!path.length)throw fail('invalid_path','A workspace root cannot be moved');
   const src=at(root,path.join('/')),srcKey=src.toString(),name=path.at(-1);
   if(!await exists(src))throw fail('not_found',name+' no longer exists',404);
   if(folderKey===srcKey||folderKey.startsWith(srcKey+'/'))throw fail('into_itself',`'${name}' cannot be moved into itself`,409);
   const same=vscode.Uri.joinPath(src,'..').toString()===folderKey;
   if(same&&!copy)continue;
   if(names.has(name))throw fail('duplicate_names','Two selected items have the same name: '+name,409);
   names.add(name);plan.push({root,path,src,name,same});
  }
  const taken=new Set((await fs.readDirectory(folder)).map(([n])=>n));
  const clashes=plan.filter(p=>!p.same&&taken.has(p.name)).map(p=>p.name);
  if(clashes.length&&conflict==='ask')throw fail('destination_exists','An item with the same name already exists in the destination',409,{names:clashes});
  const done=[];
  try{
   for(const p of plan){
    let target=vscode.Uri.joinPath(folder,p.name),backup=null;
    if(taken.has(p.name)){
     if(conflict==='replace'&&!p.same){backup=vscode.Uri.joinPath(backups,Date.now()+'-'+Math.random().toString(16).slice(2,8),p.name);await fs.copy(target,backup,{overwrite:false});}
     else target=vscode.Uri.joinPath(folder,freeName(taken,p.name,(await fs.stat(p.src)).type&vscode.FileType.Directory));
    }
    if(copy)await fs.copy(p.src,target,{overwrite:Boolean(backup)});else await rename(p.src,target,Boolean(backup));
    taken.add(target.path.split('/').pop());
    done.push({src:p.src,dst:target,backup,copy:Boolean(copy),root_id:p.root.id,path:p.path.join('/'),dest_root_id:dest.id,dest_path:[...parts(dest_dir),target.path.split('/').pop()].join('/')});
   }
  }catch(error){await reverse(done).catch(()=>{});throw error;}
  const token=done.length?Math.random().toString(16).slice(2)+Date.now().toString(16):null;
  if(token){moves.set(token,done);while(moves.size>20)moves.delete(moves.keys().next().value);}
  return {ok:true,token,moved:done.map(({root_id,path,dest_root_id,dest_path})=>({root_id,path,dest_root_id,dest_path}))};
 }
 async function reverse(done){
  for(const d of [...done].reverse()){
   if(d.copy)await fs.delete(d.dst,{recursive:true,useTrash:true});else await rename(d.dst,d.src);
   if(d.backup)await fs.copy(d.backup,d.dst,{overwrite:false});
  }
 }
 async function undo({token}={}){
  const done=moves.get(String(token));if(!done)throw fail('not_found','Nothing to undo',404);
  for(const d of done)if(!await exists(d.dst)||(!d.copy&&await exists(d.src)))throw fail('undo_conflict','The moved items changed since the move, so Undo was not applied',409);
  moves.delete(String(token));await reverse(done);return {ok:true,restored:done.length};
 }
 async function request(path,body){
  const [route,search]=String(path).split('?'),query=Object.fromEntries(new URLSearchParams(search||''));
  if(route==='/api/files/roots'&&body===undefined)return {ok:true,roots:(await roots()).map(({uri,...root})=>root)};
  if(route==='/api/files/list'&&body===undefined)return {ok:true,...await list(query.root_id,query.path||'')};
  if(route==='/api/files/move'&&body!==undefined)return move(body);
  if(route==='/api/files/move/undo'&&body!==undefined)return undo(body);
  throw fail('unsupported','The Files tab does not support this request',404);
 }
 // The Files tab's id for a workspace folder: its URI on this Mac, the Library root id for a remote one.
 const rootId=uri=>uri.scheme==='file'?uri.toString():uri.authority;
 return {request,rootId};
}
module.exports={create,freeName};
