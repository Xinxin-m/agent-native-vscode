'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');const ts=require('../../../vscode/node_modules/typescript');
const base=path.join(__dirname,'../../../vscode/src/vs/workbench/browser/parts/editor');
function compile(name,require){const box={exports:{},require};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(base,name),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,box);return box.exports;}
const {TabGroupModel}=compile('libraryTabGroupModel.ts',()=>({}));
const {planPaneArrangement}=compile('libraryPaneArrangement.ts',()=>({}));
class WebviewInput{} class CustomEditorInput extends WebviewInput{}
const registered={};const groupService={},storageService={};
const mod=compile('libraryTabGroups.ts',id=>{
 if(id.endsWith('event.js'))return {Emitter:class{event(){} fire(){}}};
 if(id.endsWith('commands.js'))return {CommandsRegistry:{registerCommand:(id,fn)=>registered[id]=fn}};
 if(id.endsWith('customEditorInput.js'))return {CustomEditorInput};if(id.endsWith('webviewEditorInput.js'))return {WebviewInput};
 if(id.endsWith('editorGroupsService.js'))return {IEditorGroupsService:groupService,GroupsOrder:{GRID_APPEARANCE:2}};
 if(id.endsWith('storage.js'))return {IStorageService:storageService,StorageScope:{WORKSPACE:1},StorageTarget:{MACHINE:1}};
 if(id.endsWith('libraryPaneArrangement.js'))return {planPaneArrangement};
 if(id.endsWith('editor.js'))return {EditorsOrder:{SEQUENTIAL:0}};if(id.endsWith('libraryTabGroupModel.js'))return {TabGroupModel};return {};
});
test('automatic regroup moves matching tabs together and chats-only removes file membership',async()=>{
 const chat=new WebviewInput();chat.webview={state:JSON.stringify({resource:{kind:'chat',key:'a'}})};
 const file={resource:{toString:()=> 'file:///project/a.md'}};const other={resource:{toString:()=> 'file:///else/b'}};
 const editors=[chat,other,file];const pane={id:1,getEditors:()=>editors.slice(),contains:editor=>editors.includes(editor),focus(){},moveEditor(editor,_target,{index}){editors.splice(editors.indexOf(editor),1);editors.splice(index,0,editor);}};
 const groups={groups:[pane],getGroups:()=>[pane],getGroup:id=>id===pane.id?pane:undefined,activeGroup:pane,enforcePartOptions(options){assert.equal(options.closeEmptyGroups,false);return {dispose(){}};}},storage={get(){},store(){}};const accessor={get(id){return id===groupService?groups:storage;}};
 const data={assignments:{a:'p'},projects:[{id:'p',name:'P',prefixes:['file:///project']}],includeFiles:true};
 await registered['_library.groupTabsByProject'](accessor,data);
 assert.deepEqual(editors,[chat,file,other]);assert.equal(mod.tabGroups(storage).groupFor('1','file:file:///project/a.md').name,'P');
 await registered['_library.groupTabsByProject'](accessor,{...data,includeFiles:false});
 assert.equal(mod.tabGroups(storage).groupFor('1','file:file:///project/a.md'),undefined);assert.equal(mod.tabGroups(storage).groupFor('1','chat:a').name,'P');
});

test('submitted drafts group by their native conversation without changing stable tab identity',async()=>{
 const submitted=new WebviewInput();submitted.webview={state:JSON.stringify({resource:{kind:'chat',draftId:'draft-one',meta:{nativeKey:'server-one'}}})};
 const unsent=new WebviewInput();unsent.webview={state:JSON.stringify({resource:{kind:'chat',draftId:'draft-unsent'}})};
 const unassigned=new WebviewInput();unassigned.webview={state:JSON.stringify({resource:{kind:'chat',draftId:'draft-unassigned',meta:{nativeKey:'server-unassigned'}}})};
 const editors=[submitted,unsent,unassigned];const pane={id:2,getEditors:()=>editors.slice(),contains:editor=>editors.includes(editor),focus(){},moveEditor(editor,_target,{index}){editors.splice(editors.indexOf(editor),1);editors.splice(index,0,editor);}};
 const groups={groups:[pane],getGroups:()=>[pane],getGroup:id=>id===pane.id?pane:undefined,activeGroup:pane,enforcePartOptions(options){assert.equal(options.closeEmptyGroups,false);return {dispose(){}};}},storage={get(){},store(){}};const accessor={get(id){return id===groupService?groups:storage;}};
 await registered['_library.groupTabsByProject'](accessor,{assignments:{'server-one':'p'},projects:[{id:'p',name:'Project',prefixes:[]}],includeFiles:false});
 const model=mod.tabGroups(storage);
 assert.deepEqual({identity:mod.tabIdentity(submitted),group:model.groupFor('2','draft:draft-one')?.name,nativeIdentityGroup:model.groupFor('2','chat:server-one'),unsent:model.groupFor('2','draft:draft-unsent')?.name,unassigned:model.groupFor('2','draft:draft-unassigned')?.name},{identity:'draft:draft-one',group:'Project',nativeIdentityGroup:undefined,unsent:'Other chats',unassigned:'Other chats'});
});
