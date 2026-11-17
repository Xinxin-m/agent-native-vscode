'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {register}=require('../chat-references.cjs');
const ts=require('../../../vscode/node_modules/typescript');
const source=fs.readFileSync(path.join(__dirname,'../../../vscode/src/vs/workbench/browser/parts/editor/libraryChatIdentity.ts'),'utf8'),exportsObject={};
new Function('exports',ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(exportsObject);
const {libraryChatIdentity}=exportsObject;
test('native identity reads verified Claude state, Codex saved route, and Library resource only',()=>{
 assert.deepEqual(libraryChatIdentity({viewType:'mainThreadWebview-claudeVSCodePanel',webview:{state:JSON.stringify({sessionID:'claude-id',draft:'private draft'})}}),{isChat:true,provider:'claude',key:'claude-claude-id'});
 assert.deepEqual(libraryChatIdentity({resource:{scheme:'openai-codex',path:'/local/codex-id'}}),{isChat:true,provider:'codex',key:'codex-codex-id'});
 assert.deepEqual(libraryChatIdentity({viewType:'mainThreadWebview-library.conversation',webview:{state:JSON.stringify({resource:{kind:'chat',key:'claude-saved'},storage:{draft:'private'}})}}),{isChat:true,key:'claude-saved',provider:'claude'});
 assert.deepEqual(libraryChatIdentity({viewType:'library.conversation',webview:{state:JSON.stringify({resource:{kind:'files'}})}}),{isChat:false});
});
test('missing/malformed vendor state never guesses a session from title or active focus',()=>{
 assert.deepEqual(libraryChatIdentity({viewType:'claudeVSCodePanel',getName:()=> 'claude-secret',webview:{state:'bad'}}),{isChat:true,provider:'claude'});
 assert.deepEqual(libraryChatIdentity({viewType:'claudeVSCodePanel',webview:{state:'null'}}),{isChat:true,provider:'claude'});
 assert.deepEqual(libraryChatIdentity({resource:{scheme:'openai-codex',path:'/extension/panel/new'}}),{isChat:true,provider:'codex'});
 assert.deepEqual(libraryChatIdentity({resource:{scheme:'file',path:'/notes.md'}}),{isChat:false});
});
test('four copy actions preserve exact API bytes and use the right-clicked tab identity',async()=>{
 const handlers=new Map(),copied=[],status=[],requests=[];
 const vscode={commands:{registerCommand:(id,fn)=>{handlers.set(id,fn);return {dispose(){}};},executeCommand:async()=>assert.fail('Exact context must not consult active tab')},env:{clipboard:{writeText:async value=>copied.push(value)}},window:{setStatusBarMessage:(...args)=>status.push(args),showErrorMessage:message=>assert.fail(message)}};
 const values={reference:'claude session id | reference bytes\n',session_id:'id',transcript_path:'/tmp/file with spaces.jsonl',resume_command:"cd '/tmp/a b' && claude --resume id"};
 register(vscode,{subscriptions:[]},async route=>{requests.push(route);return values;});
 for(const suffix of ['reference','sessionId','transcriptPath','resumeCommand'])await handlers.get('library.copyChat.'+suffix)({scheme:'webview-panel'},{libraryChatKey:'claude-id',groupId:99,editorIndex:3});
 assert.deepEqual(copied,Object.values(values));assert.ok(requests.every(route=>route==='/api/session/reference?key=claude-id'));assert.ok(status.every(([text])=>text==='Copied'));
});
test('unknown tab identity never reads a reference or writes clipboard',async()=>{
 const handlers=new Map(),errors=[];
 const vscode={commands:{registerCommand:(id,fn)=>{handlers.set(id,fn);return {dispose(){}};},executeCommand:async()=>[]},env:{clipboard:{writeText:async()=>assert.fail('No copy')}},window:{tabGroups:{activeTabGroup:{viewColumn:1,tabs:[]}},showErrorMessage:message=>errors.push(message)}};
 register(vscode,{subscriptions:[]},async()=>assert.fail('No lookup'));
 await handlers.get('library.copyChat.reference')();assert.equal(errors.length,1);
});
