'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {closeFiles}=require('../tab-cleanup.cjs');
function fixture(keep='chats'){
 class Text{} class Custom{} class Webview{}
 const chat={input:new Webview()}, clean={input:new Text()}, dirty={input:new Custom(),isDirty:true};
 const active={tabs:[chat,dirty]}, other={tabs:[clean]}; let call;
 const vscode={TabInputText:Text,TabInputCustom:Custom,workspace:{getConfiguration:()=>({get:()=>keep})},window:{tabGroups:{activeTabGroup:active,all:[active,other],close:async (...args)=>{call=args;return true;}}}};
 return {vscode,chat,clean,dirty,get call(){return call;}};
}
test('passes dirty files to native close',async()=>{const f=fixture();assert.equal(await closeFiles(f.vscode,true),true);assert.deepEqual(f.call,[[f.dirty],true]);});
test('closes files across panes while preserving conversations',async()=>{const f=fixture();await closeFiles(f.vscode,false);assert.deepEqual(f.call,[[f.clean],true]);});

test('keeps pinned files when the clean workspace setting says so',async()=>{const f=fixture('pinned');f.clean.isPinned=true;await closeFiles(f.vscode,false);assert.deepEqual(f.call,[[f.dirty],true]);});
test('cancellation stops before closing files in later panes',async()=>{const f=fixture();let calls=0;f.vscode.window.tabGroups.close=async()=>{calls++;return false;};assert.equal(await closeFiles(f.vscode,false),false);assert.equal(calls,1);});
