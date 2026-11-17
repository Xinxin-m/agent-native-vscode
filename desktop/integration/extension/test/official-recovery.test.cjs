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
 const api=async(route,body)=>{state.api.push({route,body});if(route==='/api/accounts')return {accounts:state.accounts||[]};if(route==='/api/accounts/verify')return {accounts:state.helperAccounts||[{id:'first',email:'first@example.invalid',logged_in:true}]};if(body.action==='login'||route==='/api/accounts/login')return {login:{login_id:'login',url:'https://example.invalid/login',needs_code:true},account_id:'first'};if(body.action==='handoff')return {run:{id:'new-run',conversation_key:'codex-new'}};return {ok:true};};
 const watcher=create({vscode,context,helper,api,findTranscript:async()=>({file}),openLibrary:async(...args)=>state.library.push(args),switchAccount:async(...args)=>state.switched.push(args),codexAccountWindow:async(...args)=>state.codexWindows.push(args),options:{home,noWatch:true,ownsClaude:async()=>true,openBrowser:async url=>state.opened.push(url)}});
 const entry={provider,resource:{kind:'chat',key:provider+'-session'},transcript:{file},account:'first',column:2};
 return {home,file,tab,state,vscode,watcher,entry,cleanup(){watcher.dispose();fs.rmSync(home,{recursive:true,force:true});}};
}
test('official sign-in notification opens browser/code flow without changing transcript or draft',async()=>{
 const f=fixture();try{f.state.selection='login';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.state.opened,['https://example.invalid/login']);assert.deepEqual(f.state.api,[{route:'/api/run/recover',body:{key:'claude-session',action:'login',account_id:'first',when_idle:true,open_browser:true}},{route:'/api/accounts/login/code',body:{id:'first',login_id:'login',code:'fixture-code'}},{route:'/api/accounts/verify',body:{id:'first'}}]);assert.equal(fs.readFileSync(f.file,'utf8'),'original transcript\n');assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});
test('one click continues Claude on the named account with no confirmation or provider send',async()=>{
 const f=fixture();try{f.state.failure.kind='limit_reached';f.state.selection='account';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.equal(f.state.switched.length,1);assert.deepEqual(f.state.switched[0],[f.entry.resource,'second',{recovery:true}]);assert.deepEqual(f.state.api,[]);assert.deepEqual(f.state.commands,[]);}finally{f.cleanup();}
});
test('Codex continuation requests a distinct account window instead of changing current app-server',async()=>{
 const f=fixture('codex');try{f.state.failure.kind='limit_reached';f.state.selection='account';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.state.codexWindows,[['second',f.entry.resource]]);assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});
test('handoff delegates clean-history boundary to recovery endpoint and opens returned Library run',async()=>{
 const f=fixture();try{f.state.selection='handoff';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.state.api,[{route:'/api/run/recover',body:{key:'claude-session',action:'handoff',provider:'codex'}}]);assert.equal(f.state.library[0][0].runId,'new-run');assert.equal(f.state.library[0][1],2);}finally{f.cleanup();}
});
test('the same failure is notified once; later failure gets its own notification',async()=>{
 const f=fixture();try{await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.equal(f.state.notes.length,1);f.state.marker='two';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.equal(f.state.notes.length,2);f.state.failure.kind='other';f.state.marker='three';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.equal(f.state.notes.length,2);}finally{f.cleanup();}
});
test('native Claude error owned by this extension host notifies without guessing a tab to close',async()=>{
 const f=fixture();try{await f.watcher.changed('claude',f.file);for(let i=0;i<20&&!f.state.notes.length;i++)await new Promise(r=>setTimeout(r,5));assert.equal(f.state.notes.length,1);assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});
test('an unattributed new Codex rollout is never assigned to a merely focused tab',async()=>{
 const f=fixture('codex');try{fs.writeFileSync(f.file,JSON.stringify({type:'session_meta',payload:{id:'session',originator:'codex_vscode',source:'vscode'}})+'\n');await f.watcher.changed('codex',f.file);assert.equal(f.state.notes.length,0);}finally{f.cleanup();}
});
test('Add an account opens the existing Accounts flow',async()=>{
 const f=fixture();try{f.state.selection='add';await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.state.commands,[['library.accounts']]);}finally{f.cleanup();}
});

test('prevention warning renews through the same browser flow with safe idle scheduling',async()=>{
 const f=fixture();try{f.state.accounts=[{id:'first',tool:'claude',warnings:[{kind:'login_expiring',key:'expiry',message:'Expires soon'}]}];f.state.selection='renew';await f.watcher.checkWarnings();assert.deepEqual(f.state.api[1],{route:'/api/accounts/login',body:{id:'first',when_idle:true,open_browser:true}});assert.equal(f.state.opened.length,1);}finally{f.cleanup();}
});
test('usage warning changes only the default for future Claude chats',async()=>{
 const f=fixture();try{f.state.accounts=[{id:'first',tool:'claude',warnings:[{kind:'usage_high',key:'90',message:'90% used'}]},{id:'second',tool:'claude',logged_in:true,email:'second@example.invalid'}];f.state.selection='default';await f.watcher.checkWarnings();assert.deepEqual(f.state.api[1],{route:'/api/accounts/default',body:{tool:'claude',id:'second'}});assert.deepEqual(f.state.switched,[]);}finally{f.cleanup();}
});

test('an undismissed notification never blocks classification of a later error',async()=>{
 const f=fixture();let release;
 f.vscode.window.showInformationMessage=async(message,...actions)=>{f.state.notes.push({message,actions});if(f.state.notes.length===1)return new Promise(resolve=>{release=resolve;});};
 const first=f.watcher.check(f.entry);
 try{await new Promise(resolve=>setTimeout(resolve,10));f.state.marker='newer';void f.watcher.check(f.entry);await new Promise(resolve=>setTimeout(resolve,10));assert.equal(f.state.classifications,2);}
 finally{release?.();await first;f.cleanup();}
});

test('native warnings label conservative renewal as Renew when idle and explain its safety',async()=>{
 for(const flag of ['account','warning']){const f=fixture();try{
  const warning={kind:'login_expiring',key:'expiry',message:'Expires soon'},account={id:'first',tool:'claude',warnings:[warning]};(flag==='account'?account:warning).renew_requires_idle=true;f.state.accounts=[account];
  await f.watcher.checkWarnings();assert.equal(f.state.notes[0].actions[0].title,'Renew when idle');assert.match(f.state.notes[0].message,/running turn.*not verified safe/);
 }finally{f.cleanup();}}
});
test('queued native renewal is reported without offering another immediate renewal',async()=>{
 const f=fixture();try{f.state.accounts=[{id:'first',tool:'claude',renewal_pending:true,warnings:[{kind:'login_expiring',key:'queued',message:'Expires soon',renew_requires_idle:true}]}];await f.watcher.checkWarnings();assert.match(f.state.notes[0].message,/Renewal is queued/);assert.ok(!f.state.notes[0].actions.some(action=>action.action==='renew'));assert.equal(f.state.api.length,1);}finally{f.cleanup();}
});

test('unknown login state is never offered as a usable recovery account',async()=>{
 const f=fixture();try{f.state.failure.kind='limit_reached';f.state.helperAccounts=[{id:'first',logged_in:true},{id:'unknown'}];await f.watcher.check(f.entry);await new Promise(resolve=>setImmediate(resolve));assert.equal(f.state.notes[0].actions.some(action=>action.action==='account'),false);}finally{f.cleanup();}
});
