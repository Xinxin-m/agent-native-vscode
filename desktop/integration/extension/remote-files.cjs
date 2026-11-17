'use strict';
function query(uri){
 if(uri.scheme!=='library-file'||!uri.authority||uri.query||uri.fragment)throw Error('Invalid remote file URI');
 const path=uri.path.replace(/^\//,'');if(path.includes('\\')||path.includes('\0')||path.split('/').some(p=>p==='..'||p==='.'))throw Error('Remote path must remain inside its workspace');
 return new URLSearchParams({root_id:uri.authority,path}).toString();
}
function createProvider(vscode,api,bytes){
 const changed=new vscode.EventEmitter(),known=new Map();
 const directory={type:vscode.FileType.Directory,ctime:0,mtime:0,size:0,permissions:vscode.FilePermission.Readonly};
 const provider={onDidChangeFile:changed.event,watch(){return new vscode.Disposable(()=>{});},
 async stat(uri){const q=query(uri);if(uri.path==='/'){const roots=(await api('/api/files/roots')).roots;if(!roots.some(r=>r.id===uri.authority))throw vscode.FileSystemError.FileNotFound(uri);return directory;}
 if(known.has(uri.toString()))return known.get(uri.toString());
 try{const r=(await api('/api/files/stat?'+q)).resource;return {type:vscode.FileType.File,ctime:0,mtime:0,size:r.size,permissions:vscode.FilePermission.Readonly};}catch(e){if(e.status!==415)throw e;await api('/api/files/list?'+q);return directory;}},
 async readDirectory(uri){const q=query(uri);let cursor='',result=[];do{const page=await api('/api/files/list?'+q+(cursor?'&cursor='+encodeURIComponent(cursor):''));for(const e of page.entries||[]){const type=e.type==='directory'?vscode.FileType.Directory:e.type==='file'?vscode.FileType.File:vscode.FileType.Unknown;result.push([e.name,type]);const child=vscode.Uri.joinPath(uri,e.name);known.set(child.toString(),{type,ctime:0,mtime:0,size:e.size||0,permissions:vscode.FilePermission.Readonly});}cursor=page.next_cursor||'';}while(cursor);return result;},
 readFile(uri){return bytes('/api/files/content?'+query(uri));},
 writeFile(){throw vscode.FileSystemError.NoPermissions('Cluster files are read-only through the current connector.');},
 createDirectory(){throw vscode.FileSystemError.NoPermissions('Cluster files are read-only.');},delete(){throw vscode.FileSystemError.NoPermissions('Cluster files are read-only.');},rename(){throw vscode.FileSystemError.NoPermissions('Cluster files are read-only.');},
 refresh(){known.clear();changed.fire([]);},dispose(){changed.dispose();}};return provider;
}
module.exports={query,createProvider};
