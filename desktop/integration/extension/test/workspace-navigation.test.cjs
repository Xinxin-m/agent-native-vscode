'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {showInitialChats,openWorkspaceFolder,createWorkspaceChoice}=require('../workspace-navigation.cjs');

function harness(folders=[],tabs=[],workspaceFile){
 const calls=[],values=new Map();
 const state={get:(key,fallback)=>values.get(key)??fallback,update:async(key,value)=>{values.set(key,value)}};
 const vscode={workspace:{workspaceFolders:folders,workspaceFile,updateWorkspaceFolders:(index,remove,folder)=>{calls.push(['add',index,remove,folder]);return true}},
  window:{tabGroups:{all:[{tabs}]}},commands:{executeCommand:async(...args)=>{calls.push(args)}}};
 return {vscode,state,calls,values};
}
test('opening the first root reveals Explorer before replacing the extension host',async()=>{
 const h=harness(),folder={uri:'file:///Projects',name:'Projects'};
 await openWorkspaceFolder(h.vscode,folder);
 assert.deepEqual(h.calls,[['workbench.view.explorer'],['add',0,0,folder]]);
 const restarted=harness([folder]);
 await showInitialChats(restarted.vscode,restarted.state,()=>assert.fail('Opened a draft'));
 assert.deepEqual(restarted.calls,[]);
});
test('opening a second root or a folder already open leaves Explorer selected',async()=>{
 const first={uri:'file:///Projects',name:'Projects'},second={uri:'library-file://cluster/',name:'Cluster'};
 const h=harness([first]);
 await openWorkspaceFolder(h.vscode,second);
 await openWorkspaceFolder(h.vscode,first);
 assert.deepEqual(h.calls,[['workbench.view.explorer'],['add',1,0,second],['workbench.view.explorer']]);
});
test('activation defaults to Chats only once in a new empty window',async()=>{
 const h=harness();let drafts=0;
 await showInitialChats(h.vscode,h.state,()=>drafts++);
 await showInitialChats(h.vscode,h.state,()=>drafts++);
 assert.deepEqual(h.calls,[['workbench.view.extension.library']]);assert.equal(drafts,1);
 for(const existing of [harness([],['existing tab']),harness([],[],'workspace-file')]){
  await showInitialChats(existing.vscode,existing.state,()=>assert.fail('Opened a draft'));
  assert.deepEqual(existing.calls,[]);
 }
});
test('native workspace choice migrates the old sidebar setting, persists and broadcasts in order',async()=>{
 const h=harness(),published=[];
 const sc='["cluster","/Projects"]';
 await h.state.update('draft:sidebar',{'workbench-chat-workspace':sc});
 const choice=createWorkspaceChoice(h.state,value=>published.push(value));
 assert.equal(choice.get(),sc);
 await Promise.all([choice.set('entire'),choice.set('added')]);
 assert.deepEqual(published,['entire','added']);
 assert.equal(createWorkspaceChoice(h.state,()=>{}).get(),'added');
});

test('the old implicit added-workspaces default becomes All chats once; later choices persist',async()=>{
 const h=harness();
 assert.equal(createWorkspaceChoice(h.state,()=>{}).get(),'entire');
 await h.state.update('chatWorkspaceChoice','added');
 const choice=createWorkspaceChoice(h.state,()=>{});
 assert.equal(choice.get(),'entire');
 await choice.set('added');
 assert.equal(createWorkspaceChoice(h.state,()=>{}).get(),'added');
});
