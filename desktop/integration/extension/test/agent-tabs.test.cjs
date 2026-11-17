const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {newChatIndex,create,isPanelThread,markCodexTurn,watchCodexTurns}=require('../agent-tabs.cjs');
const codex=(label='Codex',path='/extension/panel/new')=>({label,input:{viewType:'chatgpt.conversationEditor',uri:{path}}});
const claude=(label='Claude Code')=>({label,input:{viewType:'mainThreadWebview-claudeVSCodePanel'}});
const library=label=>({label,input:{viewType:'mainThreadWebview-library.conversation'}});
const file={label:'notes.md',input:{uri:{path:'/notes.md'}}};

test('an unused chat of the same provider in the pane is reused',()=>{
 const group={tabs:[file,library('Old question'),claude(),codex(),library('New chat')]};
 assert.deepEqual(['codex','claude','library'].map(p=>newChatIndex(group,p)),[3,2,4]);
});
test('other providers, chats with messages and other panes do not block a new chat',()=>{
 const group={tabs:[file,claude('Fix the parser'),codex('Codex','/local/thread-1'),library('Settings'),library('Old question')]};
 assert.deepEqual(['codex','claude','library'].map(p=>newChatIndex(group,p)),[-1,-1,-1]);
 assert.equal(newChatIndex({tabs:[claude()]},'codex'),-1);
 assert.equal(newChatIndex(undefined,'claude'),-1);
});
test('a button press focuses the unused chat instead of opening another, once per burst',async()=>{
 const calls=[];const group={viewColumn:2,tabs:[file]};
 const vscode={window:{tabGroups:{activeTabGroup:group}},ViewColumn:{Active:-1},Uri:{parse:s=>s.replace(/\?pane=[^?]+$/,'?pane=N')},
  extensions:{getExtension:id=>({id,activate:async()=>{}})},
  commands:{executeCommand:async(...args)=>{calls.push(args);if(args[0]==='vscode.openWith')group.tabs.push(codex());}}};
 const openAgentChat=create(vscode,()=>assert.fail('Library fallback is unused when the extension exists'));
 // The Codex button reveals its container, then focuses its view: both requests arrive together.
 await Promise.all([openAgentChat('workbench.view.extension.codexSecondaryViewContainer'),openAgentChat('codex')]);
 await openAgentChat('codex');
 await openAgentChat('workbench.view.extension.claude-sidebar-secondary');
 await openAgentChat('unknown-container');
 assert.deepEqual(calls,[
  ['vscode.openWith','openai-codex://route/extension/panel/new?pane=N','chatgpt.conversationEditor',{viewColumn:2,preview:false}],
  ['workbench.action.openEditorAtIndex',1],
  ['claude-vscode.editor.open',undefined,undefined,2],
 ]);
});
test('without the official extension the button opens a Library chat',async()=>{
 let opened=0;
 const vscode={window:{tabGroups:{activeTabGroup:{tabs:[]}}},extensions:{getExtension:()=>undefined},commands:{executeCommand:async()=>assert.fail('No command expected')}};
 await create(vscode,()=>{opened++;})('codex');
 assert.equal(opened,1);
});
test('a Codex tab stops counting as new once its first message writes a rollout',async()=>{
 const used={label:'Codex',input:{viewType:'chatgpt.conversationEditor',uri:{path:'/extension/panel/new',query:'pane=1'}}};
 const fresh={label:'Codex',input:{viewType:'chatgpt.conversationEditor',uri:{path:'/extension/panel/new',query:'pane=2'}}};
 const group={tabs:[used,fresh],activeTab:used},vscode={window:{state:{focused:true},tabGroups:{activeTabGroup:group}}};
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-sessions-'));fs.mkdirSync(path.join(root,'2026/11/15'),{recursive:true});
 const watcher=watchCodexTurns(vscode,root);
 // A subagent rollout does not count as a message from the panel.
 fs.writeFileSync(path.join(root,'2026/11/15/rollout-a.jsonl'),'{"type":"session_meta","payload":{"originator":"codex_vscode","source":{"subagent":{}}}}\n');
 await new Promise(r=>setTimeout(r,300));assert.equal(newChatIndex(group,'codex'),0);
 fs.writeFileSync(path.join(root,'2026/11/15/rollout-b.jsonl'),'{"timestamp":"t","type":"session_meta","payload":{"id":"x","originator":"codex_vscode","cli_version":"1","source":"vscode"}}\n');
 for(let i=0;i<40&&newChatIndex(group,'codex')===0;i++)await new Promise(r=>setTimeout(r,50));
 watcher.dispose();fs.rmSync(root,{recursive:true,force:true});
 assert.equal(newChatIndex(group,'codex'),1);
});
test('only a focused window marks its active new Codex tab, and only panel threads count',()=>{
 const tab={label:'Codex',input:{viewType:'chatgpt.conversationEditor',uri:{path:'/extension/panel/new',query:'pane=3'}}},group={tabs:[tab],activeTab:tab};
 markCodexTurn({window:{state:{focused:false},tabGroups:{activeTabGroup:group}}});assert.equal(newChatIndex(group,'codex'),0);
 markCodexTurn({window:{state:{focused:true},tabGroups:{activeTabGroup:group}}});assert.equal(newChatIndex(group,'codex'),-1);
 assert.equal(isPanelThread('{"type":"session_meta","payload":{"originator":"codex_exec","source":"exec"}}'),false);
 assert.equal(isPanelThread('{"type":"session_meta","payload":{"originator":"chat_library","source":"vscode"}}'),false);
});
const {providerOf,route,findTranscript,chooseProvider,official}=require('../agent-tabs.cjs');
test('official chats are recognized by provider; Library and file tabs are not',()=>{
 assert.deepEqual([claude('Fix it'),codex('Thread','/local/abc'),library('New chat'),file].map(providerOf),['claude','codex',undefined,undefined]);
});
test('a chat routes to its official panel in this window or a Library pane with a reason',()=>{
 const root='/Users/me/work',here={file:'/h/.claude/projects/-Users-me-work/s1.jsonl',cwd:root},elsewhere={file:'/h/.claude/projects/-Users-me-other/s1.jsonl',cwd:'/Users/me/other'};
 assert.deepEqual(route({kind:'chat',draftId:'d'},{root}),{engine:'new',provider:undefined});
 assert.deepEqual(route({kind:'chat',draftId:'d',meta:{source:'codex'}},{root}),{engine:'new',provider:'codex'});
 assert.equal(route({kind:'chat',draftId:'d',meta:{forkSourceKey:'claude-s1',forkThroughTurn:2}},{root}).engine,'library');
 assert.deepEqual(route({kind:'chat',key:'claude-s1'},{root,transcript:here}),{engine:'official',provider:'claude',id:'s1'});
 assert.deepEqual(route({kind:'chat',key:'claude-s1'},{root,transcript:elsewhere}),{engine:'official',provider:'claude',id:'s1'});
 assert.deepEqual(route({kind:'chat',key:'codex-t1'},{root,transcript:{file:'/h/.codex/sessions/2026/rollout-x-t1.jsonl',cwd:'/Users/me/other'}}),{engine:'official',provider:'codex',id:'t1'});
 for(const [resource,transcript] of [
  [{kind:'chat',key:'codex-t1',runId:'run-1'},{file:'f'}],
  [{kind:'chat',key:'codex-t1',meta:{node:'sc'}},{file:'f'}],
  [{kind:'chat',key:'codex-t1',meta:{parent_session_id:'p'}},{file:'f'}],
  [{kind:'chat',key:'codex-t1'},{file:'f',subagent:true}],
  [{kind:'chat',key:'codex-t1'},undefined],
  [{kind:'chat',key:'claude-s1'},{file:elsewhere.file}],
  [{kind:'chat',key:'gemini-g1'},{file:'f'}],
 ]){const target=route(resource,{root,transcript});assert.equal(target.engine,'library');assert.match(target.reason,/\w/);}
});
test('transcripts are found on this Mac, with the folder and the subagent marker',async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-home-')),folder=fs.mkdtempSync(path.join(os.tmpdir(),'official-cwd-'));
 fs.mkdirSync(path.join(home,'.claude/projects/-x'),{recursive:true});fs.mkdirSync(path.join(home,'.codex/sessions/2026/11/01'),{recursive:true});
 fs.writeFileSync(path.join(home,'.claude/projects/-x/s1.jsonl'),JSON.stringify({type:'user',cwd:folder})+'\n');
 fs.writeFileSync(path.join(home,'.codex/sessions/2026/11/01/rollout-2026-t-t1.jsonl'),'{"type":"session_meta","payload":{"id":"t1","cwd":"/gone","source":{"subagent":{}},"thread_source":"subagent"}}\n');
 const saved=[process.env.CLAUDE_CONFIG_DIR,process.env.CODEX_HOME];delete process.env.CLAUDE_CONFIG_DIR;delete process.env.CODEX_HOME;
 try{
  assert.deepEqual(await findTranscript('claude','s1',home),{file:path.join(home,'.claude/projects/-x/s1.jsonl'),cwd:folder,subagent:false});
  assert.deepEqual(await findTranscript('codex','t1',home),{file:path.join(home,'.codex/sessions/2026/11/01/rollout-2026-t-t1.jsonl'),cwd:undefined,subagent:true,missingCwd:true});
  assert.equal(await findTranscript('claude','missing',home),undefined);
 }finally{if(saved[0]!==undefined)process.env.CLAUDE_CONFIG_DIR=saved[0];if(saved[1]!==undefined)process.env.CODEX_HOME=saved[1];fs.rmSync(home,{recursive:true,force:true});fs.rmSync(folder,{recursive:true,force:true});}
});
function fakePicker(answer){
 const shown=[];return {shown,createQuickPick(){const handlers={};const picker={items:[],activeItems:[],selectedItems:[],onDidAccept:f=>handlers.accept=f,onDidHide:f=>handlers.hide=f,hide:()=>handlers.hide(),dispose(){},
  show(){shown.push(picker);setTimeout(()=>{if(answer){picker.selectedItems=[picker.items.find(i=>i.provider===answer)];handlers.accept();}else handlers.hide();});}};return picker;}};
}
function memento(values={}){return {get:(k,d)=>k in values?values[k]:d,update:async(k,v)=>{values[k]=v;},values};}
test('a new chat follows the active official chat, else asks and highlights the last choice',async()=>{
 const storage=memento({officialProvider:'codex'});
 let ui=fakePicker('claude');
 assert.equal(await chooseProvider({window:{...ui,tabGroups:{activeTabGroup:{activeTab:codex('Thread','/local/t')}}}},storage),'codex');
 assert.equal(await chooseProvider({window:{...ui,tabGroups:{activeTabGroup:{activeTab:claude('Fix it')}}}},storage),'claude');
 assert.equal(ui.shown.length,0);
 assert.equal(await chooseProvider({window:{...ui,tabGroups:{activeTabGroup:{activeTab:file}}}},storage),'claude');
 assert.deepEqual(ui.shown[0].items.map(i=>i.label),['Claude Code','Codex']);
 assert.equal(ui.shown[0].activeItems[0].provider,'codex');
 assert.equal(storage.values.officialProvider,'claude');
 ui=fakePicker(undefined);
 assert.equal(await chooseProvider({window:{...ui,tabGroups:{activeTabGroup:{}}}},storage),undefined);
 assert.equal(ui.shown[0].activeItems[0].provider,'claude');
});
test('a library chat or the default provider setting skips the picker',async()=>{
 const ui=fakePicker('claude'),empty={window:{...ui,tabGroups:{activeTabGroup:{}}}};
 assert.equal(await chooseProvider(empty,memento({preferences:{defaultProvider:'ask'}}),'codex'),'codex');
 assert.equal(await chooseProvider(empty,memento({preferences:{defaultProvider:'codex'}})),'codex');
 assert.equal(ui.shown.length,0);
 assert.equal(await chooseProvider(empty,memento({preferences:{defaultProvider:'ask'}})),'claude');
 assert.equal(ui.shown.length,1);
});
test('in official mode sidebar clicks open or focus the official panel; Library mode is untouched',async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-open-'));
 fs.mkdirSync(path.join(home,'.claude/projects',require('../agent-tabs.cjs').projectDir(home)),{recursive:true});fs.mkdirSync(path.join(home,'.codex/sessions/2026/11/01'),{recursive:true});
 fs.writeFileSync(path.join(home,'.claude/projects',require('../agent-tabs.cjs').projectDir(home),'s1.jsonl'),JSON.stringify({cwd:home})+'\n');
 fs.writeFileSync(path.join(home,'.codex/sessions/2026/11/01/rollout-x-t1.jsonl'),'{"type":"session_meta","payload":{"id":"t1","source":"vscode"}}\n');
 const saved=[process.env.CLAUDE_CONFIG_DIR,process.env.CODEX_HOME];process.env.CLAUDE_CONFIG_DIR=path.join(home,'.claude');process.env.CODEX_HOME=path.join(home,'.codex');
 let engine='official';const calls=[],messages=[],agentChats=[];
 const group={viewColumn:2,tabs:[file],activeTab:file};
 const vscode={workspace:{getConfiguration:()=>({get:()=>engine}),workspaceFolders:[{uri:{scheme:'file',fsPath:home}}]},
  window:{tabGroups:{activeTabGroup:group,all:[group]},showInformationMessage:m=>{messages.push(m);},onDidChangeWindowState:()=>({dispose(){}}),...fakePicker('codex')},
  ViewColumn:{Active:-1},Uri:{parse:s=>({toString:()=>s}),file:p=>({fsPath:p})},extensions:{getExtension:id=>({id,activate:async()=>{}})},
  commands:{executeCommand:async(...args)=>{calls.push(args);if(args[0]==='vscode.openWith')group.tabs.push({label:'Thread',input:{viewType:'chatgpt.conversationEditor',uri:args[1]}});}}};
 const context={globalStorageUri:{fsPath:path.join(home,'storage')},globalState:memento(),subscriptions:[]};
 try{
  const chats=official(vscode,context,async provider=>{agentChats.push(provider);},{api:async()=>({sessions:[]}),helper:async()=>({config_dir:path.join(home,'.claude'),account_id:'test',active:false}),noWatch:true});
  assert.equal(await chats.open({kind:'chat',key:'claude-s1',title:'Old'}),true);
  assert.equal(await chats.open({kind:'chat',key:'codex-t1',title:'Old'}),true);
  assert.equal(await chats.open({kind:'chat',key:'codex-t1',title:'Old'}),true);
  assert.deepEqual(calls,[['claude-vscode.editor.open','s1',undefined,2],['vscode.openWith',calls[1][1],'chatgpt.conversationEditor',{viewColumn:2,preview:false}],['workbench.action.focusSecondEditorGroup'],['workbench.action.openEditorAtIndex',1]]);
  assert.equal(calls[1][1].toString(),'openai-codex://route/local/t1');
  assert.equal(await chats.open({kind:'chat',draftId:'d',title:'New chat'}),true);
  assert.deepEqual(agentChats,['codex']);
  assert.equal(await chats.open({kind:'chat',key:'codex-t1',runId:'r'}),false);
  assert.equal(messages.length,1);
  engine='library';calls.length=0;
  assert.equal(await chats.open({kind:'chat',key:'codex-t1'}),false);
  assert.equal(chats.enabled(),false);assert.deepEqual(calls,[]);
 }finally{if(saved[0]!==undefined)process.env.CLAUDE_CONFIG_DIR=saved[0];else delete process.env.CLAUDE_CONFIG_DIR;if(saved[1]!==undefined)process.env.CODEX_HOME=saved[1];else delete process.env.CODEX_HOME;fs.rmSync(home,{recursive:true,force:true});}
});

 test('provider icon captures the clicked pane before activation and permits concurrent other panes',async()=>{
  let activate;const wait=new Promise(resolve=>{activate=resolve;}),calls=[];
  const a={viewColumn:1,tabs:[]},b={viewColumn:2,tabs:[]};
  const vscode={window:{tabGroups:{activeTabGroup:a,all:[a,b]}},ViewColumn:{Active:-1},Uri:{parse:s=>s},
   extensions:{getExtension:()=>({activate:()=>wait})},commands:{executeCommand:async(...args)=>calls.push(args)}};
  const open=create(vscode,()=>assert.fail('unexpected fallback'));
  const first=open('codex');vscode.window.tabGroups.activeTabGroup=b;const second=open('codex');
  activate();await Promise.all([first,second]);
  assert.equal(calls.length,2);assert.deepEqual(calls.map(c=>c[3].viewColumn),[1,2]);
  assert.notEqual(calls[0][1],calls[1][1],'new resources in different panes must be distinct');
 });
 test('Claude icon opens in the pane captured before extension activation',async()=>{
  let activate;const wait=new Promise(resolve=>{activate=resolve;}),calls=[];
  const a={viewColumn:1,tabs:[]},b={viewColumn:2,tabs:[claude()]};
  const vscode={window:{tabGroups:{activeTabGroup:a,all:[a,b]}},ViewColumn:{Active:-1},
   extensions:{getExtension:()=>({activate:()=>wait})},commands:{executeCommand:async(...args)=>calls.push(args)}};
  const first=create(vscode,()=>assert.fail('unexpected fallback'))('claude');
  vscode.window.tabGroups.activeTabGroup=b;activate();await first;
  assert.deepEqual(calls,[['claude-vscode.editor.open',undefined,undefined,1]]);
 });

test('Claude project names match the official long-path UTF-16 hash rule',()=>{
 const {projectDir}=require('../agent-tabs.cjs');
 assert.equal(projectDir('/a b/c'),'-a-b-c');
 const raw='/'+('x'.repeat(205))+'😀';let hash=0;for(let i=0;i<raw.length;i++)hash=(31*hash+raw.charCodeAt(i))|0;
 assert.equal(projectDir(raw),raw.replace(/[^a-zA-Z0-9]/g,'-').slice(0,200)+'-'+Math.abs(hash).toString(36));
});
test('cross-folder Claude sessions stay in the current window',()=>{
 assert.deepEqual(route({kind:'chat',key:'claude-session'},{root:'/workspace',transcript:{file:'/other/session.jsonl',cwd:'/other'}}),{engine:'official',provider:'claude',id:'session'});
});
test('transcript lookup uses last cwd and skips aliases to avoid self-links',async()=>{
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'official-last-cwd-')),projects=path.join(home,'.claude/projects'),a=path.join(home,'a'),b=path.join(home,'b');
 fs.mkdirSync(a);fs.mkdirSync(b);fs.mkdirSync(path.join(projects,'-actual'),{recursive:true});fs.mkdirSync(path.join(projects,'-alias'));
 const actual=path.join(projects,'-actual/s1.jsonl');fs.writeFileSync(actual,JSON.stringify({cwd:a})+'\n'+JSON.stringify({cwd:b})+'\n');fs.symlinkSync(actual,path.join(projects,'-alias/s1.jsonl'));
 const saved=process.env.CLAUDE_CONFIG_DIR;process.env.CLAUDE_CONFIG_DIR=path.join(home,'.claude');
 try{const found=await findTranscript('claude','s1',home);assert.equal(found.file,actual);assert.equal(found.cwd,b);assert.equal(await findTranscript('claude','../escape',home),undefined);}
 finally{if(saved===undefined)delete process.env.CLAUDE_CONFIG_DIR;else process.env.CLAUDE_CONFIG_DIR=saved;fs.rmSync(home,{recursive:true,force:true});}
});

test('new remote and different-folder drafts never silently launch a local native panel',()=>{
 for(const provider of ['claude','codex']){
  const base={kind:'chat',draftId:'new',meta:{source:provider}};
  for(const remote of [{...base,host_id:'sc'},{...base,meta:{...base.meta,execution_host:'sc'}}])assert.equal(route(remote,{root:'/local'}).engine,'library');
  assert.equal(route({...base,cwd:'/other'},{root:'/local'}).engine,'library');
  assert.equal(route({...base,cwd:'/local'},{}).engine,'library');
  assert.equal(route({...base,cwd:'/local'},{root:'/local'}).engine,'new');
 }
});
