'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {create}=require('../official-recovery.cjs');
function fixture(provider='claude'){
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-auth-')),file=path.join(home,'session.jsonl');fs.writeFileSync(file,'original transcript\n');
 const tab={input:{viewType:provider==='claude'?'claudeVSCodePanel':'chatgpt.conversationEditor',uri:{path:'/extension/panel/new'}},group:{viewColumn:2}};
 const state={notes:[],api:[],opened:[],switched:[],codexWindows:[],commands:[],library:[],failure:{kind:'auth_expired',provider,account_id:'first',raw:'fixture'},marker:'one',selection:undefined};
 const vscode={window:{state:{focused:true},tabGroups:{all:[{tabs:[tab],viewColumn:2}],activeTabGroup:{activeTab:tab}},showInformationMessage:async(message,...actions)=>{state.notes.push({message,actions});return actions.find(a=>a.action===state.selection);},showErrorMessage:message=>state.notes.push({error:message}),showInputBox:async()=> 'fixture-code',showWarningMessage:()=>assert.fail('No second confirmation')},Uri:{parse:s=>s},env:{openExternal:async url=>state.opened.push(url)},commands:{executeCommand:async(...args)=>state.commands.push(args)}};
 const context={subscriptions:[]};
 const helper=async args=>args[0]==='classify'?(state.classifications=(state.classifications||0)+1,{failure:state.failure,marker:state.marker,message:'failed prompt',through_turn:2,session_id:'session'}):{accounts:state.helperAccounts||[{id:'first',email:'first@example.invalid',logged_in:true},{id:'second',email:'second@example.invalid',logged_in:true}]};
 const api=async(route,body)=>{state.api.push({route,body});if(route==='/api/notices'){
  if(!body)return {settings:{duration_seconds:state.seconds??3}};
  assert.equal(body.action,'collect');
  if(state.collectError){state.collectError=false;throw Error('Transient collection failure');}
  return {ok:true};
 }if(route==='/api/accounts')return {accounts:state.accounts||[]};if(route==='/api/accounts/verify')return {accounts:state.helperAccounts||[{id:'first',email:'first@example.invalid',logged_in:true}]};if(body.action==='login'||route==='/api/accounts/login')return {login:{login_id:'login',url:'https://example.invalid/login',needs_code:true},account_id:'first'};if(body.action==='handoff')return {run:{id:'new-run',conversation_key:'codex-new'}};return {ok:true};};
 const watcher=create({vscode,context,helper,api,findTranscript:async()=>({file}),openLibrary:async(...args)=>state.library.push(args),switchAccount:async(...args)=>state.switched.push(args),codexAccountWindow:async(...args)=>state.codexWindows.push(args),options:{home,noWatch:true,ownsClaude:async()=>true,openBrowser:async url=>state.opened.push(url)}});
 const entry={provider,resource:{kind:'chat',key:provider+'-session'},transcript:{file},account:'first',column:2};
 return {home,file,tab,state,vscode,watcher,entry,cleanup(){watcher.dispose();fs.rmSync(home,{recursive:true,force:true});}};
}
test('official sign-in notification opens browser/code flow without changing transcript or draft',async()=>{
 const f=fixture();try{f.state.selection='login';f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.deepEqual(f.state.opened,['https://example.invalid/login']);assert.deepEqual(f.state.api.filter(x=>x.route!=='/api/notices'),[{route:'/api/run/recover',body:{key:'claude-session',action:'login',account_id:'first',when_idle:true,open_browser:true}},{route:'/api/accounts/login/code',body:{id:'first',login_id:'login',code:'fixture-code'}},{route:'/api/accounts/verify',body:{id:'first'}}]);assert.equal(fs.readFileSync(f.file,'utf8'),'original transcript\n');assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});
test('one click continues Claude on the named account with no confirmation or provider send',async()=>{
 const f=fixture();try{f.state.failure.kind='limit_reached';f.state.selection='account';f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.equal(f.state.switched.length,1);assert.deepEqual(f.state.switched[0],[f.entry.resource,'second',{recovery:true}]);assert.deepEqual(f.state.api.filter(x=>x.route!=='/api/notices'),[]);assert.deepEqual(f.state.commands,[]);}finally{f.cleanup();}
});
test('Codex continuation requests a distinct account window instead of changing current app-server',async()=>{
 const f=fixture('codex');try{f.state.failure.kind='limit_reached';f.state.selection='account';f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.deepEqual(f.state.codexWindows,[['second',f.entry.resource]]);assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});
test('handoff delegates clean-history boundary to recovery endpoint and opens returned Library run',async()=>{
 const f=fixture();try{f.state.selection='handoff';f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.deepEqual(f.state.api.filter(x=>x.route!=='/api/notices'),[{route:'/api/run/recover',body:{key:'claude-session',action:'handoff',provider:'codex'}}]);assert.equal(f.state.library[0][0].runId,'new-run');assert.equal(f.state.library[0][1],2);}finally{f.cleanup();}
});
test('tracking and observing Official failures collect silently and deduplicate replay',async()=>{
 const f=fixture();
 try{
  f.watcher.track(f.tab,f.entry);
  await f.watcher.check(f.entry);
  await f.watcher.check(f.entry);
  assert.deepEqual(f.state.api,[{route:'/api/notices',body:{action:'collect',failure:{...f.state.failure,chat_key:f.entry.resource.key,official:true}}}]);
  assert.deepEqual(f.state.notes,[]);
  f.state.marker='newer';
  await f.watcher.check(f.entry);
  assert.equal(f.state.api.length,2);
  assert.deepEqual(f.state.notes,[]);
 }finally{f.cleanup();}
});
test('native Claude error detection collects without guessing a tab to close',async()=>{
 const f=fixture();
 try{
  await f.watcher.changed('claude',f.file);
  for(let i=0;i<20&&!f.state.api.length;i++)await new Promise(r=>setTimeout(r,5));
  assert.equal(f.state.api[0].body.action,'collect');
  assert.deepEqual(f.state.notes,[]);
  assert.deepEqual(f.state.switched,[]);
 }finally{f.cleanup();}
});
test('an unattributed new Codex rollout is never assigned to a merely focused tab',async()=>{
 const f=fixture('codex');try{fs.writeFileSync(f.file,JSON.stringify({type:'session_meta',payload:{id:'session',originator:'codex_vscode',source:'vscode'}})+'\n');await f.watcher.changed('codex',f.file);assert.equal(f.state.notes.length,0);}finally{f.cleanup();}
});
test('Add an account opens the existing Accounts flow',async()=>{
 const f=fixture();try{f.state.selection='add';f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.deepEqual(f.state.commands,[['library.accounts']]);}finally{f.cleanup();}
});

test('Official account polling stays silent and skips requests with no user-initiated login',async()=>{
 const f=fixture();try{
  f.state.accounts=[{id:'first',tool:'claude',warnings:[{kind:'login_expiring',key:'expiry',message:'Expires soon'}]}];
  await f.watcher.checkWarnings();
  assert.deepEqual(f.state.api,[]);
  assert.deepEqual(f.state.notes,[]);
 }finally{f.cleanup();}
});
test('unknown login state is never offered as a usable recovery account',async()=>{
 const f=fixture();try{f.state.failure.kind='limit_reached';f.state.helperAccounts=[{id:'first',logged_in:true},{id:'unknown'}];f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);assert.equal(f.state.notes[0].actions.some(action=>action.action==='account'),false);}finally{f.cleanup();}
});
test('configured native prompt stays available when action fails, and its X only closes the prompt',async()=>{
 const f=fixture(), callbacks=new Map(), prompts=[];
 try{
  f.state.seconds=0;
  f.vscode.commands.getCommands=async()=>['_library.showAccountNotice'];
  f.vscode.commands.registerCommand=(name,fn)=>{callbacks.set(name,fn);return {dispose:()=>callbacks.delete(name)}};
  f.vscode.commands.executeCommand=async(name,input)=>{if(name==='_library.showAccountNotice')prompts.push(input);else throw Error('Fixture unavailable')};
  f.watcher.track(f.tab,f.entry);await f.watcher.recover(f.entry.resource.key);
  assert.equal(prompts.length,1);assert.equal(prompts[0].seconds,0);
  const callback=callbacks.get(prompts[0].callback);
  assert.equal(await callback(prompts[0].actions.indexOf('Add an account')),false);
  assert.equal(f.state.api.some(x=>x.body?.action==='dismiss'),false);
  await callback(-1);
  assert.equal(callbacks.size,0);
  assert.ok(f.state.api.every(x=>!['archive','dismiss'].includes(x.body?.action)));
 }finally{f.cleanup();}
});

test('a transient collection failure retries silently',async()=>{
 const f=fixture();
 try{
  f.state.collectError=true;
  await f.watcher.check(f.entry);
  await f.watcher.check(f.entry);
  assert.deepEqual(f.state.notes,[]);
  assert.equal(f.state.api.length,2);
  assert.deepEqual(f.state.api[0],f.state.api[1]);
 }finally{f.cleanup();}
});
