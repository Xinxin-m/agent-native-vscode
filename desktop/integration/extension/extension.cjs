"use strict";
const vscode=require('vscode');
const fs=require('node:fs/promises');
const path=require('node:path');
const {randomUUID,randomBytes}=require('node:crypto');
const bridge=require('./bridge.cjs');
const {createReadCache,createDispatcher,scopeState}=require('./rpc-flow.cjs');
const {targetColumn,resourceKey}=require('./panes.cjs');
const agentTabs=require('./agent-tabs.cjs');
const {openWorkspaceFolder,showInitialChats,createWorkspaceChoice}=require('./workspace-navigation.cjs');
const defaults={theme:'dark',fontSize:14,lineHeight:1.65,readingWidth:780,openDefault:'tab',maxAutoPanes:3,defaultProvider:'ask',defaultModels:{},thinking:'collapsed',toolOutput:'folded',composerMax:null,foldComposer:true,sendKey:'enter',whileWorking:'queue',openAtLatest:true,tabGroups:'manual',groupFiles:false,confirmMoves:'some',showHidden:false,liveRefresh:15};
async function revealExisting(vscode,resource,column){
 try{return await vscode.commands.executeCommand('_library.revealExisting',resource,column);}
 catch(error){if(error?.message === "command '_library.revealExisting' not found")return false;throw error;}
}
// A restored tab stays unresolved until shown, so neither the panel map nor revealExisting sees a Sessions tab; match it by title.
async function focusTab(vscode,match){
 for(const group of vscode.window.tabGroups.all){
  const index=group.tabs.findIndex(match);if(index<0)continue;
  const nth=['First','Second','Third','Fourth','Fifth','Sixth','Seventh','Eighth'][group.viewColumn-1];
  if(nth)await vscode.commands.executeCommand(`workbench.action.focus${nth}EditorGroup`);
  await vscode.commands.executeCommand('workbench.action.openEditorAtIndex',index);return true;
 }
 return false;
}
async function choosePaneChat(vscode, api, open, storage){
 const group=vscode.window.tabGroups.activeTabGroup;
 if(!group)return;
 const state=await api('/api/state');
 const projects=new Map((state.projects?.projects||[]).map(project=>[project.id,project.name]));
 const assignments=state.projects?.assignments||{};
 const items=(state.chats||[]).filter(historyChat).map(chat=>{
  const source=String(chat.source||chat.provider||'').toLowerCase();
  const provider=source==='claude'?'Claude':source==='codex'?'Codex':source;
  return {label:chat.title||'Untitled conversation',description:[provider,projects.get(assignments[chat.key])].filter(Boolean).join(' · '),detail:chat.node||chat.host_id||'',chat,provider:source};
 });
 const options=[{id:'all',label:'All agents',icon:'list-unordered'},{id:'codex',label:'Codex',icon:'code'},{id:'claude',label:'Claude',icon:'comment-discussion'}];
 const saved=storage.get('paneHistoryProvider','all');let provider=options.some(option=>option.id===saved)?saved:'all',saveQueue=Promise.resolve();
 const picker=vscode.window.createQuickPick();picker.title='Open chat in this pane';picker.placeholder='Search conversations';picker.matchOnDescription=true;picker.matchOnDetail=true;
 function render(){
  picker.prompt=`Provider: ${options.find(option=>option.id===provider).label} · Use the filter buttons to switch`;
  picker.items=provider==='all'?items:items.filter(item=>item.provider===provider);
  picker.buttons=options.map(option=>({iconPath:new vscode.ThemeIcon(option.icon),tooltip:option.label,toggle:{checked:option.id===provider}}));
 }
 const selected=await new Promise(resolve=>{
  let selection;
  picker.onDidTriggerButton(button=>{
   const next=options.find(option=>option.label===button.tooltip);if(!next)return;
   provider=next.id;render();
   saveQueue=saveQueue.then(()=>storage.update('paneHistoryProvider',next.id)).catch(error=>{void vscode.window.showErrorMessage(`Could not save provider filter: ${error.message||String(error)}`);});
  });
  picker.onDidAccept(()=>{if(picker.selectedItems[0]){selection=picker.selectedItems[0];picker.hide();}});
  picker.onDidHide(()=>{picker.dispose();resolve(selection);});
  render();picker.show();
 });
 await saveQueue;
 if(!selected||!vscode.window.tabGroups.all.includes(group)||!group.viewColumn)return;
 return open({kind:'chat',key:selected.chat.key,title:selected.chat.title||'Untitled conversation',meta:selected.chat},'tab',group.viewColumn);
}
// Same rule as the workbench history: no subagent or automation chats.
function historyChat(chat){return typeof chat.key==='string'&&Boolean(chat.key)&&!chat.automation&&!chat.is_child&&!chat.parent_key&&!chat.parent_agent_id&&!chat.parent_session_id&&(!chat.root_key||chat.root_key===chat.key);}
async function chooseProjectMap(vscode, api, open, project, title){
 if(!project){
  const catalog=await api('/api/project_catalog');
  const items=(catalog.projects||[]).filter(item=>typeof item.id==='string'&&/^[A-Za-z0-9_.:-]{1,512}$/.test(item.id)&&!item.id.includes('..')).map(item=>({label:item.name||item.id,id:item.id}));
  const selected=await vscode.window.showQuickPick(items,{title:'Open project map',placeHolder:'Search projects'});
  if(!selected)return;
  project=selected.id;title=selected.label;
 }
 return open({kind:'project-map',project_id:project,title});
}
function activate(context){
 const doubleClickMs=require('./gestures.cjs').doubleClickInterval();
 const panels=new Map(),views=new Set(),fileViews=new Set();let preferences={...defaults,...context.globalState.get('preferences',{})};
 const chatNavigationViews=new Set();
 const workspaceChoice=createWorkspaceChoice(context.workspaceState,choice=>{for(const w of chatNavigationViews)void w.postMessage({type:'workspaceChoice',choice});});
 const base=()=>bridge.endpoint(vscode.workspace.getConfiguration('library').get('endpoint'));
 const trusted=()=>{if(!vscode.workspace.isTrusted)throw Error('Trust this workspace before running agents or changing accounts.');};
 // A new chat list tells every view to ask for its part at once instead of at its next poll.
 const reads=createReadCache(bridge.request,{sendState:bridge.requestState,onState:()=>{for(const w of views)void w.postMessage({type:'stateChanged'});}});
 const api=(p,b,options)=>reads.request(base(),p,b,options);
 // Index new transcripts when the app opens; views pick them up through the revision poll.
 void Promise.resolve().then(()=>api('/api/sync',{})).catch(()=>{});
 const projectMaps=require('./project-map.cjs').create({vscode,context,openSession:key=>open({kind:'chat',key,title:'Conversation'}),theme:()=>preferences.theme==='system'?(vscode.window.activeColorTheme.kind===vscode.ColorThemeKind.Dark?'dark':'light'):preferences.theme});
 const showError=e=>vscode.window.showErrorMessage(e.message||String(e));
 if(!preferences.interfaceScaleVersion){preferences={...preferences,fontSize:14,interfaceScaleVersion:1};void context.globalState.update('preferences',preferences).catch(showError);}
 void require('./interface-scale.cjs').apply(vscode,preferences.fontSize).catch(showError);
 if(['beige','sage'].includes(preferences.theme))void require('./themes.cjs').apply(vscode,preferences.theme).catch(showError);
 const filesTab=require('./files-tab.cjs').create(vscode,{api,backups:vscode.Uri.joinPath(context.globalStorageUri,'file-backups')});
 const refreshFiles=()=>{for(const w of fileViews)void w.postMessage({type:'refresh'});};
 context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(()=>{for(const w of views)void w.postMessage({type:'refresh'});}));
 context.subscriptions.push(require('./github-accounts.cjs').watch(vscode,()=>{for(const w of views)void w.postMessage({type:'refresh'});}));
 context.subscriptions.push(vscode.workspace.onDidRenameFiles(refreshFiles),vscode.workspace.onDidCreateFiles(refreshFiles),vscode.workspace.onDidDeleteFiles(refreshFiles),vscode.workspace.onDidChangeWorkspaceFolders(refreshFiles));
 const remoteProvider=require('./remote-files.cjs').createProvider(vscode,api,p=>bridge.requestBytes(base(),p));
 context.subscriptions.push(remoteProvider,vscode.workspace.registerFileSystemProvider('library-file',remoteProvider,{isCaseSensitive:true,isReadonly:true}));
 require('./remote-search.cjs').register(vscode,context,api);
 const referenceProvider=require('./remote-document-reference.cjs').createProvider(vscode,api,p=>bridge.requestBytes(base(),p));
 context.subscriptions.push(referenceProvider,vscode.workspace.registerFileSystemProvider('library-reference',referenceProvider,{isCaseSensitive:true,isReadonly:true}));
 async function addWorkspace(){
  const hosts=(await api('/api/hosts')).hosts||[];
  const choice=await vscode.window.showQuickPick([{label:'This Mac',id:'mac'},...hosts.filter(h=>h.id!=='mac'&&h.configured!==false).map(h=>({label:h.label||h.id,description:'Remote files · read-only',id:h.id}))],{title:'Add a workspace to Files'});if(!choice)return;
  let uri,name,selectedFile;
  if(choice.id==='mac'){const picked=await vscode.window.showOpenDialog({canSelectFolders:true,canSelectFiles:true,canSelectMany:false,title:'Open a folder or file'});if(!picked?.length)return;const stat=await vscode.workspace.fs.stat(picked[0]);if(stat.type&vscode.FileType.Directory)uri=picked[0];else {selectedFile=picked[0];uri=vscode.Uri.file(path.dirname(selectedFile.fsPath));}name=path.basename(uri.fsPath);}
  else {const host=hosts.find(h=>h.id===choice.id);const selected=await require('./remote-browser.cjs').browse(vscode,{...host,label:choice.label});if(!selected)return;
   const root=(await api('/api/files/roots',{host_id:choice.id,path:selected.folder,access:'read'})).root;
   uri=vscode.Uri.from({scheme:'library-file',authority:root.id,path:'/'});name=(path.posix.basename(root.path)||root.path)+' · '+choice.label;
   if(selected.file)selectedFile=vscode.Uri.joinPath(uri,path.posix.basename(selected.file));
  }
  await openWorkspaceFolder(vscode,{uri,name});
  if(selectedFile)await vscode.commands.executeCommand('vscode.open',selectedFile,{preview:false});
 }

 async function layout(development){
  await vscode.workspace.getConfiguration('library').update('layout',development?'development':'library',vscode.ConfigurationTarget.Global);
  await vscode.workspace.getConfiguration('workbench').update('activityBar.location',require('./icon-bar.cjs').location(vscode),vscode.ConfigurationTarget.Global);
  await vscode.workspace.getConfiguration('workbench').update('statusBar.visible',development,vscode.ConfigurationTarget.Global);
  await vscode.commands.executeCommand('workbench.action.closeAuxiliaryBar');
  await vscode.commands.executeCommand(development?'workbench.view.explorer':'workbench.view.extension.library');
 }
 async function open(resource,destination,explicitColumn){
  if(!resource||typeof resource.kind!=='string')throw Error('Invalid resource');
  if(resource.kind==='files'&&resource.root_id===undefined){const location=await require('./file-context.cjs').workspaceFor(vscode,filesTab.rootId);if(vscode.workspace.getConfiguration('library').get('files.tabWorkspace')==='all')location.root_id='all';resource={...resource,...location};}
  if(resource.kind==='project-map'){if(resource.project_id&&await revealExisting(vscode,resource))return;await projectMaps.open(resource.project_id,resource.title);return;}
  const columns=vscode.window.tabGroups.all.map(g=>g.viewColumn),active=vscode.window.tabGroups.activeTabGroup.viewColumn;
  const dest=destination||(resource.kind==='file'?preferences.openDefault:'tab')||'tab';
  let column=explicitColumn||targetColumn(columns,active,dest,preferences.maxAutoPanes);
  if(!explicitColumn&&['right','below'].includes(dest)&&(preferences.maxAutoPanes<=0||columns.length<preferences.maxAutoPanes)){
   await vscode.commands.executeCommand(dest==='below'?'workbench.action.newGroupBelow':'workbench.action.newGroupRight');
   column=vscode.window.tabGroups.activeTabGroup.viewColumn;
  }
  if(resource.kind==='file'){
   if(!['mac','local',undefined].includes(resource.host_id)){
    const roots=(await api('/api/files/roots?host_id='+encodeURIComponent(resource.host_id))).roots||[];
    const root=roots.filter(r=>resource.root_id?r.id===resource.root_id:resource.path===r.path||resource.path.startsWith(r.path.replace(/\/$/,'')+'/')).sort((a,b)=>b.path.length-a.path.length)[0];
    let uri,reader=remoteProvider;
    if(root){
     const relative=path.posix.relative(root.path,resource.path);if(relative.startsWith('..')||path.posix.isAbsolute(relative))throw Error('Remote file is outside its workspace');
     uri=vscode.Uri.from({scheme:'library-file',authority:root.id,path:'/'+relative});
    }else{uri=await referenceProvider.resolve(resource);reader=referenceProvider;}
    if(['browser-tab','default-browser','system'].includes(dest)){
     const bytes=await reader.readFile(uri);const folder=path.join(context.globalStorageUri.fsPath,'remote-open',randomUUID());await fs.mkdir(folder,{recursive:true});
     const cached=path.join(folder,path.posix.basename(resource.path)||'remote-file');await fs.writeFile(cached,bytes,{flag:'wx'});
     if(!await require('./external-open.cjs').openExternal(vscode.Uri.file(cached).toString(),dest!=='system'))await vscode.env.openExternal(vscode.Uri.file(cached));return;
    }
    await vscode.commands.executeCommand('vscode.open',uri,{viewColumn:column,preview:dest!=='tab'});return;
   }
   if(typeof resource.path!=='string'||!path.isAbsolute(resource.path)||resource.path.includes('\0'))throw Error('An absolute file path is required');
   const stat=await fs.stat(resource.path);if(!stat.isFile())throw Error('Select a regular file');
   const uri=vscode.Uri.file(resource.path);
   if(['browser-tab','default-browser','system'].includes(dest)){if(!await require('./external-open.cjs').openExternal(uri.toString(),dest!=='system'))await vscode.env.openExternal(uri);return;}
   await require('./file-context.cjs').workspaceFor(vscode,filesTab.rootId,uri);
   // Native preview semantics preserve dirty buffers without closing or replacing them.
   await vscode.commands.executeCommand('vscode.open',uri,{viewColumn:column,preview:dest!=='tab',...(resource.line?{selection:new vscode.Range(Math.max(0,resource.line-1),0,Math.max(0,resource.line-1),0)}:{})});return;
  }
  if(resource.kind==='url'){
   const u=new URL(resource.url);if(!['https:','http:'].includes(u.protocol))throw Error('Unsupported link');
   if(['default-browser','browser-tab','system'].includes(dest)){if(!await require('./external-open.cjs').openExternal(u.href))await vscode.env.openExternal(vscode.Uri.parse(u.href));}
   else await vscode.commands.executeCommand('simpleBrowser.show',u.href,{viewColumn:column,preserveFocus:false});return;
  }
  if(resource.kind==='chat'&&await officialChats.open(resource,explicitColumn))return;
  if(resource.kind==='chat'&&resource.draftId&&!resource.key&&Object.keys(resource.meta||{}).every(k=>k==='source')&&!explicitColumn&&dest==='tab'&&await agentTabs.focusNewChat(vscode,'library'))return;
  const key=resourceKey(resource);if(panels.has(key)){const panel=panels.get(key);if(resource.kind==='files'&&resource.meta?.filesPath)void panel.webview.postMessage({type:'filesContext',resource});panel.reveal(destination||explicitColumn?column:undefined);return;}
  if(await revealExisting(vscode,resource,destination||explicitColumn?column:undefined))return;
  if(['sessions','files'].includes(resource.kind)&&await focusTab(vscode,t=>/library\.conversation$/.test(t.input?.viewType||'')&&t.label===resource.title))return;
  if(panels.has(key)){const panel=panels.get(key);if(resource.kind==='files'&&resource.meta?.filesPath)void panel.webview.postMessage({type:'filesContext',resource});panel.reveal(destination||explicitColumn?column:undefined);return;}
  const panel=vscode.window.createWebviewPanel('library.conversation',resource.title||'New conversation',column,{enableScripts:true,retainContextWhenHidden:true,localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media')]});
  panels.set(key,panel);panel.iconPath=new vscode.ThemeIcon(resource.kind==='chat'?'comment-discussion':resource.kind==='settings'?'settings-gear':resource.kind==='files'?'folder':'library');
  connect(panel,resource.kind,resource,key);
 }
 function connect(view,kind,resource,key){
  const webview=view.webview,disposables=[],streams=new Map();views.add(webview);if(kind==='files')fileViews.add(webview);
  if(['sidebar','projects','recent'].includes(kind))chatNavigationViews.add(webview);
  const storageKey='draft:'+key;
  let build;try{build=require('./build-info.json')}catch{}
  const configuration={kind,resource,preferences,build,doubleClickMs,workspaceChoice:workspaceChoice.get(),storage:context.workspaceState.get(storageKey,{})};
  webview.options={enableScripts:true,localResourceRoots:[vscode.Uri.joinPath(context.extensionUri,'media')]};
  const nonce=randomBytes(24).toString('base64');const media=n=>webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri,'media',n)).with({query:'v='+encodeURIComponent(nonce)}).toString();
  webview.html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' ${webview.cspSource}; style-src ${webview.cspSource} 'unsafe-inline'; font-src ${webview.cspSource} data:; img-src ${webview.cspSource} data: blob: https:; connect-src 'none';"><link rel="stylesheet" href="${media('main.css')}"></head><body><div id="root"></div><script nonce="${nonce}">window.__LIBRARY_BOOT__=${JSON.stringify(configuration).replace(/</g,'\\u003c')};</script><script nonce="${nonce}" type="module" src="${media('boot.js')}"></script></body></html>`;
  async function handle(m){
   // A Files tab reads and moves workspace files itself; other tabs and the tree refresh after a move.
   if(m.action==='api'&&kind==='files'&&String(m.path).startsWith('/api/files/')){if(m.body!==undefined)trusted();const value=await filesTab.request(m.path,m.body);if(m.body!==undefined)for(const w of fileViews)if(w!==webview)void w.postMessage({type:'refresh'});return value;}
   if(m.action==='filesRoot'){if(kind!=='files'||typeof m.root_id!=='string'||typeof m.title!=='string')throw Error('Invalid workspace');if(panels.get(key)===view)panels.delete(key);resource={...resource,root_id:m.root_id,title:m.title.slice(0,150)};key=resourceKey(resource);panels.set(key,view);view.title=resource.title;return true;}
   if(m.action==='api'){
    bridge.route(m.path,m.body===undefined?'GET':'POST');
    const inboxAction=m.path==='/api/notices'&&['read','archive'].includes(m.body?.action);
    if(m.body!==undefined&&!inboxAction)trusted();
    if(m.path==='/api/conversation/folder'){
     if(m.body?.host_id!=='mac')return api(m.path,m.body);
     const found=await vscode.window.showOpenDialog({canSelectFiles:false,canSelectFolders:true,canSelectMany:false,defaultUri:m.body.path?vscode.Uri.file(m.body.path):undefined});return {path:found?.[0]?.fsPath||''};
    }
    if(m.path==='/api/state'&&m.body===undefined)return scopeState(await api(m.path,undefined,{fresh:m.fresh===true}),kind==='chat'?(Array.isArray(m.keys)?m.keys:[resource?.key]):undefined,m.since);
    return api(m.path,m.body,{fresh:m.fresh===true});
   }
   if(m.action==='officialRecovery'){
    trusted();
    if(kind!=='sidebar'||!officialChats.enabled())throw Error('Official recovery is unavailable in this view.');
    return officialChats.recover(m.key);
   }
   if(m.action==='workspaceRoots')return filesTab.request('/api/files/roots');
   if(m.action==='setWorkspaceChoice'&&chatNavigationViews.has(webview))return workspaceChoice.set(m.choice);
   if(m.action==='tabIdentityReady')return vscode.commands.executeCommand('_library.refreshTabGroups');
   if(m.action==='storage'){if(!m.values||typeof m.values!=='object'||JSON.stringify(m.values).length>12000000)throw Error('Draft storage limit exceeded; keep this tab open.');await context.workspaceState.update(storageKey,m.values);return true;}
   if(m.action==='copy'){if(typeof m.text==='string')await vscode.env.clipboard.writeText(m.text);return true;}
   if(m.action==='open')return open(m.resource,m.destination);
   if(m.action==='history')return choosePaneChat(vscode,api,async(next,destination,column)=>{
    // A tab with a draft or a running turn keeps it; the chosen chat opens beside it in this pane.
    const nextKey=resourceKey(next);
    if(m.busy||kind!=='chat'||panels.has(nextKey)||officialChats.enabled())return open(next,destination,column);
    if(await revealExisting(vscode,next,column))return;
    if(panels.get(key)===view)panels.delete(key);release();
    panels.set(nextKey,view);view.title=next.title;connect(view,'chat',next,nextKey);
   },context.globalState);
   if(m.action==='title'){if('title' in view&&typeof m.title==='string')view.title=m.title.slice(0,150);return true;}
   if(m.action==='preferences'){const previous=preferences,next={...preferences,...m.value};require('./interface-scale.cjs').zoomLevel(next.fontSize);try{if(previous.fontSize!==next.fontSize)await require('./interface-scale.cjs').apply(vscode,next.fontSize);if(previous.theme!==next.theme)await require('./themes.cjs').apply(vscode,next.theme);await context.globalState.update('preferences',next);}catch(error){await require('./interface-scale.cjs').apply(vscode,previous.fontSize).catch(()=>{});throw error;}preferences=next;for(const w of views)void w.postMessage({type:'preferences',value:preferences});projectMaps.setTheme(preferences.theme==='system'?(vscode.window.activeColorTheme.kind===vscode.ColorThemeKind.Dark?'dark':'light'):preferences.theme);return true;}
   if(m.action==='githubAccounts'){if(kind!=='accounts'&&kind!=='settings')throw Error('Account actions are only available in Accounts.');if(m.connect)trusted();return require('./github-accounts.cjs').list(vscode,Boolean(m.connect));}
   if(m.action==='rename'){trusted();if(typeof m.key!=='string'||!m.key)throw Error('Conversation is unavailable');if(typeof m.title!=='string'||!m.title.trim()||m.title.length>200)throw Error('Enter a title between 1 and 200 characters');const result=await api('/api/chat_state',{action:'rename',key:m.key,title:m.title.trim()});for(const w of views)void w.postMessage({type:'refresh'});return result.title;}
   if(m.action==='sessionDetails'){const session=(await api('/api/live')).sessions?.find(s=>s.id===m.id);if(!session)throw Error('This session is no longer observed');const owner=session.where==='vscode'?'Visual Studio Code':session.where||'its original app';const options=session.where==='vscode'?['Open Visual Studio Code','Refresh Library']:['Refresh Library'];const choice=await vscode.window.showInformationMessage(`${session.name||'This session'} is running in ${owner}. Its conversation is not indexed in Library yet.`,...options);if(choice==='Open Visual Studio Code')await require('node:util').promisify(require('node:child_process').execFile)('/usr/bin/open',['-b','com.microsoft.VSCode']);if(choice==='Refresh Library')for(const w of views)void w.postMessage({type:'refresh'});return;}
   if(m.action==='imageResource')return bridge.imageResource(base(),m.path);
   if(m.action==='models'){trusted();if(m.host_id!=='mac')return api('/api/models/discover',{host_id:m.host_id,provider:m.provider||'claude',account_id:m.account,cwd:m.cwd});return require('./models.cjs').models(await api('/api/accounts'),m.account,m.cwd);}
   if(m.action==='confirm'){if(typeof m.message!=='string'||m.message.length>2000)throw Error('Invalid confirmation');const label=m.button==='Stop'?'Stop':'Continue';return (await vscode.window.showWarningMessage(m.message,{modal:true},label))===label;}
   if(m.action==='terminal')return vscode.commands.executeCommand('workbench.action.terminal.toggleTerminal');
   if(m.action==='showChats')return vscode.commands.executeCommand('workbench.view.extension.library');
   if(m.action==='toggleSidebar')return vscode.commands.executeCommand('workbench.action.toggleSidebarVisibility');
   if(m.action==='development')return layout(true);
   if(m.action==='config'){if(kind!=='settings')throw Error('Settings are only available in Settings.');return require('./settings-config.cjs').handle(vscode,m);}
   if(m.action==='advancedEditor'){if(kind!=='settings')throw Error('Editor tools are only available in Settings.');return require('./advanced-editor.cjs').run(vscode,m.tool);}
   if(m.action==='endpoint'){const value=await vscode.window.showInputBox({title:'Connect to Library',value:base(),prompt:'HTTP loopback address of your existing Library service',validateInput:v=>{try{bridge.endpoint(v)}catch(e){return e.message}}});if(value)await vscode.workspace.getConfiguration('library').update('endpoint',value,vscode.ConfigurationTarget.Global);return true;}
   if(m.action==='subscribe'){
    if(typeof m.runId!=='string'||typeof m.token!=='string'||streams.size>=12)throw Error('Invalid event subscription');
    streams.get(m.token)?.();streams.set(m.token,bridge.subscribe(base(),m.runId,m.after,entry=>webview.postMessage({type:'event',token:m.token,entry}),()=>{streams.delete(m.token);void webview.postMessage({type:'eventEnd',token:m.token});}));return true;
   }
   if(m.action==='unsubscribe'){streams.get(m.token)?.();streams.delete(m.token);return true;}
   throw Error('Unsupported Library action');
  }
  const dispatch=createDispatcher(handle);
  disposables.push(webview.onDidReceiveMessage(m=>{
   if(m?.type!=='rpc'||typeof m.id!=='string')return;
   // Draft persistence and all mutations share FIFO; reads/UI never wait for a slow GET.
   void dispatch(m).then(value=>webview.postMessage({type:'reply',id:m.id,value}),e=>webview.postMessage({type:'reply',id:m.id,error:e.message,details:{status:e.status,data:e.data,code:e.data?.code}})).catch(()=>{});
  }));
  function release(){views.delete(webview);chatNavigationViews.delete(webview);fileViews.delete(webview);for(const stop of streams.values())stop();for(const d of disposables)d.dispose();}
  disposables.push(view.onDidDispose(()=>{if(panels.get(key)===view)panels.delete(key);release();}));
  // A background tab or collapsed sidebar stops polling until it is shown again.
  disposables.push((view.onDidChangeViewState||view.onDidChangeVisibility).call(view,()=>void webview.postMessage({type:'visibility',visible:view.visible})));
 }
 context.subscriptions.push(vscode.window.registerWebviewViewProvider('library.navigation',{resolveWebviewView(view){connect(view,'sidebar',null,'sidebar');}},{webviewOptions:{retainContextWhenHidden:true}}));
 context.subscriptions.push(vscode.window.registerWebviewPanelSerializer('library.conversation',{async deserializeWebviewPanel(panel,state){const resource=state?.resource;if(resource){const key=resourceKey(resource);panels.set(key,panel);connect(panel,resource.kind,resource,key);}else{panel.dispose();}}}));
 require('./tab-cleanup.cjs').register(vscode,context);
 require('./tab-project-groups.cjs').register(vscode,context,api);
 require('./icon-bar.cjs').register(vscode,context);
 const command=(name,fn)=>context.subscriptions.push(vscode.commands.registerCommand(name,(...args)=>Promise.resolve().then(()=>fn(...args)).catch(showError)));
 command('library.addWorkspace',addWorkspace);
 command('library.newFile',uri=>{trusted();return require('./file-actions.cjs').createItem(vscode,filesTab,'file',uri).then(refreshFiles);});
 command('library.newFolder',uri=>{trusted();return require('./file-actions.cjs').createItem(vscode,filesTab,'directory',uri).then(refreshFiles);});
 command('library.openResource',(resource,column)=>open(resource,'tab',column));
 context.subscriptions.push(agentTabs.watchCodexTurns(vscode));
 const openAgentChat=agentTabs.create(vscode,()=>open({kind:'chat',draftId:randomUUID(),title:'New conversation'}));
 command('_library.openAgentChat',openAgentChat);
 const officialChats=agentTabs.official(vscode,context,openAgentChat);
 command('library.arrange',async()=>{const choice=await vscode.window.showQuickPick([{label:'Arrange automatically',command:'library.arrangeAutomatically'},{label:'Split right',command:'workbench.action.newGroupRight'},{label:'Split down',command:'workbench.action.newGroupBelow'},{label:'Stack panes vertically',command:'workbench.action.editorLayoutTwoRows'},{label:'Move whole pane left',command:'workbench.action.moveActiveEditorGroupLeft'},{label:'Move whole pane right',command:'workbench.action.moveActiveEditorGroupRight'},{label:'Move whole pane up',command:'workbench.action.moveActiveEditorGroupUp'},{label:'Move whole pane down',command:'workbench.action.moveActiveEditorGroupDown'}],{title:'Arrange panes',ignoreFocusOut:false});if(!choice)return;await vscode.commands.executeCommand('workbench.action.closeAuxiliaryBar');return vscode.commands.executeCommand(choice.command||'_library.arrangeEditors',choice.preset);});
 command('library.transferFiles',async request=>{trusted();return require('./transfer-files.cjs').transferFiles(vscode,filesTab,request,async()=>{remoteProvider.refresh();refreshFiles();await vscode.commands.executeCommand('workbench.files.action.refreshFilesExplorer');});});
 command('library.removeWorkspace',uri=>{const folder=(vscode.workspace.workspaceFolders||[]).find(f=>f.uri.toString()===uri);if(folder&&!vscode.workspace.updateWorkspaceFolders(folder.index,1))throw Error('Could not close workspace');});
 command('library.search',()=>{for(const w of views)void w.postMessage({type:'search'});});
 command('library.refresh',()=>{reads.invalidate();remoteProvider.refresh();for(const w of views)void w.postMessage({type:'refresh'});});
 command('library.splitRight',()=>vscode.commands.executeCommand('workbench.action.newGroupRight'));
 command('library.splitDown',()=>vscode.commands.executeCommand('workbench.action.newGroupBelow'));
 // The tab-bar plus starts a chat with the active chat's provider, else the default provider, else asks.
 const newChatSources=new Map(),activeLibraryProvider=()=>{for(const [key,panel] of panels)if(panel.active&&panel.viewColumn===vscode.window.tabGroups.activeTabGroup?.viewColumn)return /^chat:(claude|codex)-/.exec(key)?.[1]||newChatSources.get(key);};
 command('library.new',async()=>{const source=await agentTabs.chooseProvider(vscode,context.globalState,activeLibraryProvider());if(!source)return;const draftId=randomUUID();newChatSources.set('chat:'+draftId,source);return open({kind:'chat',draftId,title:'New conversation',meta:{source}});});
 command('library.openChat',()=>choosePaneChat(vscode,api,open,context.globalState));
 command('library.show',()=>layout(false));command('library.development',()=>layout(true));
 command('library.openProjectMap',(project,title)=>chooseProjectMap(vscode,api,open,project,title));
 command('library.accounts',()=>open({kind:'accounts',title:'Accounts'}));
 command('library.settings',()=>open({kind:'settings',title:'Settings'}));command('library.projects',()=>open({kind:'projects',title:'Projects'}));
 command('library.openFiles',async uri=>{const location=uri?await require('./file-context.cjs').workspaceFor(vscode,filesTab.rootId,uri):{};return open({kind:'files',title:'Files',...location});});
 if(true){
  if(!context.globalState.get('quietChromeV3')){void (async()=>{const config=vscode.workspace.getConfiguration('workbench');await config.update('statusBar.visible',false,vscode.ConfigurationTarget.Global);await config.update('layoutControl.enabled',false,vscode.ConfigurationTarget.Global);await context.globalState.update('quietChromeV3',true);})().catch(showError);}
  if(!context.globalState.get('unifiedRailV1')){
   void vscode.workspace.getConfiguration('workbench').update('activityBar.location',require('./icon-bar.cjs').location(vscode),vscode.ConfigurationTarget.Global);
   void vscode.workspace.getConfiguration('library').update('layout','library',vscode.ConfigurationTarget.Global);
   void context.globalState.update('unifiedRailV1',true);
  }
  void vscode.commands.executeCommand('workbench.action.closeAuxiliaryBar').catch(()=>{});
  void showInitialChats(vscode,context.workspaceState,()=>{if(!officialChats.enabled())return open({kind:'chat',draftId:randomUUID(),title:'New conversation'});}).catch(showError);
 }
 if(require('node:fs').existsSync(path.join(__dirname,'documents.cjs')))require('./documents.cjs').register(context);
}
module.exports={activate};
