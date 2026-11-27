'use strict';
const {randomBytes}=require('node:crypto');
const {artifactPreview}=require('./artifact-preview.cjs');

// The webview retains its isolated document origin while the extension checks the backing file.
async function liveDocument({vscode,viewer,name,scope,file,api,bytes,local,writableWorkspace,hostId}){
 const fs=require('node:fs/promises');
 const html=/\.html?$/i.test(name);
 const markdown=html?null:require('./media/markdown-it.min.js')({html:false});
 const stat=async()=>scope?(await api('/api/files/stat?'+new URLSearchParams(scope))).resource:await fs.stat(file);
 const key=info=>info.version||[info.ino,info.mtimeMs,info.size].join('/');
 const load=async()=>scope?await bytes('/api/files/content?'+new URLSearchParams(scope)):await fs.readFile(file);
 const preview=await artifactPreview({name,target:scope,source:()=>html?content:markdown.render(content.toString('utf8')),api,bytes});
 const nonce=randomBytes(24).toString('hex');
 let version,content,canRewind=false,closed=false,timer,revision=0,queue=Promise.resolve();
 function render(){
  const url=preview.url.replace(/"/g,'&quot;')+'?revision='+ ++revision;
  viewer.webview.html='<!doctype html><html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'nonce-'+nonce+'\'; frame-src '+preview.origin+'; style-src \'unsafe-inline\'"><style>body{margin:0;display:flex;height:100vh;flex-direction:column}nav{display:flex;gap:.5em;padding:4px}button{color:var(--vscode-button-secondaryForeground);background:var(--vscode-button-secondaryBackground);border:0;padding:4px 8px}iframe{border:0;flex:1}</style><nav><button id="renew">Renew</button>'+(canRewind?'<button id="rewind">Rewind to previous version</button>':'')+'</nav><iframe title="Document preview" sandbox="allow-scripts allow-same-origin" referrerpolicy="no-referrer" src="'+url+'"></iframe><script nonce="'+nonce+'">const host=acquireVsCodeApi();document.querySelector("nav").addEventListener("click",e=>{if(e.target.id)host.postMessage({action:e.target.id})});</script></html>';
 }
 async function refresh(force){
  if(closed)return;
  const info=await stat(),next=key(info);
  if(next===version&&!force)return;
  if(info.size>64*1024*1024)throw Error('This preview exceeds 64 MB.');
  const nextContent=await load();
  if(key(await stat())!==next)throw Error('The file changed during renewal. Try again.');
  if(closed)return;
  if(!scope)await local.observe(nextContent);
  version=next;content=nextContent;
  canRewind=scope?info.document_editable&&info.has_previous:nextContent.length<=5*1024*1024&&await local.hasPrevious();
  render();
 }
 function run(fn,report){
  const result=queue.then(fn);
  queue=result.catch(e=>{if(report&&!closed)vscode.window.showErrorMessage(e.message);});
  return result;
 }
 viewer.onDidDispose(()=>{closed=true;clearInterval(timer);preview.close();});
 await refresh(true);
 if(closed)return;
 viewer.webview.onDidReceiveMessage(m=>{
  if(m?.action==='renew')void run(()=>refresh(true),true).catch(()=>{});
  if(m?.action==='rewind')void run(async()=>{
   writableWorkspace();
   if(!canRewind)return;
   if(['mac','local'].includes(hostId)&&vscode.workspace.textDocuments.some(doc=>doc.isDirty&&doc.uri.scheme==='file'&&doc.uri.fsPath===file))throw Error('Save the pending source changes before rewinding.');
   if(scope)await api('/api/files/document-rewind',{...scope,base_version:version});
   else{
    const doc=await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    if(doc.isDirty||key(await stat())!==version)throw Error('The file changed. Renew before rewinding.');
    const old=await local.previous(),edit=new vscode.WorkspaceEdit();
    if(old===null)throw Error('No previous version is available.');
    const end=doc.positionAt(doc.getText().length);
    edit.replace(doc.uri,new vscode.Range(doc.positionAt(0),end),old);
    if(!await vscode.workspace.applyEdit(edit)||!await doc.save())throw Error('Could not restore the previous version.');
   }
   await refresh(true);
  },true).catch(()=>{});
 });
 let checking=false;
 timer=setInterval(()=>{
  if(closed||checking)return;
  checking=true;void run(()=>refresh(false),false).catch(()=>{}).finally(()=>{checking=false;});
 },3000);
 timer.unref();
}
function localVersions(storage,file){
 const fs=require('node:fs/promises'),path=require('node:path');
 const directory=path.join(storage,'file-versions');
 const record=path.join(directory,require('node:crypto').createHash('sha256').update(file).digest('hex')+'.json');
 const read=()=>fs.readFile(record,'utf8').then(JSON.parse,error=>{if(error.code==='ENOENT')return {};throw error;});
 return {
  async observe(bytes){
   if(bytes.length>5*1024*1024)return;
   const current=bytes.toString('base64'),last=await read();
   if(last.current===current)return;
   await fs.mkdir(directory,{recursive:true});
   const tmp=record+'.'+randomBytes(8).toString('hex');
   await fs.writeFile(tmp,JSON.stringify({current,previous:last.current}),{flag:'wx',mode:0o600});
   await fs.rename(tmp,record);
  },
  async previous(){const {previous}=await read();return previous===undefined?null:Buffer.from(previous,'base64').toString('utf8');},
  async hasPrevious(){return (await read()).previous!==undefined;}
 };
}
module.exports.liveDocument=liveDocument;
module.exports.localVersions=localVersions;
