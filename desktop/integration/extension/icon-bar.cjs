"use strict";
// The left icon bar: Always visible, Auto-hide or Hidden (`library.iconBar`).
// Hidden uses VS Code's own activity bar location; auto-hide is drawn by the
// patched activity bar part, which needs the bar in its default location.
const modes=['visible','autoHide','hidden'];
const mode=vscode=>{const value=vscode.workspace.getConfiguration('library').get('iconBar');return modes.includes(value)?value:'visible';};
const location=vscode=>mode(vscode)==='hidden'?'hidden':'default';
async function apply(vscode){
 const workbench=vscode.workspace.getConfiguration('workbench');
 if(workbench.get('activityBar.location')!==location(vscode))await workbench.update('activityBar.location',location(vscode),vscode.ConfigurationTarget.Global);
}
function register(vscode,context){
 void apply(vscode).catch(()=>{});
 context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(e=>{if(e.affectsConfiguration('library.iconBar'))void apply(vscode).catch(e=>vscode.window.showErrorMessage(e.message));}));
}
module.exports={modes,mode,location,apply,register};
