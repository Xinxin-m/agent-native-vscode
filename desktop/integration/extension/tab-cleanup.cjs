'use strict';

function isFileTab(vscode, tab) {
 // An official Codex chat is a custom editor on an openai-codex: URI, not a file.
 if (tab.input?.uri?.scheme === 'openai-codex') return false;
 return [vscode.TabInputText, vscode.TabInputTextDiff, vscode.TabInputNotebook,
  vscode.TabInputNotebookDiff, vscode.TabInputCustom].some(Type => Type && tab.input instanceof Type);
}
async function closeFiles(vscode, currentPaneOnly) {
 const groups = currentPaneOnly ? [vscode.window.tabGroups.activeTabGroup] : vscode.window.tabGroups.all;
 for (const group of groups) {
  const keepPinned = vscode.workspace.getConfiguration('library').get('cleanWorkspace') === 'pinned';
  const files = group.tabs.filter(tab => isFileTab(vscode, tab) && !(keepPinned && tab.isPinned));
  if (files.length && !await vscode.window.tabGroups.close(files, true)) return false;
 }
 return true;
}
function register(vscode, context) {
 context.subscriptions.push(
  vscode.commands.registerCommand('library.closeFilesInPane', () => closeFiles(vscode, true)),
  vscode.commands.registerCommand('library.closeFilesEverywhere', () => closeFiles(vscode, false))
 );
}
module.exports = { isFileTab, closeFiles, register };
