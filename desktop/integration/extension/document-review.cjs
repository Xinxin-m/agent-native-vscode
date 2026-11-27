'use strict';
const path = require('node:path');
const {randomBytes, randomUUID} = require('node:crypto');
const {createAssetLoader} = require('./document-assets.cjs');
const {markdownImageSources} = require('./html-source.cjs');

async function targetFor(uri, api) {
 if (uri.scheme === 'library-reference') {
  const file_id = new URLSearchParams(uri.query).get('file_id');
  return file_id ? {file_id} : null;
 }
 if (uri.scheme === 'library-file') return {root_id:uri.authority,path:uri.path.slice(1)};
 if (uri.scheme !== 'file') return null;
 const {roots} = await api('/api/files/roots');
 const file = uri.fsPath;
 const root = roots.filter(r => (r.host_id || 'mac') === 'mac' && file.startsWith(r.path.replace(/\/$/, '') + '/'))
   .sort((a,b) => b.path.length - a.path.length)[0];
 return root ? {root_id:root.id || root.root_id, path:path.relative(root.path,file)} : null;
}

// Local files opened directly from Finder may be outside every registered Library root.
function localReviewStore(folder, uri) {
 const location=path.join(folder,'document-reviews.sqlite');
 const key=uri.toString();
 function run(fn) {
  require('node:fs').mkdirSync(folder,{recursive:true});
  const {DatabaseSync}=require('node:sqlite');
  const db=new DatabaseSync(location);
  try {
   db.exec('PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS checkpoints(doc TEXT PRIMARY KEY, previous TEXT, at INTEGER); CREATE TABLE IF NOT EXISTS comments(doc TEXT, id TEXT, text TEXT, quote TEXT, created INTEGER, PRIMARY KEY(doc,id)); CREATE TABLE IF NOT EXISTS drafts(doc TEXT, writer TEXT, text TEXT, PRIMARY KEY(doc,writer))');
   return fn(db);
  } finally {db.close();}
 }
 return {
  read(){return run(db=>({previous:db.prepare('SELECT previous FROM checkpoints WHERE doc=?').get(key)?.previous??null,comments:db.prepare('SELECT id,text,quote,created FROM comments WHERE doc=? ORDER BY created,rowid').all(key)}));},
  comment(c){run(db=>db.prepare('INSERT OR IGNORE INTO comments VALUES(?,?,?,?,unixepoch())').run(key,c.id,c.text,c.quote));},
  checkpoint(text){run(db=>db.prepare('INSERT INTO checkpoints VALUES(?,?,unixepoch()) ON CONFLICT(doc) DO UPDATE SET previous=excluded.previous,at=excluded.at WHERE excluded.at-checkpoints.at>=3600').run(key,text));},
  saveDraft(writer,text){run(db=>db.prepare('INSERT OR REPLACE INTO drafts VALUES(?,?,?)').run(key,writer,text));},
  clearDraft(writer,text){run(db=>db.prepare('DELETE FROM drafts WHERE doc=? AND writer=? AND text=?').run(key,writer,text));},
  drafts(){return run(db=>db.prepare('SELECT writer,text FROM drafts WHERE doc=? ORDER BY rowid').all(key));}
 };
}
const activeFlushes=new Set();
async function flushEditors(){await Promise.allSettled([...activeFlushes].map(flush=>flush()));}

async function resolveReviewEditor(vscode,context,document,panel,services) {
 const uri=document.uri;
 let target, metadata;
 try {
  target=await targetFor(uri,services.api);
  if (target) metadata=(await services.api('/api/files/stat?'+new URLSearchParams(target))).resource;
 } catch (e) {if (uri.scheme !== 'file') throw e;}
 const shared=typeof metadata?.document_editable === 'boolean';
 if (!shared && !context.globalStorageUri) return false;
 const local=localReviewStore(context.globalStorageUri.fsPath,uri),writer=randomUUID();
 const html=/\.html?$/i.test(uri.path);
 const file=metadata?.path ? metadata : {name:path.basename(uri.path),path:uri.fsPath,host_id:'mac'};
 const loader=await createAssetLoader({uri,...services});
 const assets=Object.create(null);
 const readDisk=async()=>new TextDecoder('utf-8',{fatal:true}).decode(
  await services.bytes('/api/files/content?'+new URLSearchParams(target))
 );
 let version=shared?metadata.version:document.version;
 let text=document.getText();
 if (shared) {
  // Re-read the version after bytes so a change during open cannot be overwritten.
  text=await readDisk();
  const check=(await services.api('/api/files/stat?'+new URLSearchParams(target))).resource;
  if (check.version !== version) throw Error('The document changed while opening. Reopen the viewer.');
  if(document.isDirty) throw Error('Save the source editor’s pending changes before opening rendered editing.');
 }
 if (!html) for (const src of markdownImageSources(text)) {
  const value=await loader.image(src);
  if (value) assets[src]=value;
 }
 const readOnly=shared?!metadata.document_editable:uri.scheme!=='file'||vscode.workspace.fs.isWritableFileSystem(uri.scheme)===false;
 const nonce=randomBytes(24).toString('base64');
 const media=vscode.Uri.joinPath(context.extensionUri,'media');
 const asset=name=>panel.webview.asWebviewUri(vscode.Uri.joinPath(media,name));
 panel.webview.options={enableScripts:true,localResourceRoots:[media]};
 const preview=html?await require('./artifact-preview.cjs').artifactPreview({name:path.basename(file.path||uri.path),target,source:()=>text,...services}):null;
 if(preview)panel.onDidDispose(()=>preview.close());
 const boot=JSON.stringify({previewUrl:preview?.url,format:html?'html':'markdown',file,text,version,assets,nonce,readOnly,recoveries:local.drafts()}).replace(/</g,'\\u003c');
 const src=panel.webview.cspSource;
 panel.webview.html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'nonce-'+nonce+'\' '+src+'; style-src '+src+' \'unsafe-inline\' data:; img-src '+src+' data:; font-src '+src+' data:; frame-src \'self\' about: '+(preview?.origin||'')+'; connect-src \'none\';"><link rel="stylesheet" href="'+asset('review.css')+'"></head><body><main id="root"></main><script nonce="'+nonce+'">window.__DOCUMENT_REVIEW__='+boot+';</script><script nonce="'+nonce+'" type="module" src="'+asset('review.js')+'"></script></body></html>';
 let queue=Promise.resolve(),applying=false,disposed=false;
 const review=()=>shared?services.api('/api/files/document-review?'+new URLSearchParams(target)):local.read();
 async function handle(m) {
  switch(m.action) {
   case 'review':return review();
   case 'preview-network':preview?.network(m.enabled===true);return true;
   case 'asset':{
    if(typeof m.value!=='string'||m.value.length>2*1024*1024) throw Error('Invalid document asset');
    if(m.kind==='image')return loader.image(m.value);
    if(m.kind==='style')return loader.stylesheet(m.value);
    if(m.kind==='css')return loader.css(m.value);
    throw Error('Unsupported asset type');
   }
   case 'comment':{
    if(!/^[a-f\d-]{36}$/.test(m.idempotency)||typeof m.text!=='string'||!m.text.trim()||m.text.length>12000||typeof m.quote!=='string'||m.quote.length>2000)throw Error('Invalid document comment');
    const comment={id:m.idempotency,text:m.text.trim(),quote:m.quote};
    if(shared)return services.api('/api/files/document-review',{...target,...comment});
    local.comment(comment);return local.read();
   }
   case 'save':{
    if(readOnly||typeof m.text!=='string'||Buffer.byteLength(m.text)>5*1024*1024||m.text.includes('\0'))throw Error('This document cannot be saved.');
    if(m.version!==version||(!shared&&document.version!==version)||(shared&&document.isDirty))throw Error('The document changed elsewhere. Your draft is retained.');
    applying=true;
    try {
     if(shared){
      const result=await services.api('/api/files/document-save',{...target,base_version:version,text:m.text});
      version=result.resource.version;
     } else {
      local.checkpoint(text);
      if(document.version!==version)throw Error('The document changed elsewhere. Your draft is retained.');
      const edit=new vscode.WorkspaceEdit();
      edit.replace(uri,new vscode.Range(document.positionAt(0),document.positionAt(document.getText().length)),m.text);
      if(!await vscode.workspace.applyEdit(edit)||!await document.save())throw Error('Could not save the document. Your draft is retained.');
      version=document.version;
     }
     text=m.text;
     return {version};
    }finally{applying=false;}
   }
   case 'open':{
    const r=m.resource;
    if (r?.kind==='url' && /^https?:\/\//i.test(r.url)) return vscode.env.openExternal(vscode.Uri.parse(r.url));
    if (r?.kind==='file' && r.host_id===file.host_id && typeof r.path==='string' && path.isAbsolute(r.path))
      return vscode.commands.executeCommand('library.openResource',{kind:'file',path:r.path,host_id:r.host_id});
    throw Error('Unsupported document link');
   }
   case 'recover':{
    if(typeof m.text!=='string')throw Error('No draft to recover');
    const copy=await vscode.workspace.openTextDocument({content:m.text,language:html?'html':'markdown'});
    await vscode.window.showTextDocument(copy);
    if(m.writer)local.clearDraft(m.writer,m.text);
    return;
   }
   default:throw Error('Unknown document action');
  }
 }
 let pending,timer,ownVersions=new Set([version]),editEpoch=0;
 function flush() {
  clearTimeout(timer);
  queue=queue.then(async()=>{
   const draft=pending;if(!draft)return;
   pending=undefined;
   try {
    if(draft.epoch!==editEpoch||!ownVersions.has(draft.version))throw Error('The document changed elsewhere. Your draft is retained.');
    const result=await handle({action:'save',text:draft.text,version});
    ownVersions.add(result.version);
    if(!pending)local.clearDraft(writer,draft.text);
    if(!disposed)await panel.webview.postMessage({type:'review-saved',version:result.version,revision:draft.revision});
   }catch(error){
    if(!disposed)await panel.webview.postMessage({type:'review-failed',error:error.message});
   }
  });
  return queue;
 }
 activeFlushes.add(flush);
 const changed=vscode.workspace.onDidChangeTextDocument(e=>{
  if(disposed||applying||e.document!==document||!e.contentChanges.length)return;
  queue=queue.then(async()=>{
   if(!shared){
    version=document.version;text=document.getText();
   } else {
    const next=(await services.api('/api/files/stat?'+new URLSearchParams(target))).resource;
    if(next.version===version&&!document.isDirty)return;
    if(document.isDirty){await panel.webview.postMessage({type:'review-conflict'});return;}
    const fresh=await readDisk();
    if((await services.api('/api/files/stat?'+new URLSearchParams(target))).resource.version!==next.version)return;
    version=next.version;text=fresh;
   }
   ownVersions=new Set([version]);editEpoch++;
   await panel.webview.postMessage({type:'review-update',version,text});
  }).catch(()=>{});
 });
 const receive=panel.webview.onDidReceiveMessage(m=>{
  if(m?.type!=='document-review'||typeof m.id!=='string')return;
  if(m.action==='stage'){
   if(readOnly||typeof m.text!=='string'||!Number.isSafeInteger(m.revision)||Buffer.byteLength(m.text)>5*1024*1024)return;
   try{
    local.saveDraft(writer,m.text);
    pending={text:m.text,version:m.version,revision:m.revision,epoch:editEpoch};
    clearTimeout(timer);timer=setTimeout(flush,350);
   }catch(error){void panel.webview.postMessage({type:'review-failed',error:error.message});}
   return;
  }
  if(m.action==='flush'){void flush();return;}
  queue=queue.then(async()=>{
   try{const value=await handle(m);if(!disposed)await panel.webview.postMessage({type:'review-reply',id:m.id,value});}
   catch(e){if(!disposed)await panel.webview.postMessage({type:'review-reply',id:m.id,error:e.message});}
  });
 });
 panel.onDidDispose(()=>{disposed=true;receive.dispose();changed.dispose();void flush().finally(()=>activeFlushes.delete(flush));});
 return true;
}
module.exports={resolveReviewEditor,targetFor,localReviewStore,flushEditors};
