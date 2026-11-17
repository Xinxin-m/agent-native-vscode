'use strict';
// Official webviews are owned by their providers. Recovery uses VS Code notifications,
// and never injects scripts, submits a provider prompt, or closes an unowned panel.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {promisify}=require('node:util'),{execFile}=require('node:child_process');
const runFile=promisify(execFile);
function providerOf(tab){const type=String(tab?.input?.viewType||'').replace(/^mainThreadWebview-/,'');return type==='claudeVSCodePanel'?'claude':type==='chatgpt.conversationEditor'?'codex':undefined;}
function text(failure,label){
 const provider=failure.provider==='codex'?'Codex':'Claude',account=label||failure.account_id||'this account';
 if(['auth_expired','not_logged_in'].includes(failure.kind))return `${provider} sign-in expired for ${account}.`;
 const reset=failure.resets_at?new Date(failure.resets_at):null,suffix=reset&&!Number.isNaN(reset.valueOf())?' Resets '+reset.toLocaleString()+'.':'';
 if(failure.kind==='limit_reached')return `${provider} usage limit reached on ${account}.`+suffix;
 return `${provider} is temporarily rate limited.`+suffix;
}
function create({vscode,context,helper,api,findTranscript,openLibrary,switchAccount,codexAccountWindow,options={}}){
 const entries=new Map(),seen=new Set(),timers=new Map(),busy=new Map(),watchers=[],warningSeen=new Set(),pendingLogins=new Map();
 const home=options.home||os.homedir(),claudeRoot=process.env.CLAUDE_CONFIG_DIR||path.join(home,'.claude'),codexRoot=process.env.CODEX_HOME||path.join(home,'.codex');
 let disposed=false,processesAt=0,parents=new Map();
 const tabs=()=>vscode.window.tabGroups.all.flatMap(group=>group.tabs);
 async function ownClaude(id){
  if(options.ownsClaude)return options.ownsClaude(id);
  let accounts=[];try{accounts=(await helper(['accounts','--provider','claude'])).accounts||[];}catch{}
  const roots=[...new Set([claudeRoot,...accounts.map(a=>a.config_dir).filter(Boolean)])];
  for(const root of roots){const directory=path.join(root,'sessions');
  for(const name of await fs.promises.readdir(directory).catch(()=>[])){
   if(!name.endsWith('.json'))continue;
   let item;try{item=JSON.parse(await fs.promises.readFile(path.join(directory,name),'utf8'));}catch{continue;}
   if(item.sessionId!==id||!Number.isInteger(item.pid))continue;
   if(Date.now()-processesAt>1000){const {stdout}=await runFile('ps',['-axo','pid=,ppid='],{timeout:3000,maxBuffer:2*1024*1024});parents=new Map(stdout.trim().split('\n').map(line=>line.trim().split(/\s+/).map(Number)));processesAt=Date.now();}
   let pid=item.pid;for(let depth=0;pid&&depth<32;depth++){if(pid===process.pid)return true;pid=parents.get(pid);}
  }}
  return false;
 }
 async function finishLogin(entry,login,account){
  if(login.renewal_pending){pendingLogins.set(account,entry);void vscode.window.showInformationMessage(login.message||'Sign-in will open when chats are safely idle.');return;}
  const url=login.authorization_url||login.url;
  if(url&&!login.browser_opened){
   if(new URL(url).protocol!=='https:')throw Error('The sign-in service returned an invalid authorization URL.');
   if(options.openBrowser)await options.openBrowser(url);else await runFile('open',['-g',url],{timeout:10000});
  }
  if(login.code_required||login.needs_code){
   const code=await vscode.window.showInputBox({title:'Finish '+(entry.provider==='codex'?'Codex':'Claude')+' sign-in',prompt:'Paste the authorization code from your browser',ignoreFocusOut:true});
   if(!code)return;
   await api('/api/accounts/login/code',{id:account,login_id:login.login_id,code});
  }
  const verified=await api('/api/accounts/verify',{id:account});
  const row=(verified.accounts||[]).find(item=>item.id===account&&item.logged_in&&item.email);
  void vscode.window.showInformationMessage(row?'Sign-in verified. Retry from the provider panel; your unsent draft is unchanged.':'Finish signing in in your browser. The account is not verified yet; your draft remains unchanged.');
 }
 async function signIn(entry,failure,prevention=false){
  const account=failure.account_id||entry.account;if(!account)throw Error('This session’s account could not be identified. Open Accounts to choose the correct profile.');
  const response=prevention?await api('/api/accounts/login',{id:account,when_idle:true,open_browser:true}):await api('/api/run/recover',{key:entry.resource.key,action:'login',account_id:account,when_idle:true,open_browser:true});
  await finishLogin(entry,response.login||response,response.account_id||account);
 }
 async function checkWarnings(){
  if(disposed)return;const response=await api('/api/accounts'),accounts=[...(response.accounts||[]),...(response.pending_accounts||[])];
  for(const account of accounts){
   if(pendingLogins.has(account.id)&&account.login){const entry=pendingLogins.get(account.id);pendingLogins.delete(account.id);await finishLogin(entry,account.login,account.id);}
   if(!tabs().some(tab=>providerOf(tab)===account.tool))continue;
   for(const warning of account.warnings||[]){
    const key=warning.key||account.id+':'+warning.kind+':'+(warning.expires_at||warning.resets_at);if(warningSeen.has(key))continue;warningSeen.add(key);
    const alternative=accounts.find(other=>other.tool===account.tool&&other.id!==account.id&&other.logged_in),actions=[];
    const renewal=['login_expiring','auth_expired'].includes(warning.kind),requiresIdle=!!(account.renew_when_idle||account.renew_requires_idle||warning.renew_requires_idle);
    if(renewal&&!account.renewal_pending)actions.push({title:requiresIdle?'Renew when idle':'Renew sign-in now',action:'renew'});
    else if(warning.kind==='usage_high'&&alternative)actions.push({title:'Use '+(alternative.email||alternative.label||alternative.id)+' for new chats',action:'default',account:alternative.id});
    actions.push({title:'Add an account',action:'add'});
    const date=warning.expires_at||warning.resets_at,when=date?new Date(date):null;
    const renewalNote=renewal&&account.renewal_pending?' Renewal is queued until chats on this account are idle.':renewal&&requiresIdle&&!String(warning.message||'').includes('not verified safe')?' Sign-in will wait until chats are idle. Signing in during a running turn is not verified safe.':'';
    const message=warning.message+renewalNote+(when&&!Number.isNaN(when.valueOf())?' '+(warning.expires_at?'Expires ':'Resets ')+when.toLocaleString()+'.':'');
    const selected=await vscode.window.showInformationMessage(message,...actions),action=typeof selected==='string'?actions.find(item=>item.title===selected):selected;
    if(!action||disposed)continue;
    const entry=[...entries.values()].find(item=>item.account===account.id)||{provider:account.tool,account:account.id,resource:{}};
    if(action.action==='renew')await signIn(entry,{account_id:account.id},true);
    else if(action.action==='add')await vscode.commands.executeCommand('library.accounts');
    else if(action.action==='default'){
     await api('/api/accounts/default',{tool:account.tool,id:action.account});
     if(account.tool==='codex')await codexAccountWindow(action.account);
     else void vscode.window.showInformationMessage('New Claude chats will use the selected account. Existing chats keep their account.');
    }
   }
  }
 }
 async function notify(entry,result){
  const failure=result.failure;if(!failure||!['auth_expired','not_logged_in','limit_reached','rate_limited'].includes(failure.kind))return;
  const marker=entry.resource.key+':'+(result.marker||failure.kind+':'+failure.raw);if(seen.has(marker))return;seen.add(marker);
  let accounts=[];try{accounts=(await helper(['accounts','--provider',entry.provider])).accounts||[];}catch{}
  const account=failure.account_id||entry.account,current=accounts.find(a=>a.id===account),alternative=accounts.find(a=>a.id!==account&&a.logged_in===true);
  const other=entry.provider==='claude'?'codex':'claude',actions=[];
  if(['auth_expired','not_logged_in'].includes(failure.kind))actions.push({title:'Sign in again',action:'login'});
  if(failure.kind==='limit_reached'&&alternative)actions.push({title:'Continue on '+(alternative.email||alternative.label||alternative.id),action:'account',account:alternative.id});
  actions.push({title:'Move to '+(other==='codex'?'Codex':'Claude'),action:'handoff'},{title:'Add an account',action:'add'});
  const selected=await vscode.window.showInformationMessage(text({...failure,provider:entry.provider},current?.email||current?.label),...actions);
  const action=typeof selected==='string'?actions.find(item=>item.title===selected):selected;if(!action||disposed)return;
  try{
   if(action.action==='login')await signIn(entry,{...failure,provider:entry.provider,account_id:account});
   else if(action.action==='account'){
    if(entry.provider==='claude')await switchAccount(entry.resource,action.account,{recovery:true});
    else await codexAccountWindow(action.account,entry.resource);
   }else if(action.action==='add')await vscode.commands.executeCommand('library.accounts');
   else if(action.action==='handoff'){
    const response=await api('/api/run/recover',{key:entry.resource.key,action:'handoff',provider:other});
    const run=response.run;if(!run?.id)throw Error('The recovery service did not return a handoff run.');
    await openLibrary({kind:'chat',key:run.conversation_key||run.key,runId:run.id,title:'Continued conversation',meta:{source:other}},entry.column);
   }
  }catch(error){void vscode.window.showErrorMessage(error.message||String(error));}
 }
 async function check(entry){
  if(disposed||busy.has(entry.resource.key))return busy.get(entry.resource.key);
  const work=(async()=>{const args=['classify','--provider',entry.provider,'--transcript',entry.transcript.file];if(entry.account)args.push('--account',entry.account);const result=await helper(args);void notify(entry,result).catch(error=>vscode.window.showErrorMessage(error.message||String(error)));})();
  busy.set(entry.resource.key,work);try{await work;}finally{busy.delete(entry.resource.key);}
 }
 function schedule(entry){const key=entry.resource.key;clearTimeout(timers.get(key));timers.set(key,setTimeout(()=>{timers.delete(key);void check(entry).catch(()=>{});},options.debounceMs??150));}
 function track(tab,entry){
  if(disposed)return;
  entry={...entry,provider:entry.target?.provider||entry.provider||entry.resource.key.split('-')[0],tab};
  const previous=entries.get(entry.resource.key);if(previous?.watch)fs.unwatchFile(previous.transcript.file,previous.watch);
  entry.watch=()=>schedule(entry);entries.set(entry.resource.key,entry);
  if(!options.noWatch)fs.watchFile(entry.transcript.file,{persistent:false,interval:1000},entry.watch);
  void check(entry).catch(()=>{});return entry;
 }
 function forget(tab){for(const [key,entry] of entries)if(entry.tab===tab){fs.unwatchFile(entry.transcript.file,entry.watch);clearTimeout(timers.get(key));timers.delete(key);entries.delete(key);}}
 async function pollIdentities(){
  const identities=await vscode.commands.executeCommand('_library.chatTabIdentities');
  if(!Array.isArray(identities))return;
  for(const identity of identities){
   if(!/^(claude|codex)-[A-Za-z0-9_-]{1,200}$/.test(identity.key||''))continue;
   const group=vscode.window.tabGroups.all.find(group=>group.viewColumn===identity.viewColumn),tab=group?.tabs[identity.editorIndex];
   if(!tab||providerOf(tab)!==identity.provider)continue;
   if(options.onIdentity)await options.onIdentity(tab,identity);
  }
 }
 async function observe(tab){
  if(providerOf(tab)!=='codex')return;
  const id=tab.input?.uri?.path?.match(/^\/local\/([A-Za-z0-9_-]+)$/)?.[1];if(!id)return;
  const transcript=await findTranscript('codex',id);if(!transcript)return;
  track(tab,{resource:{kind:'chat',key:'codex-'+id},target:{provider:'codex',id},transcript,column:tab.group?.viewColumn});
 }
 async function changed(provider,file){
  if(disposed||!file.endsWith('.jsonl')||!tabs().some(tab=>providerOf(tab)===provider))return;
  const stat=await fs.promises.lstat(file).catch(()=>null);if(!stat?.isFile()||stat.isSymbolicLink())return;
  for(const entry of entries.values())if(entry.transcript.file===file){schedule(entry);return;}
  let id,tab;
  if(provider==='claude'){
   id=path.basename(file,'.jsonl');if(!await ownClaude(id))return;
   // A PID ancestry match establishes window ownership, not a specific tab. Never guess a tab to close.
  }else{
   // Codex's new-panel route does not expose a stable thread ID. A focused tab is
   // not sufficient evidence: another window or panel may have created this rollout.
   // Saved /local/<id> tabs are bound by the read-only native identity command.
   return;
  }
  if(!id||!/^[A-Za-z0-9_-]{1,200}$/.test(id))return;
  const transcript={file};track(tab,{resource:{kind:'chat',key:provider+'-'+id},provider,target:{provider,id},transcript,column:tab?.group?.viewColumn});
 }
 if(!options.noWatch){
  for(const [provider,root] of [['claude',path.join(claudeRoot,'projects')],['codex',path.join(codexRoot,'sessions')]]){
   try{const watcher=fs.watch(root,{recursive:true,persistent:false},(_event,name)=>{if(name)void changed(provider,path.join(root,String(name))).catch(()=>{});});watcher.on('error',()=>{});watchers.push(watcher);}catch{}
  }
  for(const tab of tabs())void observe(tab).catch(()=>{});
  const identityTimer=setInterval(()=>{void pollIdentities().catch(()=>{});},1000);identityTimer.unref?.();watchers.push({dispose:()=>clearInterval(identityTimer)});void pollIdentities().catch(()=>{});
  const warningTimer=setInterval(()=>{void checkWarnings().catch(()=>{});},60000);warningTimer.unref?.();watchers.push({dispose:()=>clearInterval(warningTimer)});void checkWarnings().catch(()=>{});
  if(vscode.window.tabGroups.onDidChangeTabs)watchers.push(vscode.window.tabGroups.onDidChangeTabs(event=>{for(const tab of event.closed||[])forget(tab);for(const tab of event.opened||[])void observe(tab).catch(()=>{});}));
 }
 const dispose=()=>{disposed=true;for(const watcher of watchers){watcher.close?.();watcher.dispose?.();}for(const entry of entries.values())fs.unwatchFile(entry.transcript.file,entry.watch);for(const timer of timers.values())clearTimeout(timer);entries.clear();};
 context.subscriptions.push({dispose});
 return {track,forget,check,changed,observe,pollIdentities,checkWarnings,dispose};
}
module.exports={create,text,providerOf};
