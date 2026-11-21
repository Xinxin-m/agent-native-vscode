'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const run=require('node:util').promisify(require('node:child_process').execFile),{official}=require('../agent-tabs.cjs');
const sid='11111111-2222-4333-8444-555555555551';
test('Codex account continuation prepares unique background profile with a validated one-shot session request',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-account-window-')),shared=path.join(root,'shared'),profile=path.join(root,'account'),registry=path.join(root,'accounts.json');
 fs.mkdirSync(shared);const source=path.join(root,'source');fs.mkdirSync(path.join(source,'sessions'),{recursive:true});fs.mkdirSync(profile);fs.symlinkSync(path.join(source,'sessions'),path.join(profile,'sessions'));fs.writeFileSync(path.join(profile,'sessions','rollout-x-'+sid+'.jsonl'),'{}\n');
 fs.writeFileSync(registry,JSON.stringify({accounts:[{id:'codex-second',tool:'codex',config_dir:profile}],state:{active:{codex:'codex-second'},global_active:{codex:'codex-default'}}}));
 const helper=path.resolve(__dirname,'../../official-sessions.py'),args=[helper,'--shared',shared,'codex-window','--account','codex-second','--app','/tmp/Fixture.app','--user-data-dir',path.join(root,'current'),'--extensions-dir',path.join(root,'extensions'),'--session',sid];
 try{
  const env={...process.env,LIBRARY_ACCOUNTS_FILE:registry,LIBRARY_OFFICIAL_SHARED:shared,CODEX_HOME:source};
  const first=JSON.parse((await run('python3',args,{env})).stdout),second=JSON.parse((await run('python3',args,{env})).stdout);
  assert.ok(first.argv.includes('LIBRARY_OFFICIAL_OPEN_SESSION=codex-'+sid));const nonce=first.argv.find(value=>value.startsWith('LIBRARY_OFFICIAL_OPEN_REQUEST=')).split('=')[1];assert.match(nonce,/^[a-f0-9]{32}$/);
  assert.ok(first.user_data_dir.endsWith('/requests/'+nonce));assert.notEqual(first.user_data_dir,second.user_data_dir);assert.deepEqual(first.argv.slice(0,3),['open','-g','-n']);
  await assert.rejects(run('python3',[...args.slice(0,-1),'../bad'],{env}),/Invalid Codex session identifier/);
  const independent=path.join(root,'independent');fs.mkdirSync(path.join(independent,'sessions'),{recursive:true});fs.writeFileSync(path.join(independent,'sessions','rollout-x-'+sid+'.jsonl'),'{}\n');
  await assert.rejects(run('python3',args,{env:{...env,CODEX_HOME:independent}}),/same shared Codex session store/);
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('requested account window opens the exact saved Codex thread once and does not replay it after reload',async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'codex-request-receiver-')),root=path.join(home,'sessions');fs.mkdirSync(root);fs.writeFileSync(path.join(root,'rollout-x-'+sid+'.jsonl'),JSON.stringify({type:'session_meta',payload:{id:sid,cwd:home}})+'\n');
 const saved=['CODEX_HOME','LIBRARY_OFFICIAL_OPEN_SESSION','LIBRARY_OFFICIAL_OPEN_REQUEST'].map(key=>process.env[key]);process.env.CODEX_HOME=home;process.env.LIBRARY_OFFICIAL_OPEN_SESSION='codex-'+sid;process.env.LIBRARY_OFFICIAL_OPEN_REQUEST='a'.repeat(32);
 const calls=[],values={},group={viewColumn:2,tabs:[]};
 const vscode={workspace:{workspaceFolders:[{uri:{scheme:'file',fsPath:home}}],getConfiguration:()=>({get:()=> 'official'})},ViewColumn:{Active:-1},Uri:{parse:s=>({scheme:'openai-codex',path:'/local/'+sid,toString:()=>s})},
  window:{tabGroups:{all:[group],activeTabGroup:group},showInformationMessage:()=>{},showErrorMessage:message=>assert.fail(message)},extensions:{getExtension:()=>({activate:async()=>{}})},commands:{executeCommand:async(...args)=>{calls.push(args);if(args[0]==='vscode.openWith')group.tabs.push({input:{viewType:'chatgpt.conversationEditor',uri:args[1]},group});}}};
 const context={subscriptions:[],globalState:{get:key=>values[key],update:async(key,value)=>{values[key]=value;}}};
 try{
  const options={api:async()=>({sessions:[]}),helper:async()=>({active:false}),noWatch:true};
  await official(vscode,context,()=>assert.fail('No blank chat'),options).ready;
  assert.equal(calls.length,1);assert.equal(calls[0][0],'vscode.openWith');assert.equal(calls[0][1].toString(),'openai-codex://route/local/'+sid);assert.equal(values.officialOpenRequest,'a'.repeat(32));
  await official(vscode,context,()=>assert.fail('No blank chat'),options).ready;assert.equal(calls.length,1);
 }finally{for(const item of context.subscriptions)item.dispose();for(const [index,key] of ['CODEX_HOME','LIBRARY_OFFICIAL_OPEN_SESSION','LIBRARY_OFFICIAL_OPEN_REQUEST'].entries()){if(saved[index]===undefined)delete process.env[key];else process.env[key]=saved[index];}fs.rmSync(home,{recursive:true,force:true});}
});
