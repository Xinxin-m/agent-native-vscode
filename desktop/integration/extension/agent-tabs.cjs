"use strict";
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const readline=require('node:readline'),{promisify}=require('node:util'),{execFile}=require('node:child_process');
const runFile=promisify(execFile);
// Codex and Claude chats open as editor tabs in the focused pane. An unused new chat of the same
// provider already in that pane is focused instead of opening another one.
const containers={codexSecondaryViewContainer:'codex',codexViewContainer:'codex','claude-sidebar-secondary':'claude','claude-sidebar':'claude'};
// Codex reuses one tab per resource, so each pane gets its own new-chat route (the query keeps it distinct).
const codexNewChat='openai-codex://route/extension/panel/new';
let newChatSequence=0;
// Codex never retitles a new panel or changes its route. It writes the thread's rollout file when
// the first message is sent, so that file marks the focused new Codex tab as used.
const usedCodexTabs=new Set(),tabKey=uri=>uri?.path+'?'+uri?.query;
function isNewChat(tab,provider){
 const input=tab?.input||{},viewType=String(input.viewType||'').replace(/^mainThreadWebview-/,'');
 if(provider==='codex')return viewType==='chatgpt.conversationEditor'&&input.uri?.path==='/extension/panel/new'&&tab.label==='Codex'&&!usedCodexTabs.has(tabKey(input.uri));
 // Claude renames the tab to the session title after the first message.
 if(provider==='claude')return viewType==='claudeVSCodePanel'&&tab.label==='Claude Code';
 // Library renames a draft after its first run is accepted.
 if(provider==='library')return viewType==='library.conversation'&&['New chat','New conversation'].includes(tab.label);
 return false;
}
function newChatIndex(group,provider){return group?group.tabs.findIndex(tab=>isNewChat(tab,provider)):-1;}
async function focusNewChat(vscode,provider,group=vscode.window.tabGroups.activeTabGroup){
 const index=newChatIndex(group,provider);
 if(index<0)return false;
 await vscode.commands.executeCommand('workbench.action.openEditorAtIndex',index);
 return true;
}
// Subagent rollouts carry an object source, so only threads started from a panel match.
const isPanelThread=head=>/"type":"session_meta"/.test(head)&&/"originator":"codex_vscode"/.test(head)&&/"source":"vscode"/.test(head);
function markCodexTurn(vscode){
 const tab=vscode.window.tabGroups.activeTabGroup?.activeTab;
 if(vscode.window.state.focused&&isNewChat(tab,'codex'))usedCodexTabs.add(tabKey(tab.input.uri));
}
async function head(file,size=4096){const handle=await fs.promises.open(file);try{const {buffer,bytesRead}=await handle.read(Buffer.alloc(size),0,size,0);return buffer.toString('utf8',0,bytesRead);}finally{await handle.close();}}
function watchCodexTurns(vscode,root=path.join(process.env.CODEX_HOME||path.join(os.homedir(),'.codex'),'sessions')){
 let watcher;
 try{watcher=fs.watch(root,{recursive:true},(event,name)=>{
  if(event==='rename'&&/rollout-[^/]*\.jsonl$/.test(name||''))head(path.join(root,name)).then(text=>{if(isPanelThread(text))markCodexTurn(vscode);},()=>{});
 });}catch{return {dispose(){}};}
 return {dispose:()=>watcher.close()};
}
function create(vscode,openLibraryChat){
 const pending=new Map();
 async function openNew(provider,group,viewColumn){
  if(await focusNewChat(vscode,provider,group))return;
  const extension=vscode.extensions.getExtension(provider==='codex'?'openai.chatgpt':'anthropic.claude-code');
  if(!extension)return openLibraryChat();
  // Activating first keeps the call below inside the extension host, so it is not rerouted back here.
  await extension.activate();
  if(provider==='codex')return vscode.commands.executeCommand('vscode.openWith',vscode.Uri.parse(codexNewChat+'?pane='+Date.now()+'-'+(++newChatSequence)),'chatgpt.conversationEditor',{viewColumn,preview:false});
  return vscode.commands.executeCommand('claude-vscode.editor.open',undefined,undefined,viewColumn);
 }
 return function openAgentChat(target){
  const provider=['codex','claude'].includes(target)?target:containers[String(target).replace(/^workbench\.view\.extension\./,'')];
  if(!provider)return;
  // Capture the click's pane before activation yields; another pane may be focused while it loads.
  const group=vscode.window.tabGroups.activeTabGroup,viewColumn=group?.viewColumn||vscode.ViewColumn?.Active||-1;
  const key=provider+':'+viewColumn;
  if(!pending.has(key))pending.set(key,openNew(provider,group,viewColumn).finally(()=>pending.delete(key)));
  return pending.get(key);
 };
}
// library.chatEngine = official: Library opens every chat it can in the official Claude Code and Codex panels.
const extensionIds={claude:'anthropic.claude-code',codex:'openai.chatgpt'},names={claude:'Claude Code',codex:'Codex'};
function providerOf(tab){
 const input=tab?.input||{},viewType=String(input.viewType||'').replace(/^mainThreadWebview-/,'');
 if(viewType==='claudeVSCodePanel')return 'claude';
 if(viewType==='chatgpt.conversationEditor'||input.uri?.scheme==='openai-codex')return 'codex';
}
// Verified against Claude Code 2.1.325: normalized real path, ASCII sanitizing,
// then a signed 32-bit UTF-16 hash suffix when the encoded path exceeds 200 characters.
function projectDir(folder){
 try{folder=fs.realpathSync(folder);}catch{}
 if(process.platform==='darwin')folder=folder.normalize('NFC');
 const encoded=folder.replace(/[^a-zA-Z0-9]/g,'-');if(encoded.length<=200)return encoded;
 let hash=0;for(let i=0;i<folder.length;i++)hash=(31*hash+folder.charCodeAt(i))|0;
 return encoded.slice(0,200)+'-'+Math.abs(hash).toString(36);
}
const validSession=id=>typeof id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(id);
// Official panels stay in this window; unsupported sessions keep the Library pane and its reason.
function route(resource,{root,transcript}){
 const meta=resource.meta||{},key=resource.key;
 const host=resource.host_id||meta.execution_host||meta.host_id||meta.node;
 if(host&&!['mac','local'].includes(host))return {engine:'library',reason:'Remote chats run in a Library pane in this window. Native remote panels require a separate Remote-SSH window.'};
 const requestedFolder=resource.cwd||meta.cwd;
 if(!key&&requestedFolder&&(!root||path.resolve(requestedFolder)!==path.resolve(root)))return {engine:'library',reason:'This folder opens in a Library pane so the agent runs in the folder you selected.'};
 if(!key)return meta.forkSourceKey||meta.editText!==undefined?{engine:'library'}:{engine:'new',provider:extensionIds[meta.source]?meta.source:undefined};
 const provider=key.split('-')[0],id=key.slice(provider.length+1);
 if(!extensionIds[provider]||!validSession(id))return {engine:'library',reason:'Only Claude Code and Codex chats have official panels.'};
 if(resource.runId)return {engine:'library',reason:'This chat has a Library run.'};
 if(meta.is_child||meta.parent_key||meta.parent_session_id||meta.parent_agent_id||(meta.root_key&&meta.root_key!==key)||transcript?.subagent)return {engine:'library',reason:'Opening a subagent thread in an official panel would write to it.'};
 if(!transcript)return {engine:'library',reason:`${names[provider]} has no transcript of this chat on this Mac.`};
 if((provider==='claude'&&!transcript.cwd)||transcript.missingCwd)return {engine:'library',reason:'The session folder is missing.'};
 return {engine:'official',provider,id};
}
function rateLimited(row){
 return row.error==='rate_limit'||row.error==='rate_limit_error'||row.error?.type==='rate_limit_error'||row.error?.code==='rate_limit_exceeded'||(row.type==='system'&&row.subtype==='api_error'&&row.error?.status===429);
}
async function readTranscript(file,provider){
 let cwd,subagent=false,limitReached=false;
 const input=fs.createReadStream(file,{encoding:'utf8'}),lines=readline.createInterface({input,crlfDelay:Infinity});
 for await(const line of lines){let row;try{row=JSON.parse(line);}catch{continue;}
  const data=row.payload||row;if(typeof data.cwd==='string')cwd=data.cwd;
  if(row.isSidechain===true||data.thread_source==='subagent'||(data.source&&typeof data.source==='object'&&data.source.subagent))subagent=true;
  if(rateLimited(row))limitReached=true;else if(row.type==='assistant'&&!row.error)limitReached=false;
 }
 const exists=cwd&&await fs.promises.stat(cwd).then(s=>s.isDirectory(),()=>false);
 return {file,cwd:exists?cwd:undefined,subagent,...(cwd&&!exists?{missingCwd:true}:{}),...(limitReached?{limitReached:true}:{})};
}
async function findTranscript(provider,id,home=os.homedir(),shared=process.env.LIBRARY_OFFICIAL_SHARED||path.join(home,'.library-code-official-shared')){
 if(!validSession(id))return;
 let file;
 if(provider==='claude'){
  // Persisted aliases must never win the canonical transcript scan after restart.
  let manifest;try{manifest=JSON.parse(await fs.promises.readFile(path.join(shared,'session-aliases.json'),'utf8'));}catch(error){if(error.code!=='ENOENT')throw Error('Official transcript alias ownership is unreadable.');}
  for(const [alias,row] of Object.entries(manifest?.aliases||{})){
   if(row.session_id!==id)continue;
   const stat=await fs.promises.lstat(alias).catch(()=>null);
   if(!stat?.isFile()||stat.dev!==row.dev||stat.ino!==row.ino)continue;
   const source=await fs.promises.lstat(row.source).catch(()=>null);
   if(!source?.isFile()||source.dev!==row.dev||source.ino!==row.ino)throw Error('The canonical transcript changed or is missing. Its owned alias was preserved.');
   return readTranscript(row.source,provider);
  }
  const roots=[...new Set([process.env.CLAUDE_CONFIG_DIR||path.join(home,'.claude'),path.join(home,'.claude')])];
  for(const config of roots){
   const root=path.join(config,'projects');
   for(const dir of await fs.promises.readdir(root).catch(()=>[])){
    const candidate=path.join(root,dir,id+'.jsonl');
    const stat=await fs.promises.lstat(candidate).catch(()=>null);
    if(stat?.isFile()&&!stat.isSymbolicLink()){file=candidate;break;}
   }
   if(file)break;
  }
 }else if(provider==='codex'){
  const root=path.join(process.env.CODEX_HOME||path.join(home,'.codex'),'sessions');
  const names=await fs.promises.readdir(root,{recursive:true}).catch(()=>[]);
  for(const name of names.filter(n=>n.endsWith('-'+id+'.jsonl')).sort().reverse()){
   const candidate=path.join(root,name),stat=await fs.promises.lstat(candidate).catch(()=>null);
   if(stat?.isFile()&&!stat.isSymbolicLink()){file=candidate;break;}
  }
 }
 return file?readTranscript(file,provider):undefined;
}
async function aliasHelper(args,shared){
 const installed=path.join(shared,'bin/official-sessions.py'),file=fs.existsSync(installed)?installed:path.join(__dirname,'../official-sessions.py');
 const {stdout}=await runFile('python3',[file,'--shared',shared,...args],{timeout:20000,maxBuffer:1024*1024});return JSON.parse(stdout);
}
async function ensureTranscriptLink(workspace,configDir,id,transcript,options={}){
 if(!validSession(id))throw Error('Invalid session ID.');
 const shared=options.shared||process.env.LIBRARY_OFFICIAL_SHARED||path.join(os.homedir(),'.library-code-official-shared');
 const helper=options.helper|| (args=>aliasHelper(args,shared));
 const file=path.join(configDir,'projects',projectDir(workspace),id+'.jsonl'),owner=require('node:crypto').randomUUID();
 const link=await helper(['alias-acquire','--file',file,'--transcript',transcript,'--session',id,'--owner',owner,'--pid',String(process.pid)]);
 return {...link,shared,helper};
}
async function removeTranscriptLink(link){
 if(!link?.owned)return;
 await (link.helper|| (args=>aliasHelper(args,link.shared)))(['alias-release','--file',link.file,'--owner',link.owner]);
}
async function focusTab(vscode,match){
 for(const group of vscode.window.tabGroups.all){
  const index=group.tabs.findIndex(match);if(index<0)continue;
  const nth=['First','Second','Third','Fourth','Fifth','Sixth','Seventh','Eighth'][group.viewColumn-1];
  if(nth)await vscode.commands.executeCommand(`workbench.action.focus${nth}EditorGroup`);
  await vscode.commands.executeCommand('workbench.action.openEditorAtIndex',index);return true;
 }
 return false;
}
async function openSession(vscode,{provider,id},viewColumn){
 await vscode.extensions.getExtension(extensionIds[provider]).activate();
 if(provider==='claude')return vscode.commands.executeCommand('claude-vscode.editor.open',id,undefined,viewColumn);
 const uri=vscode.Uri.parse('openai-codex://route/local/'+id);
 if(await focusTab(vscode,tab=>tab.input?.uri?.toString()===uri.toString()))return;
 return vscode.commands.executeCommand('vscode.openWith',uri,'chatgpt.conversationEditor',{viewColumn,preview:false});
}
// New chat: the active chat's provider, else the default provider setting, else a picker that highlights the last choice.
async function chooseProvider(vscode,storage,current){
 const active=providerOf(vscode.window.tabGroups.activeTabGroup?.activeTab)||current;
 if(active)return active;
 const preferred=storage.get('preferences')?.defaultProvider;
 if(extensionIds[preferred])return preferred;
 const items=['claude','codex'].map(provider=>({label:names[provider],provider}));
 const picker=vscode.window.createQuickPick();picker.title='New chat';picker.items=items;
 picker.activeItems=[items.find(item=>item.provider===storage.get('officialProvider'))||items[0]];
 const chosen=await new Promise(resolve=>{let value;picker.onDidAccept(()=>{value=picker.selectedItems[0]?.provider;picker.hide();});picker.onDidHide(()=>{picker.dispose();resolve(value);});picker.show();});
 if(chosen)await storage.update('officialProvider',chosen);
 return chosen;
}
function official(vscode,context,openAgentChat,options={}){
 const enabled=()=>vscode.workspace.getConfiguration('library').get('chatEngine')==='official';
 const shared=options.shared||process.env.LIBRARY_OFFICIAL_SHARED||path.join(os.homedir(),'.library-code-official-shared');
 const helper=options.helper|| (async args=>{
  const {stdout}=await runFile('python3',[path.join(shared,'bin/official-sessions.py'),...args],{timeout:20000,maxBuffer:2*1024*1024});return JSON.parse(stdout);
 });
 const api=options.api||(async(route,body)=>{const bridge=require('./bridge.cjs');return bridge.request(bridge.endpoint(vscode.workspace.getConfiguration('library').get('endpoint')),route,body);});
 const locateTranscript=(provider,id)=>findTranscript(provider,id,os.homedir(),shared);
 const tracked=new Map(),opening=new Map();let sessionQueue=Promise.resolve();
 const currentTabs=()=>vscode.window.tabGroups.all.flatMap(group=>group.tabs);
 const info=message=>vscode.window.showInformationMessage(message+' It opens in a Library pane.');
 async function liveRows(resource){
  const inventory=await api('/api/live');if(!Array.isArray(inventory.sessions))throw Error('Live session status is unavailable.');
  const id=resource.key?.slice(resource.key.indexOf('-')+1);
  return inventory.sessions.filter(row=>(row.chat_key===resource.key||row.session_id===id)&&row.alive!==false);
 }
 function release(entry){
  if(entry.releasePromise)return entry.releasePromise;entry.released=true;
  recovery?.forget(entry.tab);
  entry.releasePromise=removeTranscriptLink(entry.link);return entry.releasePromise;
 }
 function routeArgs(resource,target,transcript,workspace,account,explicit=false){
  const args=['route','--session',target.id,'--transcript',transcript.file,'--cwd',transcript.cwd,'--host','mac','--workspace',workspace];
  if(account)args.push('--account',account);if(resource.meta?.model_main)args.push('--model',resource.meta.model_main);
  if(!explicit)args.push('--prefer-saved-account');return args;
 }
 async function fallback(resource,column){
  if(options.openLibrary)return options.openLibrary(resource,column);
  return vscode.commands.executeCommand('library.openResource',{...resource,officialFallback:true},column);
 }
 async function captureTab(target,column){
  const before=new Set(currentTabs());let resolve,timer;
  const find=()=>currentTabs().find(tab=>!before.has(tab)&&providerOf(tab)===target.provider&&(tab.group?.viewColumn||vscode.window.tabGroups.all.find(group=>group.tabs.includes(tab))?.viewColumn)===column);
  const appeared=new Promise(done=>{resolve=done;});
  const listener=vscode.window.tabGroups.onDidChangeTabs?.(()=>{const tab=find();if(tab)resolve(tab);});
  try{await openSession(vscode,target,column);return find()||await Promise.race([appeared,new Promise(done=>{timer=setTimeout(()=>done(undefined),options.tabWaitMs??1500);})]);}
  finally{listener?.dispose();clearTimeout(timer);}
 }
 if(vscode.window.tabGroups.onDidChangeTabs)context.subscriptions.push(vscode.window.tabGroups.onDidChangeTabs(event=>{
  for(const tab of event.closed||[]){const entry=tracked.get(tab);if(entry){tracked.delete(tab);void release(entry).catch(()=>{});}}
 }));
 async function chooseAccount(provider,current){
  const data=await helper(['accounts','--provider',provider]);
  const items=(data.accounts||[]).filter(a=>a.logged_in!==false).map(a=>({label:a.email||a.label||a.id,description:a.id+(a.id===current?' · Current':''),account:a.id}));
  return (await vscode.window.showQuickPick(items,{title:provider==='claude'?'Claude session account':'Codex window account',placeHolder:'Choose a connected account'}))?.account;
 }
 async function switchAccount(resource,selectedAccount){
  if(!enabled())return;
  const pair=[...tracked].find(([tab,entry])=>resource?.key?entry.resource.key===resource.key:tab===vscode.window.tabGroups.activeTabGroup?.activeTab);
  if(!pair){void vscode.window.showInformationMessage('Open this Claude session from Library first, then switch its account.');return;}
  const [tab,entry]=pair;if(entry.target.provider!=='claude')return;
  let closed=false;
  try{
   const idle=rows=>!rows.some(row=>row.status!=='idle');
   if(!idle(await liveRows(entry.resource)))throw Error('This session is running or its state is uncertain. Wait until its turn finishes before switching accounts.');
   const account=selectedAccount||await chooseAccount('claude',entry.account);if(!account||account===entry.account)return;
   // Validate the new route without changing credentials, links or the current session's binding.
   await helper([...routeArgs(entry.resource,entry.target,entry.transcript,entry.workspace,account,true),'--dry-run']);
   if(tracked.get(tab)!==entry||!currentTabs().includes(tab))return;
   const identities=await vscode.commands.executeCommand('_library.chatTabIdentities'),group=vscode.window.tabGroups.all.find(group=>group.tabs.includes(tab));
   if(!Array.isArray(identities)||!identities.some(identity=>identity.key===entry.resource.key&&identity.viewColumn===group?.viewColumn&&identity.editorIndex===group.tabs.indexOf(tab)))throw Error('The panel session changed or its identity is unavailable. Its panel was left unchanged.');
   if(!idle(await liveRows(entry.resource)))throw Error('This session started working while the account picker was open. Its panel was left unchanged.');
   // Only this integration's exact tab may be closed, never another matching title or external process.
   if(!await vscode.window.tabGroups.close(tab,true))return;
   closed=true;tracked.delete(tab);await release(entry);
   const deadline=Date.now()+(options.closeWaitMs??5000);
   while((await liveRows(entry.resource)).length){
    if(Date.now()>=deadline)throw Error('The previous Claude process is still closing. Try opening this session again once it exits.');
    await new Promise(resolve=>setTimeout(resolve,100));
   }
   if(!await open(entry.resource,entry.column,{account,skipLimit:true}))await fallback(entry.resource,entry.column);
  }catch(error){void vscode.window.showErrorMessage(error.message||String(error));if(closed)await fallback(entry.resource,entry.column);}
 }
 async function codexAccountWindow(selectedAccount,resource){
  if(!enabled())return;
  try{
   const account=selectedAccount||await chooseAccount('codex');if(!account)return;
   const args=['codex-window','--account',account];
   if(resource){
    if(!/^codex-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(resource.key||''))throw Error('This Codex session has no stable identifier.');
    const id=resource.key.slice(6),transcript=await locateTranscript('codex',id);if(!transcript)throw Error('The Codex transcript is unavailable.');
    const state=await helper(['classify','--provider','codex','--transcript',transcript.file]);
    if(state.active!==false||!state.failure||(await liveRows(resource)).some(row=>row.status!=='idle'))throw Error('This Codex session is running or no longer has the reported failure.');
    args.push('--session',id);
   }
   if(vscode.env?.appRoot){const app=vscode.env.appRoot.split('/Contents/')[0];if(app.endsWith('.app'))args.push('--app',app);}
   const result=await helper(args),argv=result.argv;
   if(!Array.isArray(argv)||argv[0]!=='open'||!argv.includes('-g'))throw Error('The account launcher returned an invalid background launch command.');
   if(options.launch)await options.launch(argv);else await runFile(argv[0],argv.slice(1),{timeout:20000});
  }catch(error){void vscode.window.showErrorMessage(error.message||String(error));}
 }
 if(vscode.commands.registerCommand){
  require('./chat-references.cjs').register(vscode,context,api);
  context.subscriptions.push(vscode.commands.registerCommand('library.official.switchClaudeAccount',switchAccount));
  context.subscriptions.push(vscode.commands.registerCommand('library.official.openCodexAccountWindow',codexAccountWindow));
 }
 async function adoptNativeTab(tab,identity){
  const existing=tracked.get(tab);if(existing?.resource.key===identity.key)return;
  const provider=identity.provider,id=identity.key.slice(provider.length+1),transcript=await locateTranscript(provider,id);if(!transcript||!currentTabs().includes(tab))return;
  if(existing){tracked.delete(tab);await release(existing);}
  const folder=vscode.workspace.workspaceFolders?.[0]?.uri,entry={tab,resource:{kind:'chat',key:identity.key,title:tab.label},target:{provider,id},transcript,workspace:folder?.scheme==='file'?folder.fsPath:os.homedir(),column:identity.viewColumn};
  if(provider==='claude'){const root=process.env.CLAUDE_CONFIG_DIR||path.join(os.homedir(),'.claude');entry.link=await ensureTranscriptLink(entry.workspace,root,id,transcript.file,{shared,helper});}
  tracked.set(tab,entry);recovery?.track(tab,entry);
 }
 const recovery=enabled()?require('./official-recovery.cjs').create({vscode,context,helper,api,findTranscript:locateTranscript,openLibrary:fallback,switchAccount,codexAccountWindow,options:{noWatch:!!options.noWatch,onIdentity:adoptNativeTab}}):null;
 async function doOpen(resource,column,extra){
  const group=vscode.window.tabGroups.activeTabGroup,capturedColumn=column||group?.viewColumn||vscode.ViewColumn.Active;
  const known=[...tracked].find(([tab,entry])=>entry.resource.key===resource.key&&currentTabs().includes(tab));
  const folder=vscode.workspace.workspaceFolders?.[0]?.uri,provider=resource.key?.split('-')[0];
  let transcript,target;
  try{transcript=resource.key&&extensionIds[provider]?await locateTranscript(provider,resource.key.slice(provider.length+1)):undefined;
   target=route(resource,{root:folder?.fsPath,transcript});
  }catch(error){info(error.message||'The transcript could not be read.');return false;}
  const chosen=target.engine==='library'?undefined:target.provider||await chooseProvider(vscode,context.globalState);
  if(target.engine==='library'||(chosen&&!vscode.extensions.getExtension(extensionIds[chosen]))){const reason=target.reason||(chosen&&`The ${names[chosen]} extension is not installed.`);if(reason)info(reason);return false;}
  if(!chosen)return true;
  if(known){await focusTab(vscode,tab=>tab===known[0]);return true;}
  if(target.engine==='new'){await openAgentChat(chosen);return true;}
  let link;
  try{
   if(vscode.workspace.isTrusted===false)throw Error('Trust this workspace before opening an agent session.');
   const live=await liveRows(resource);
   if(live.length&&!(extra.requestedAccountWindow&&live.every(row=>row.status==='idle')))throw Error('This chat is already running in another panel or process.');
   if(provider==='codex'){const state=await helper(['classify','--provider','codex','--transcript',transcript.file]);if(state.active!==false)throw Error('This Codex session is working or its current state is unavailable.');}
   let account=extra.account||resource.meta?.account_id,explicitAccount=!!extra.account;
   const workspace=folder?.scheme==='file'?folder.fsPath:!folder?os.homedir():undefined;
   if(provider==='claude'){
    if(!workspace)throw Error('Claude needs a local workspace folder for its transcript lookup.');
    const args=routeArgs(resource,target,transcript,workspace,account,explicitAccount);
    const resolved=await helper(args);if(!path.isAbsolute(resolved.config_dir||''))throw Error('The account helper did not return a Claude profile directory.');
    account=resolved.account_id;
    // The extension host looks in its own config root; account profiles share that projects tree.
    const lookupRoot=process.env.CLAUDE_CONFIG_DIR||path.join(os.homedir(),'.claude');
    link=await ensureTranscriptLink(workspace,lookupRoot,target.id,transcript.file,{shared,helper});
   }
   const tab=await captureTab(target,capturedColumn);
   if(tab){const entry={resource,target,transcript,link,account,workspace,column:capturedColumn,tab};tracked.set(tab,entry);recovery?.track(tab,entry);}
   else if(link?.owned)context.subscriptions.push({dispose(){void removeTranscriptLink(link).catch(()=>{});}});
   return true;
  }catch(error){await removeTranscriptLink(link).catch(()=>{});info(error.message||String(error));return false;}
 }
 async function open(resource,column,extra={}){
  if(!enabled()||resource?.kind!=='chat'||resource.officialFallback)return false;
  const key=resource.key||resource.draftId||'new';
  if(!opening.has(key)){
   const capturedColumn=column||vscode.window.tabGroups.activeTabGroup?.viewColumn||vscode.ViewColumn.Active;
   const operation=sessionQueue.then(()=>doOpen(resource,capturedColumn,extra));sessionQueue=operation.catch(()=>{});
   opening.set(key,operation.finally(()=>opening.delete(key)));
  }
  return opening.get(key);
 }
 async function consumeRequestedSession(){
  const key=process.env.LIBRARY_OFFICIAL_OPEN_SESSION,nonce=process.env.LIBRARY_OFFICIAL_OPEN_REQUEST;
  if(!enabled()||!key||!nonce||context.globalState.get('officialOpenRequest')===nonce)return;
  if(!/^[a-f0-9]{32}$/.test(nonce)||!/^codex-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(key)){void vscode.window.showErrorMessage('Invalid requested Official session.');return;}
  const resource={kind:'chat',key,title:'Continued Codex session'};
  try{if(!await open(resource,undefined,{requestedAccountWindow:true}))await fallback(resource);}
  catch(error){void vscode.window.showErrorMessage(error.message||String(error));}
  finally{await context.globalState.update('officialOpenRequest',nonce);}
 }
 const ready=consumeRequestedSession();void ready.catch(error=>vscode.window.showErrorMessage(error.message||String(error)));
 // A fresh empty Official window uses the shared folder; existing panes and sessions are never restored or replaced here.
 if(enabled()&&!vscode.workspace.workspaceFolders?.length&&currentTabs().length===0&&!options.noWorkspaceInit&&!process.env.LIBRARY_OFFICIAL_OPEN_REQUEST){
  const workspace=path.join(shared,'workspace');
  void fs.promises.mkdir(workspace,{recursive:true}).then(()=>vscode.commands.executeCommand('vscode.openFolder',vscode.Uri.file(workspace),{forceReuseWindow:true})).catch(error=>vscode.window.showErrorMessage(error.message));
 }
 context.subscriptions.push({dispose(){for(const entry of tracked.values())void release(entry).catch(()=>{});}});
 return {enabled,open,switchAccount,codexAccountWindow,ready,recover:key=>recovery?.recover(key)};
}
module.exports={isNewChat,newChatIndex,focusNewChat,create,isPanelThread,markCodexTurn,watchCodexTurns,providerOf,projectDir,route,findTranscript,readTranscript,rateLimited,ensureTranscriptLink,removeTranscriptLink,chooseProvider,openSession,official};
