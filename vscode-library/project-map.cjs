'use strict';
// Shared native adapter. The webview has no network or filesystem authority.
const fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http'),{randomBytes}=require('node:crypto');
const ENDPOINT='http://127.0.0.1:53962';
const validId=value=>typeof value==='string'&&/^[A-Za-z0-9_.:-]{1,512}$/.test(value)&&!value.includes('..');
function validatePath(value){
 if(typeof value!=='string'||value.length>5000||!value.startsWith('/api/')||value.includes('\\'))throw Error('Invalid project map request');
 const u=new URL(value,ENDPOINT),allowed={'/api/projects':[], '/api/graph':['project','q','offset','limit','period'], '/api/session':['key'], '/api/resource':['id']};
 const names=allowed[u.pathname];if(u.origin!==ENDPOINT||!names||u.hash)throw Error('Unsupported project map request');
 for(const [k,v] of u.searchParams){if(!names.includes(k)||u.searchParams.getAll(k).length!==1)throw Error('Unsupported project map parameter');if(['project','key','id'].includes(k)&&!validId(v))throw Error('Invalid graph identity');if(k==='q'&&v.length>2000)throw Error('Search is too long');if(k==='period'&&!['live','3','7','14','30','all'].includes(v))throw Error('Invalid activity window');if(k==='offset'&&(!/^\d+$/.test(v)||Number(v)>1000000))throw Error('Invalid offset');if(k==='limit'&&(!/^\d+$/.test(v)||Number(v)<1||Number(v)>40))throw Error('Invalid page size');}
 return value;
}
function request(value){validatePath(value);return new Promise((resolve,reject)=>{
 const req=http.get(ENDPOINT+value,{headers:{Accept:'application/json'}},res=>{let total=0;const chunks=[];res.on('data',chunk=>{total+=chunk.length;if(total>32*1024*1024){req.destroy(Error('Project map response is too large'));return;}chunks.push(chunk)});res.on('end',()=>{try{const data=JSON.parse(Buffer.concat(chunks).toString());if(res.statusCode!==200||data.error)throw Error(data.error||'Project map unavailable');resolve(data)}catch(e){reject(e)}});res.on('error',reject)});req.setTimeout(15000,()=>req.destroy(Error('Project map service did not respond')));req.on('error',reject);
 })}
function html(source,boot,nonce,asset,cspSource){
 const policy=`default-src 'none'; script-src 'nonce-${nonce}'; style-src ${cspSource} 'unsafe-inline'; font-src ${cspSource}; img-src ${cspSource} data:; connect-src 'none';`;
 const safe=JSON.stringify(boot).replace(/</g,'\\u003c');
 return source.replace('<head>',`<head><meta http-equiv="Content-Security-Policy" content="${policy}"><script nonce="${nonce}">window.__PROJECT_MAP_BOOT__=${safe};</script>`).replace(/(<script)\s+src="([A-Za-z0-9_.-]+)"/g,(_,tag,name)=>`${tag} nonce="${nonce}" src="${asset(name)}"`).replace(/href="([A-Za-z0-9_.-]+\.css)"/g,(_,name)=>`href="${asset(name)}"`);
}
function create({vscode,context,openSession,theme=()=> 'dark',api=request}){
 const panels=new Map();
 async function setup(panel,project,title){
  const base=vscode.Uri.joinPath(context.extensionUri,'media','project-map');
  panel.webview.options={enableScripts:true,localResourceRoots:[base]};
  const nonce=randomBytes(24).toString('base64'),asset=n=>panel.webview.asWebviewUri(vscode.Uri.joinPath(base,n)).toString()+'?v='+encodeURIComponent(nonce);
  const source=await fs.readFile(path.join(context.extensionPath,'media/project-map/index.html'),'utf8');
  panel.webview.html=html(source,{project,embedded:true,visible:panel.visible===true,theme:theme(),period:'7',resource:{kind:'project-map',project_id:project,title}},nonce,asset,panel.webview.cspSource);
  const receive=panel.webview.onDidReceiveMessage(async m=>{
   if(!m||typeof m.type!=='string')return;
   if(m.type==='project-map:ready'){await panel.webview.postMessage({type:'project-map:visibility',visible:panel.visible===true});return;}
   if(m.type==='project-map:request'){
    if(typeof m.id!=='string'&&typeof m.id!=='number')return;
    try{const data=await api(validatePath(m.path));await panel.webview.postMessage({type:'project-map:response',id:m.id,data})}catch(e){await panel.webview.postMessage({type:'project-map:response',id:m.id,error:e.message})}return;
   }
   try{
    if(m.type==='project-map:copy'&&typeof m.text==='string'&&m.text.length<=2000000)await vscode.env.clipboard.writeText(m.text);
    if(m.type==='project-map:open-session'&&validId(m.key)&&/^(codex|claude)-/.test(m.key))await openSession(m.key);
    if(m.type==='project-map:external'&&typeof m.url==='string'&&m.url.length<4000){const u=new URL(m.url);if(!['https:','http:'].includes(u.protocol)||u.username||u.password)throw Error('Unsupported link');await vscode.env.openExternal(vscode.Uri.parse(u.href));}
   }catch(e){void vscode.window.showErrorMessage(e.message)}
  });
  const visibility=panel.onDidChangeViewState?.(()=>{void panel.webview.postMessage({type:'project-map:visibility',visible:panel.visible===true})});
  panel.onDidDispose(()=>{receive.dispose();visibility?.dispose();panels.delete(project)});panels.set(project,panel);return panel;
 }
 async function open(project,title){
  if(!project){const data=await api('/api/projects');const choice=await vscode.window.showQuickPick((data.projects||[]).filter(p=>validId(p.id)).map(p=>({label:p.name,description:`${p.count||0} conversations`,id:p.id})),{title:'Open project map',matchOnDescription:true});if(!choice)return;project=choice.id;title=choice.label;}
  if(!validId(project))throw Error('Invalid project identity');
  if(panels.has(project)){panels.get(project).reveal();return panels.get(project)}
  const panel=vscode.window.createWebviewPanel('library.projectMap',(title||project)+' · Map',vscode.ViewColumn.Active,{enableScripts:true,retainContextWhenHidden:true,localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media','project-map')]});
  panels.set(project,panel);
  try{return await setup(panel,project,title)}catch(e){panel.dispose();throw e}
 }
 if(vscode.window.registerWebviewPanelSerializer)context.subscriptions.push(vscode.window.registerWebviewPanelSerializer('library.projectMap',{async deserializeWebviewPanel(panel,state){const resource=state?.resource;if(!validId(resource?.project_id)){panel.dispose();return;}await setup(panel,resource.project_id,resource.title)}}));
 context.subscriptions.push({dispose(){for(const p of panels.values())p.dispose();panels.clear()}});
 const setTheme=value=>{if(!['dark','light'].includes(value))return;for(const panel of panels.values())void panel.webview.postMessage({type:'project-map:theme',theme:value})};
 if(vscode.window.onDidChangeActiveColorTheme)context.subscriptions.push(vscode.window.onDidChangeActiveColorTheme(()=>setTheme(theme())));
 return {open,setTheme};
}
module.exports={create,validatePath,validId,html,request};
