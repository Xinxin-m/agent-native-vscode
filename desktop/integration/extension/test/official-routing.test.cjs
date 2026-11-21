'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {official,projectDir,ensureTranscriptLink,removeTranscriptLink,rateLimited}=require('../agent-tabs.cjs');
function fixture(){
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-routing-')),config=path.join(home,'.claude'),a=path.join(home,'A'),b=path.join(home,'B');
 fs.mkdirSync(a);fs.mkdirSync(b);fs.mkdirSync(path.join(config,'projects',projectDir(b)),{recursive:true});
 const transcript=path.join(config,'projects',projectDir(b),'session.jsonl');fs.writeFileSync(transcript,JSON.stringify({type:'user',cwd:b})+'\n');
 const saved=process.env.CLAUDE_CONFIG_DIR;process.env.CLAUDE_CONFIG_DIR=config;
 const state={calls:[],messages:[],helperCalls:[],live:[],account:'second',closed:[],launches:[]},group={viewColumn:2,tabs:[]};const listeners=new Set();
 const vscode={workspace:{isTrusted:true,workspaceFolders:[{uri:{scheme:'file',fsPath:a}}],getConfiguration:()=>({get:()=> 'official'})},ViewColumn:{Active:-1},Uri:{parse:s=>({toString:()=>s})},env:{appRoot:'/tmp/Test.app/Contents/Resources/app'},
  extensions:{getExtension:()=>({activate:async()=>{}})},
  window:{tabGroups:{activeTabGroup:group,all:[group],onDidChangeTabs:fn=>{listeners.add(fn);return {dispose(){listeners.delete(fn);}};},close:async tab=>{state.closed.push(tab);group.tabs=group.tabs.filter(t=>t!==tab);state.live=[];for(const listener of listeners)listener({closed:[tab]});return true;}},
   showInformationMessage:async(message,...actions)=>{state.messages.push(message);return state.informationChoice;},showErrorMessage:async message=>state.messages.push(message),showWarningMessage:async()=>{if(state.busyDuringDialog)state.live=[{session_id:'session',status:'busy',alive:true}];return 'Switch account';},showQuickPick:async items=>{if(state.busyDuringDialog)state.live=[{session_id:'session',status:'busy',alive:true}];return items.find(x=>x.account===state.account);}},
  commands:{registerCommand:()=>({dispose(){}}),executeCommand:async(...args)=>{if(args[0]==='_library.chatTabIdentities')return group.tabs.map((tab,index)=>({key:'claude-session',provider:'claude',viewColumn:2,editorIndex:index}));state.calls.push(args);if(args[0]==='claude-vscode.editor.open'){const tab={label:'Session',input:{viewType:'claudeVSCodePanel'}};group.tabs.push(tab);group.activeTab=tab;for(const listener of listeners)listener({opened:[tab]});}}}};
 const context={subscriptions:[],globalState:{get:()=>undefined,update:async()=>{}},globalStorageUri:{fsPath:home}};
 const api=async()=>{if(state.apiError)throw Error('Inventory unavailable');return {sessions:state.live};};
 const helper=async args=>{if(args[0].startsWith('alias-')){const {stdout}=await require('node:util').promisify(require('node:child_process').execFile)('python3',[path.resolve(__dirname,'../../official-sessions.py'),'--shared',path.join(home,'shared'),...args]);return JSON.parse(stdout);}state.helperCalls.push(args);if(state.preflightError&&args.includes('--dry-run'))throw Error('Profile unavailable');if(args[0]==='accounts')return {accounts:[{id:'first',email:'first@example.invalid'},{id:'second',email:'second@example.invalid'}]};if(args[0]==='codex-window')return {argv:['open','-g','-n','-a','/tmp/Test.app','--args','--user-data-dir',path.join(home,'second')]};return {account_id:args.includes('--account')?args[args.indexOf('--account')+1]:'first',config_dir:config};};
 const chats=official(vscode,context,()=>{}, {shared:path.join(home,'shared'),api,helper,noWatch:true,launch:async argv=>state.launches.push(argv)});
 return {home,config,a,b,transcript,state,group,vscode,chats,resource:{kind:'chat',key:'claude-session',meta:{account_id:'first'}},cleanup(){for(const d of context.subscriptions)d.dispose();if(saved===undefined)delete process.env.CLAUDE_CONFIG_DIR;else process.env.CLAUDE_CONFIG_DIR=saved;fs.rmSync(home,{recursive:true,force:true});}};
}
test('cross-folder open records original cwd and creates owned lookup link; tab close removes only its link',async()=>{
 const f=fixture();try{
  assert.equal(await f.chats.open(f.resource),true);
  const link=path.join(f.config,'projects',projectDir(f.a),'session.jsonl');assert.equal(fs.statSync(link).ino,fs.statSync(f.transcript).ino);assert.equal(fs.lstatSync(link).isSymbolicLink(),false);
  const args=f.state.helperCalls[0];assert.equal(args[args.indexOf('--cwd')+1],f.b);assert.equal(args[args.indexOf('--account')+1],'first');
  assert.deepEqual(f.state.calls,[['claude-vscode.editor.open','session',undefined,2]]);
  await f.vscode.window.tabGroups.close(f.group.tabs[0]);for(let i=0;i<20&&fs.existsSync(link);i++)await new Promise(r=>setTimeout(r,5));
  assert.equal(fs.existsSync(link),false);assert.equal(fs.existsSync(f.transcript),true);
 }finally{f.cleanup();}
});
test('link collision preserves the existing file and falls back without opening a provider',async()=>{
 const f=fixture();try{
  const dest=path.join(f.config,'projects',projectDir(f.a));fs.mkdirSync(dest);const file=path.join(dest,'session.jsonl');fs.writeFileSync(file,'existing result');
  // Source scan finds the original B first; pass direct link routine to cover the collision deterministically.
  await assert.rejects(ensureTranscriptLink(f.a,f.config,'session',f.transcript,{shared:path.join(f.home,'shared')}),/different transcript/);assert.equal(fs.readFileSync(file,'utf8'),'existing result');
 }finally{f.cleanup();}
});
test('cleanup never removes a link replaced by someone else',async()=>{
 const f=fixture();try{const link=await ensureTranscriptLink(f.a,f.config,'session',f.transcript,{shared:path.join(f.home,'shared')});fs.unlinkSync(link.file);fs.symlinkSync('/tmp/another-owner',link.file);await assert.rejects(removeTranscriptLink(link),/changed/);assert.equal(fs.readlinkSync(link.file),'/tmp/another-owner');}finally{f.cleanup();}
});
test('known running sessions and unavailable inventory never invoke helper or provider',async()=>{
 const f=fixture();try{
  f.state.live=[{session_id:'session',alive:true,status:'busy'}];assert.equal(await f.chats.open(f.resource),false);
  f.state.live=[];f.state.apiError=true;assert.equal(await f.chats.open(f.resource),false);
  assert.deepEqual(f.state.calls,[]);assert.deepEqual(f.state.helperCalls,[]);assert.match(f.state.messages.join(' '),/already running.*Inventory unavailable/);
 }finally{f.cleanup();}
});
test('explicit account switch closes only the tracked idle tab and reopens with selected account',async()=>{
 const f=fixture();try{
  await f.chats.open(f.resource);const original=f.group.tabs[0];f.state.live=[{session_id:'session',alive:true,status:'idle',where:'vscode'}];
  await f.chats.switchAccount();assert.deepEqual(f.state.closed,[original]);assert.equal(f.group.tabs.length,1);
  const routes=f.state.helperCalls.filter(args=>args[0]==='route'&&!args.includes('--dry-run'));assert.equal(routes.length,2);assert.equal(routes[1][routes[1].indexOf('--account')+1],'second');
  assert.equal(f.state.calls.filter(c=>c[0]==='claude-vscode.editor.open').length,2);
 }finally{f.cleanup();}
});
test('account switch refuses a busy session and never closes an untracked tab',async()=>{
 const f=fixture();try{
  await f.chats.open(f.resource);f.state.live=[{session_id:'session',status:'busy',alive:true}];await f.chats.switchAccount();assert.deepEqual(f.state.closed,[]);
  f.group.activeTab={label:'Session',input:{viewType:'claudeVSCodePanel'}};await f.chats.switchAccount();assert.deepEqual(f.state.closed,[]);
 }finally{f.cleanup();}
});
test('explicit Codex account action uses prepared background launcher, without resuming a thread',async()=>{
 const f=fixture();try{await f.chats.codexAccountWindow();assert.equal(f.state.launches.length,1);assert.deepEqual(f.state.helperCalls.map(c=>c[0]),['accounts','codex-window']);assert.equal(f.state.calls.length,0);assert.ok(f.state.launches[0].includes('-g'));}finally{f.cleanup();}
});
test('only structured provider errors identify account limits',()=>{
 assert.equal(rateLimited({type:'user',message:{content:'I reached a rate limit yesterday'}}),false);
 assert.equal(rateLimited({type:'assistant',error:'rate_limit'}),true);
 assert.equal(rateLimited({type:'system',subtype:'api_error',error:{status:429}}),true);
});

test('account switch rechecks live status after dialogs and leaves a newly busy tab untouched',async()=>{
 const f=fixture();try{await f.chats.open(f.resource);f.state.busyDuringDialog=true;await f.chats.switchAccount();assert.deepEqual(f.state.closed,[]);assert.equal(f.group.tabs.length,1);}finally{f.cleanup();}
});
test('account switch preflights chosen profile before closing its current tab',async()=>{
 const f=fixture();try{await f.chats.open(f.resource);f.state.preflightError=true;await f.chats.switchAccount();assert.deepEqual(f.state.closed,[]);assert.equal(f.group.tabs.length,1);assert.match(f.state.messages.join(' '),/Profile unavailable/);}finally{f.cleanup();}
});

test('restart transcript lookup selects canonical source instead of owned hardlink alias',async()=>{
 const f=fixture();try{
  const shared=path.join(f.home,'shared'),link=await ensureTranscriptLink(f.a,f.config,'session',f.transcript,{shared});
  const {findTranscript}=require('../agent-tabs.cjs');
  const found=await findTranscript('claude','session',f.home,shared);
  assert.equal(fs.realpathSync(found.file),fs.realpathSync(f.transcript));
  const manifest=JSON.parse(fs.readFileSync(path.join(shared,'session-aliases.json')));assert.equal(Object.keys(manifest.aliases).length,1);
  await removeTranscriptLink(link);
 }finally{f.cleanup();}
});
test('restart lookup refuses atomic canonical replacement and preserves stale alias',async()=>{
 const f=fixture();try{
  const shared=path.join(f.home,'shared'),link=await ensureTranscriptLink(f.a,f.config,'session',f.transcript,{shared});
  const next=path.join(f.home,'replacement');fs.writeFileSync(next,JSON.stringify({type:'user',cwd:f.b})+'\n');fs.renameSync(next,f.transcript);
  await assert.rejects(require('../agent-tabs.cjs').findTranscript('claude','session',f.home,shared),/changed or is missing/);
  assert.ok(fs.existsSync(link.file));assert.notEqual(fs.statSync(link.file).ino,fs.statSync(f.transcript).ino);
 }finally{f.cleanup();}
});
