'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {promisify}=require('node:util'),run=promisify(require('node:child_process').execFile);
const {create}=require('../official-recovery.cjs');
test('real shared classifier reports an appended official auth failure within two seconds',async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-helper-auth-')),claude=path.join(home,'.claude'),codex=path.join(home,'.codex'),shared=path.join(home,'shared');
 const projects=path.join(claude,'projects','test');fs.mkdirSync(projects,{recursive:true});fs.mkdirSync(path.join(codex,'sessions'),{recursive:true});fs.mkdirSync(shared);
 const file=path.join(projects,'session.jsonl');fs.writeFileSync(file,JSON.stringify({type:'user',sessionId:'session',cwd:home,message:{content:'synthetic failed request'}})+'\n');
 const saved=[process.env.CLAUDE_CONFIG_DIR,process.env.CODEX_HOME];process.env.CLAUDE_CONFIG_DIR=claude;process.env.CODEX_HOME=codex;
 const helperFile=path.resolve(__dirname,'../../official-sessions.py'),notes=[],helper=async args=>JSON.parse((await run('python3',[helperFile,'--shared',shared,...args],{env:{...process.env,LIBRARY_ACCOUNTS_FILE:''},timeout:5000})).stdout);
 const tab={input:{viewType:'claudeVSCodePanel'},group:{viewColumn:1}},group={viewColumn:1,tabs:[tab],activeTab:tab};
 const vscode={window:{state:{focused:false},tabGroups:{all:[group],activeTabGroup:group,onDidChangeTabs:()=>({dispose(){}})},showInformationMessage:async(message,...actions)=>notes.push({message,actions}),showErrorMessage:()=>{}},commands:{executeCommand:async()=>[]}};
 const context={subscriptions:[]},watcher=create({vscode,context,helper,api:async()=>({accounts:[]}),findTranscript:async()=>({file}),openLibrary:async()=>assert.fail('No actions'),switchAccount:async()=>assert.fail('No actions'),codexAccountWindow:async()=>assert.fail('No actions'),options:{home}});
 const entry={resource:{kind:'chat',key:'claude-session'},target:{provider:'claude',id:'session'},provider:'claude',account:'claude-default',transcript:{file},column:1};
 try{
  watcher.track(tab,entry);await watcher.check(entry);
  const started=Date.now();fs.appendFileSync(file,JSON.stringify({type:'assistant',sessionId:'session',timestamp:'2026-11-16T08:00:00Z',isApiErrorMessage:true,message:{content:[{type:'text',text:'Failed to authenticate: this login reached its time limit and cannot be renewed. Run `claude auth login`.'}]}})+'\n');
  while(!notes.length&&Date.now()-started<2500)await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(notes.length,1);assert.ok(Date.now()-started<2000,'failure notification must arrive within two seconds');assert.match(notes[0].message,/sign-in expired/);assert.ok(notes[0].actions.some(action=>action.title==='Sign in again'));
  const result=await helper(['classify','--provider','claude','--transcript',file,'--account','claude-default']);assert.equal(result.failure.kind,'auth_expired');assert.equal(result.failure.account_id,'claude-default');assert.equal(result.message,'synthetic failed request');
 }finally{watcher.dispose();for(const [index,key] of ['CLAUDE_CONFIG_DIR','CODEX_HOME'].entries()){if(saved[index]===undefined)delete process.env[key];else process.env[key]=saved[index];}fs.rmSync(home,{recursive:true,force:true});}
});
