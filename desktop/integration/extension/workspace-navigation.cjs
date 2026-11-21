'use strict';

async function openWorkspaceFolder(vscode,{uri,name}){
 // A first folder can restart this extension host. Select Explorer before requesting the change.
 await vscode.commands.executeCommand('workbench.view.explorer');
 const folders=vscode.workspace.workspaceFolders||[];
 if(!folders.some(folder=>folder.uri.toString()===uri.toString())&&!vscode.workspace.updateWorkspaceFolders(folders.length,0,{uri,name})){
  throw Error('Could not add workspace');
 }
}

async function showInitialChats(vscode,state,openWelcome){
 if(state.get('initialChatsShown')||vscode.workspace.workspaceFile||vscode.workspace.workspaceFolders?.length||
    vscode.window.tabGroups.all.some(group=>group.tabs.length))return;
 await state.update('initialChatsShown',true);
 await vscode.commands.executeCommand('workbench.view.extension.library');
 if(vscode.window.tabGroups.all.every(group=>!group.tabs.length))await openWelcome();
}

// 'entire' is All chats, the default since November 20, 2026. Choices made after that live under v2;
// an older saved choice keeps a specific workspace, but its implicit 'added' default becomes All chats once.
function createWorkspaceChoice(state,publish){
 const key='chatWorkspaceChoice.v2';
 let pending=Promise.resolve();
 const legacy=()=>{const value=state.get('chatWorkspaceChoice',state.get('draft:sidebar',{})['workbench-chat-workspace']);return !value||value==='added'?'entire':value;};
 return {
  get:()=>state.get(key)||legacy(),
  set(choice){
   if(typeof choice!=='string'||choice.length>8192||!choice)throw Error('Invalid conversation workspace');
   const update=pending.then(async()=>{await state.update(key,choice);publish(choice);});
   pending=update.catch(()=>{});
   return update;
  }
 };
}

module.exports={openWorkspaceFolder,showInitialChats,createWorkspaceChoice};
