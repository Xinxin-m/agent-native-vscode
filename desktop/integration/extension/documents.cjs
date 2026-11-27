"use strict";
const vscode=require('vscode');const {editMarkdown}=require('./markdown-source.cjs');const {randomBytes}=require('node:crypto');const {inspectHtml,editHtml,previewHtml,resolveHtmlAssets,markdownImageSources}=require('./html-source.cjs');const {createAssetLoader}=require('./document-assets.cjs');
function register(context,services){
 const bridge=require('./bridge.cjs'),base=()=>bridge.endpoint(vscode.workspace.getConfiguration('library').get('endpoint'));
 const assets=services||{api:(p,b)=>bridge.request(base(),p,b),bytes:p=>bridge.requestBytes(base(),p)};
 context.subscriptions.push(vscode.window.registerCustomEditorProvider('library.pdf',{
  async openCustomDocument(uri){return {uri,dispose(){}};},
  async resolveCustomEditor(document,panel){
   const stat=await vscode.workspace.fs.stat(document.uri);if(stat.size>100*1024*1024)throw Error('This PDF exceeds the 100 MB reader limit.');
   const data=Buffer.from(await vscode.workspace.fs.readFile(document.uri)).toString('base64');
   panel.webview.options={enableScripts:true,localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media')]};
   const nonce=randomBytes(24).toString('base64'),uri=n=>panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri,'media',n)).with({query:'v='+encodeURIComponent(nonce)});
   panel.webview.html=`<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' ${panel.webview.cspSource}; worker-src blob: ${panel.webview.cspSource}; connect-src ${panel.webview.cspSource}; style-src ${panel.webview.cspSource} 'unsafe-inline';"><link rel="stylesheet" href="${uri('document.css')}"></head><body><div id="toolbar"><button id="previous">Previous page</button><button id="next">Next page</button><button id="zoomOut">Zoom out</button><button id="zoomIn">Zoom in</button><span id="status" role="status">Loading PDF…</span></div><div id="document"></div><script nonce="${nonce}">window.__PDF_BOOT__={data:'${data}'};</script><script nonce="${nonce}" type="module" src="${uri('pdf.js')}"></script></body></html>`;
  }
 },{webviewOptions:{retainContextWhenHidden:true}}));
 async function resolveCustomTextEditor(document,panel){
  if(await require('./document-review.cjs').resolveReviewEditor(vscode,context,document,panel,assets))return;
  const format=/\.html?$/i.test(document.uri.path)?'html':'markdown';let original=document.getText(),model=format==='html'?inspectHtml(original):null,applying=false,queue=Promise.resolve();
  panel.webview.options={enableScripts:true,localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media'),document.uri]};
  const nonce=randomBytes(24).toString('base64'),uri=n=>panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri,'media',n)).with({query:'v='+encodeURIComponent(nonce)});
  async function snapshot(){
   const version=document.version,text=document.getText(),loader=await createAssetLoader({uri:document.uri,...assets});
   const imageAssets=Object.create(null);let preview=null;
   if(format==='html')preview=previewHtml(await resolveHtmlAssets(text,loader),nonce);
   else for(const src of markdownImageSources(text)){const value=await loader.image(src);if(value)imageAssets[src]=value;}
   if(version!==document.version)return snapshot();
   return {format,text,version,model:format==='html'?inspectHtml(text):null,nonce,assets:imageAssets,readOnly:vscode.workspace.fs.isWritableFileSystem(document.uri.scheme)===false,preview};
  }
  const boot=await snapshot();original=boot.text;model=boot.model;
  panel.webview.html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' ${panel.webview.cspSource}; style-src ${panel.webview.cspSource} 'unsafe-inline' data:; font-src ${panel.webview.cspSource} data:; img-src ${panel.webview.cspSource} data:; frame-src 'self' about:; connect-src 'none';"><link rel="stylesheet" href="${uri('document.css')}"></head><body><div id="toolbar"><button id="save">Save</button><button id="recover" hidden>Recover draft as a copy</button><span id="status">${format==='markdown'?'Markdown':'HTML'} · Edit directly</span></div><div id="document"></div><script nonce="${nonce}">window.__DOCUMENT_BOOT__=${JSON.stringify(boot).replace(/</g,'\\u003c')};</script><script nonce="${nonce}" type="module" src="${uri('document.js')}"></script></body></html>`;
  const receive=panel.webview.onDidReceiveMessage(m=>{queue=queue.then(async()=>{
   if(m.type==='save'){if(!await document.save())throw Error('Could not save the document');await panel.webview.postMessage({type:'diskSaved'});return;}
   if(m.type==='recover'){const text=m.draft?.text||JSON.stringify(m.draft,null,2);const copy=await vscode.workspace.openTextDocument({content:text,language:format==='markdown'?'markdown':'json'});await vscode.window.showTextDocument(copy);return;}
   if(m.type!=='edit')return;
   if(vscode.workspace.fs.isWritableFileSystem(document.uri.scheme)===false)throw Error('This remote document is read-only.');
   if(m.version!==document.version)throw Error('The document changed elsewhere. Your draft is retained; recover a copy before reloading.');
   let text;if(format==='html'){if(m.change?.baseHash!==model.hash)throw Error('HTML preview is stale');text=editHtml(original,model,m.change.changes);}else{text=editMarkdown(original,m.change?.baseText,m.change?.text);}
   if(text!==document.getText()){const edit=new vscode.WorkspaceEdit();edit.replace(document.uri,new vscode.Range(document.positionAt(0),document.positionAt(document.getText().length)),text);applying=true;try{if(!await vscode.workspace.applyEdit(edit))throw Error('Could not apply edit');}finally{applying=false;}}
   await panel.webview.postMessage({type:'saved',version:document.version});
  }).catch(e=>panel.webview.postMessage({type:'error',message:e.message}));});
  const changed=vscode.workspace.onDidChangeTextDocument(e=>{if(e.document.uri.toString()===document.uri.toString()&&!applying){void snapshot().then(value=>{if(value.version!==document.version)return;original=value.text;model=value.model;return panel.webview.postMessage({type:'document',...value});}).catch(error=>panel.webview.postMessage({type:'error',message:error.message}));}});
  panel.onDidDispose(()=>{receive.dispose();changed.dispose();});
 }
 for(const id of ['library.markdown','library.html'])context.subscriptions.push(vscode.window.registerCustomEditorProvider(id,{resolveCustomTextEditor},{webviewOptions:{retainContextWhenHidden:true,enableFindWidget:true},supportsMultipleEditorsPerDocument:false}));
}
module.exports={register};
