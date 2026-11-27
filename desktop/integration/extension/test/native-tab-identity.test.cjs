'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');const ts=require('../../../vscode/node_modules/typescript');
class WebviewInput{} class CustomEditorInput extends WebviewInput{}
const nativeIdentity={exports:{}};
const identitySource=fs.readFileSync(path.join(__dirname,'../../../vscode/src/vs/workbench/browser/parts/editor/libraryChatIdentity.ts'),'utf8');
vm.runInNewContext(ts.transpileModule(identitySource,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,nativeIdentity);
const globals={exports:{},require(id){if(id.endsWith('libraryChatIdentity.js'))return nativeIdentity.exports;if(id.endsWith('event.js'))return {Emitter:class{event(){} fire(){}}};if(id.endsWith('commands.js'))return {CommandsRegistry:{registerCommand(){}}};if(id.endsWith('webviewEditorInput.js'))return {WebviewInput};if(id.endsWith('customEditorInput.js'))return {CustomEditorInput};return {};}};
const source=fs.readFileSync(path.join(__dirname,'../../../vscode/src/vs/workbench/browser/parts/editor/libraryTabGroups.ts'),'utf8');vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,globals);
test('chat state and custom file editor use stable identity',()=>{
 const chat=new WebviewInput();chat.webview={state:JSON.stringify({resource:{kind:'chat',key:'abc'}})};
 const file=new CustomEditorInput();file.resource={toString:()=> 'file:///notes.md'};file.webview={};
 assert.equal(globals.exports.tabIdentity(chat),'chat:abc');assert.equal(globals.exports.tabIdentity(file),'file:file:///notes.md');
});
test('official Claude and Codex panels count as chats keyed like Library chats',()=>{
 const claudePanel=new WebviewInput();claudePanel.providerId='mainThreadWebview-claudeVSCodePanel';claudePanel.webview={state:JSON.stringify({sessionID:'s1'})};
 const blank=new WebviewInput();blank.providerId='claudeVSCodePanel';blank.webview={state:'{}'};
 const thread=new CustomEditorInput();thread.resource={scheme:'openai-codex',path:'/local/t-1',toString:()=>'openai-codex://route/local/t-1'};thread.webview={};
 const fresh=new CustomEditorInput();fresh.resource={scheme:'openai-codex',path:'/extension/panel/new',toString:()=>'openai-codex://route/extension/panel/new'};fresh.webview={};
 assert.deepEqual([claudePanel,blank,thread,fresh].map(globals.exports.tabIdentity),['chat:claude-s1',undefined,'chat:codex-t-1',undefined]);
});
