"use strict";
const test=require('node:test');const assert=require('node:assert/strict');
const settings=require('../settings-config.cjs');

function fakeVscode(initial={}){
 const values={...initial};
 return {values,vscode:{ConfigurationTarget:{Global:1},workspace:{getConfiguration:()=>({get:key=>values[key],update:async(key,value)=>{values[key]=value;}})}}};
}

test('a setting written from the page persists and is read back',async()=>{
 const {vscode,values}=fakeVscode({'library.iconBar':'visible','editor.fontSize':12});
 const reply=await settings.handle(vscode,{key:'library.iconBar',value:'autoHide'});
 assert.equal(values['library.iconBar'],'autoHide');
 assert.equal(reply.values['library.iconBar'],'autoHide');
 assert.deepEqual(reply.schemas['library.iconBar'].enum,['visible','autoHide','hidden']);
 assert.equal((await settings.handle(vscode,{})).values['library.iconBar'],'autoHide');
 await settings.handle(vscode,{key:'editor.fontSize',value:15});
 assert.equal(values['editor.fontSize'],15);
});

test('unknown keys, read-only keys and bad values are refused',async()=>{
 const {vscode,values}=fakeVscode();
 await assert.rejects(settings.handle(vscode,{key:'security.workspace.trust.enabled',value:false}));
 await assert.rejects(settings.handle(vscode,{key:'library.endpoint',value:'http://127.0.0.1:1'}));
 await assert.rejects(settings.handle(vscode,{key:'library.iconBar',value:'sideways'}));
 await assert.rejects(settings.handle(vscode,{key:'editor.fontSize',value:'huge'}));
 await assert.rejects(settings.handle(vscode,{key:'workbench.statusBar.visible',value:'yes'}));
 assert.deepEqual(values,{});
});

test('Files tab and clean workspace settings are writable from the page',async()=>{
 const {vscode,values}=fakeVscode();
 await settings.handle(vscode,{key:'library.files.tabWorkspace',value:'all'});
 const reply=await settings.handle(vscode,{key:'library.cleanWorkspace',value:'pinned'});
 assert.equal(values['library.files.tabWorkspace'],'all');
 assert.deepEqual(reply.schemas['library.cleanWorkspace'].enum,['chats','pinned']);
 await assert.rejects(settings.handle(vscode,{key:'library.cleanWorkspace',value:'everything'}));
});
