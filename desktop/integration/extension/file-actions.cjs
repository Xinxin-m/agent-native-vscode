'use strict';
const path=require('node:path');
async function createItem(vscode,files,type,selected){
 const tab=vscode.window.tabGroups?.activeTabGroup?.activeTab;
 const target=selected||tab?.input?.uri||vscode.window.activeTextEditor?.document?.uri||(vscode.workspace.workspaceFolders||[])[0]?.uri;
 if(!target)throw Error('Open a local folder before creating a file or folder.');
 if(target.scheme!=='file')throw Error('Remote files are read-only.');
 await require('./file-context.cjs').workspaceFor(vscode,files.rootId,target);
 const stat=await vscode.workspace.fs.stat(target),parent=stat.type&vscode.FileType.Directory?target:vscode.Uri.file(path.dirname(target.fsPath));
 const folder=vscode.workspace.getWorkspaceFolder(parent);if(!folder)throw Error('The containing workspace is not open.');
 let value='',error='';
 while(true){
  value=await vscode.window.showInputBox({title:type==='file'?'New File':'New Folder',prompt:error||parent.fsPath,value,ignoreFocusOut:false,validateInput:name=>!name.trim()||name==='.'||name==='..'||/[\\/\0]/.test(name)?'Enter a name without slashes.':undefined});
  if(value===undefined)return;
  const name=value.trim();if(!name||name==='.'||name==='..'||/[\\/\0]/.test(name)){error='Enter a name without slashes.';continue;}
  const relative=path.relative(folder.uri.fsPath,parent.fsPath).split(path.sep).join('/'),filePath=[relative,name].filter(Boolean).join('/');
  try{await files.request('/api/files/create',{root_id:files.rootId(folder.uri),path:filePath,type});const uri=vscode.Uri.joinPath(parent,name);await vscode.commands.executeCommand('workbench.files.action.refreshFilesExplorer');if(type==='file')await vscode.commands.executeCommand('vscode.open',uri,{preview:false});else await vscode.commands.executeCommand('revealInExplorer',uri);return uri;}
  catch(e){if(e.data?.code==='destination_exists'){error=e.message;continue;}throw e;}
 }
}
module.exports={createItem};
