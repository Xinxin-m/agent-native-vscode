'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=p=>fs.readFileSync(path.join(__dirname,p),'utf8');
test('a chat dropped on the editor center shows in the current tab; an edge or the tab bar opens a tab',()=>{
 const core=read('../../../vscode/src/vs/workbench/browser/parts/editor/editorDropTarget.ts');
 assert.match(core,/executeCommand\('library\.openResource', resource, column, typeof splitDirection === 'number' \? undefined : 'current'\)/);
 const tabs=read('../../../vscode/src/vs/workbench/browser/parts/editor/multiEditorTabsControl.ts');
 assert.match(tabs,/executeCommand\('library\.openResource', resource, column\)\)/,'tab bar: a new tab');
 assert.match(tabs,/const lastTab = this\.addTabContainer \? this\.addTabContainer\.previousElementSibling : element\.lastElementChild;/,'tab bar: the insertion marker sits on the last tab, not the hidden add-tab control');
 const host=read('../extension.cjs');
 assert.match(host,/command\('library\.openResource',\(resource,column,place\)=>place==='current'\?openInCurrent\(resource,column\):open\(resource,'tab',column\)\)/);
 assert.match(host,/const existing=resource\?\.kind==='chat'&&resource\.key&&panels\.get\(resourceKey\(resource\)\);\n  if\(existing\)\{existing\.reveal\(existing\.viewColumn\);return;\}\n  if\(resource\?\.kind==='chat'&&resource\.key&&await revealExisting\(vscode,resource\)\)return;/,'a chat already open is focused, not opened again');
 assert.match(host,/if\(!current\)return open\(resource,'tab',column\);\n  current\.reveal\(column\);return current\.webview\.postMessage\(\{type:'dropChat',resource\}\);/);
 assert.match(read('../../ui/src/native/boot.ts'),/m\?\.type==='dropChat'\)window\.dispatchEvent\(new CustomEvent\('library-drop-chat'/);
 const ui=read('../../ui/src/chat/Conversation.tsx');
 assert.match(ui,/if\(!state\.text\.trim\(\)&&!state\.attachments\.length&&!active&&!busy&&next\?\.key&&wb\.replace\)wb\.replace\(resource\.meta\?\.viewId,next\);else wb\.open\(next,'tab'\);/,'a draft or running turn keeps its tab');
});
