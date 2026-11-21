'use strict';
// Files follows the active document, including custom HTML/Markdown editors.
async function workspaceFor(vscode,rootId,explicit){
 const tab=vscode.window.tabGroups?.activeTabGroup?.activeTab;
 const uri=explicit||(tab?tab.input?.uri:vscode.window.activeTextEditor?.document?.uri);
 if(uri&&['file','library-file'].includes(uri.scheme)){
  let folder=vscode.workspace.getWorkspaceFolder(uri);
  if(!folder&&uri.scheme==='file'){
   const stat=await vscode.workspace.fs.stat(uri);
   const parent=stat.type&vscode.FileType.Directory?uri:vscode.Uri.file(require('node:path').dirname(uri.fsPath));
   folder=vscode.workspace.getWorkspaceFolder(parent);
   if(!folder){
    const folders=vscode.workspace.workspaceFolders||[];
    let listener,timer;
    const ready=new Promise((resolve,reject)=>{
     const check=()=>{const found=vscode.workspace.getWorkspaceFolder(parent);if(found)resolve(found);};
     listener=vscode.workspace.onDidChangeWorkspaceFolders(check);
     timer=setTimeout(()=>reject(Error('The folder could not be opened in Files.')),3000);
    });
    try{if(!vscode.workspace.updateWorkspaceFolders(folders.length,0,{uri:parent,name:require('node:path').basename(parent.fsPath)}))throw Error('Could not add the containing workspace');folder=await ready;}
    finally{clearTimeout(timer);listener?.dispose();}
   }
  }
  if(folder){const context=uri.scheme==='file'&&!((await vscode.workspace.fs.stat(uri)).type&vscode.FileType.Directory)?{meta:{filesPath:uri.fsPath,filesHost:'mac'}}:{};return {root_id:rootId(folder.uri),title:folder.name,...context};}
 }
 const folders=vscode.workspace.workspaceFolders||[];
 return folders.length===1?{root_id:rootId(folders[0].uri),title:folders[0].name}:{};
}
module.exports={workspaceFor};
