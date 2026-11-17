'use strict';
// A URI identifies one transcript-evidenced file. It never registers a directory.
function createProvider(vscode,api,bytes){
 const changed=new vscode.EventEmitter();
 function identity(uri){
  if(uri.scheme!=='library-reference'||!uri.authority||!uri.path?.startsWith('/')||uri.fragment||/[\\\\\0]/.test(uri.path)||uri.path.split('/').includes('..'))throw Error('Invalid exact file reference');
  const q=new URLSearchParams(uri.query||'');
  if([...q.keys()].some(key=>!['file_id','chat_key','canonical_path'].includes(key))||q.getAll('file_id').length>1||q.getAll('chat_key').length>1||q.getAll('canonical_path').length>1)throw Error('Invalid exact file reference');
  return {host:uri.authority,path:uri.path,file_id:q.get('file_id'),chat_key:q.get('chat_key'),canonical_path:q.get('canonical_path')};
 }
 function match(resource,ref){
  if(!resource?.file_id||resource.host_id!==ref.host||(resource.path!==ref.path&&resource.evidence_path!==ref.path)||(ref.canonical_path&&resource.path!==ref.canonical_path))throw Error('File reference does not match its host, evidence path and canonical target');
  return resource;
 }
 async function metadata(ref){
  if(ref.file_id){
   try{return match((await api('/api/files/stat?'+new URLSearchParams({file_id:ref.file_id}))).resource,ref);}
   catch(error){if(!ref.chat_key)throw error;}
  }
  if(!ref.chat_key)throw Error('Reopen this exact file from its conversation to restore its reference');
  return match((await api('/api/files/reference',{chat_key:ref.chat_key,path:ref.path})).resource,ref);
 }
 const denied=()=>{throw vscode.FileSystemError.NoPermissions('Exact file references are read-only and grant no directory access.');};
 return {onDidChangeFile:changed.event,watch(){return new vscode.Disposable(()=>{});},
  async resolve(resource){
   const ref={host:resource.host_id,path:resource.path,file_id:resource.file_id,chat_key:resource.meta?.chat_key||resource.chat_key};
   const probe=vscode.Uri.from({scheme:'library-reference',authority:ref.host,path:ref.path,query:new URLSearchParams({...ref.file_id?{file_id:ref.file_id}:{},...ref.chat_key?{chat_key:ref.chat_key}:{}}).toString()});
   identity(probe);const found=await metadata(ref);
   return vscode.Uri.from({scheme:'library-reference',authority:found.host_id,path:ref.path,query:new URLSearchParams({file_id:found.file_id,canonical_path:found.path,...ref.chat_key?{chat_key:ref.chat_key}:{}}).toString()});
  },
  async stat(uri){const value=await metadata(identity(uri));return {type:vscode.FileType.File,ctime:0,mtime:0,size:value.size,permissions:vscode.FilePermission.Readonly};},
  async readFile(uri){
   const ref=identity(uri),value=await metadata(ref);
   try{return await bytes('/api/files/content?'+new URLSearchParams({file_id:value.file_id}));}
   catch(error){if(!ref.chat_key)throw error;const fresh=await metadata({...ref,file_id:null});return bytes('/api/files/content?'+new URLSearchParams({file_id:fresh.file_id}));}
  },
  readDirectory:denied,writeFile:denied,createDirectory:denied,delete:denied,rename:denied,
  dispose(){changed.dispose();}
 };
}
module.exports={createProvider};
