'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const ts=require('../../../vscode/node_modules/typescript');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../../vscode/src/vs/workbench/browser/parts/editor/libraryTabGroupModel.ts'),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const box={exports:{}};vm.runInNewContext(js,box);const {TabGroupModel}=box.exports;
test('membership is unique and persists with collapse and names',()=>{
 const m=new TabGroupModel();m.assign('pane','one','Research',['chat:a','file:x']);m.assign('pane','two','Other',['file:x']);m.toggle('pane','one');
 const restored=new TabGroupModel(m.serialize());
 assert.equal(restored.groupFor('pane','chat:a').name,'Research');assert.equal(restored.groupFor('pane','chat:a').collapsed,true);assert.equal(restored.groupFor('pane','file:x').name,'Other');
});
test('removing a member leaves unrelated panes and groups intact',()=>{
 const m=new TabGroupModel();m.assign('1','a','A',['x','y']);m.assign('2','b','B',['x']);m.remove('1',['x']);
 assert.equal(m.groupFor('1','x'),undefined);assert.equal(m.groupFor('1','y').name,'A');assert.equal(m.groupFor('2','x').name,'B');
});
test('automatic regroup replaces stale project membership while retaining manual groups and collapse',()=>{
 const m=new TabGroupModel();m.assign('1','manual','Manual',['m']);m.assign('1','project:p','P',['chat:a','file:x']);m.toggle('1','project:p');
 m.replaceAutomatic('1',[{id:'project:p',name:'P',keys:['chat:a']}]);
 assert.equal(m.groupFor('1','file:x'),undefined);assert.equal(m.groupFor('1','chat:a').collapsed,true);assert.equal(m.groupFor('1','m').name,'Manual');
});
test('moving a tab transfers its named group to the destination pane',()=>{
 const m=new TabGroupModel();m.assign('1','group','Research',['x','y']);m.toggle('1','group');m.transfer('1','2','x');
 assert.equal(m.groupFor('1','x'),undefined);assert.equal(m.groupFor('1','y').name,'Research');assert.equal(m.groupFor('2','x').name,'Research');assert.equal(m.groupFor('2','x').collapsed,true);
});
test('dragging out ungroups a member and inserting between members joins the group',()=>{
 const m=new TabGroupModel();m.assign('1','g','G',['a','b']);
 assert.equal(m.reconcileMove('1','b',['a','x','b']),true);assert.equal(m.groupFor('1','b'),undefined);
 m.assign('1','g','G',['b']);assert.equal(m.reconcileMove('1','x',['a','x','b']),true);assert.equal(m.groupFor('1','x').id,'g');
});
test('a new tab opened between members joins their group',()=>{
 const m=new TabGroupModel();m.assign('1','g','G',['a','b']);assert.equal(m.reconcileMove('1','x',['a','x','b'],false),true);assert.equal(m.groupFor('1','x').id,'g');
});
test('restoring the first member alone does not ungroup it',()=>{
 const m=new TabGroupModel();m.assign('1','g','G',['a','b']);assert.equal(m.reconcileMove('1','a',['a'],false),false);assert.equal(m.groupFor('1','a').id,'g');
});

test('automatic regroup never steals a manual member and keeps an automatic custom name',()=>{
 const m=new TabGroupModel();m.assign('1','manual','Keep',['x']);m.assign('1','project:p','My name',['y']);m.toggle('1','project:p');m.replaceAutomatic('1',[{id:'project:p',name:'Catalog name',keys:['x','y']}]);
 assert.equal(m.groupFor('1','x').id,'manual');assert.equal(m.groupFor('1','y').name,'My name');assert.equal(m.groupFor('1','y').collapsed,true);
});
