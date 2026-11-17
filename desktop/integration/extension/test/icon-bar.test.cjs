"use strict";
const test=require('node:test');const assert=require('node:assert/strict');
const iconBar=require('../icon-bar.cjs');
const pkg=require('../package.json');

// A configuration store that behaves like VS Code's: values persist and listeners hear changes.
function fakeVscode(initial={}){
 const values={...initial},listeners=[],updates=[];
 const vscode={
  ConfigurationTarget:{Global:1},
  window:{showErrorMessage(){}},
  workspace:{
   getConfiguration:section=>({get:key=>values[section+'.'+key],update:async(key,value)=>{updates.push([section+'.'+key,value]);values[section+'.'+key]=value;for(const l of listeners)l({affectsConfiguration:k=>k===section+'.'+key||(section+'.'+key).startsWith(k+'.')});}}),
   onDidChangeConfiguration:l=>{listeners.push(l);return {dispose(){}};},
  },
 };
 return {vscode,values,updates};
}

test('contributes library.iconBar with three modes, visible by default',()=>{
 const setting=pkg.contributes.configuration.properties['library.iconBar'];
 assert.deepEqual(setting.enum,['visible','autoHide','hidden']);
 assert.equal(setting.default,'visible');
});

test('each mode maps to the activity bar location',async()=>{
 for(const [mode,expected] of [['visible','default'],['autoHide','default'],['hidden','hidden'],[undefined,'default'],['bogus','default']]){
  const {vscode,values}=fakeVscode({'library.iconBar':mode,'workbench.activityBar.location':'top'});
  await iconBar.apply(vscode);
  assert.equal(values['workbench.activityBar.location'],expected,String(mode));
 }
});

test('changing the setting applies at once and persists in configuration',async()=>{
 const {vscode,values,updates}=fakeVscode({'library.iconBar':'visible','workbench.activityBar.location':'default'});
 const context={subscriptions:[]};
 iconBar.register(vscode,context);
 await vscode.workspace.getConfiguration('library').update('iconBar','hidden');
 await new Promise(r=>setImmediate(r));
 assert.equal(values['workbench.activityBar.location'],'hidden');
 await vscode.workspace.getConfiguration('library').update('iconBar','autoHide');
 await new Promise(r=>setImmediate(r));
 assert.equal(values['workbench.activityBar.location'],'default');
 assert.equal(iconBar.mode(vscode),'autoHide');
 assert.ok(updates.some(([k,v])=>k==='library.iconBar'&&v==='autoHide'));
});

test('Settings and Accounts stay reachable by keyboard',()=>{
 const bound=new Set(pkg.contributes.keybindings.map(k=>k.command));
 for(const command of ['library.show','library.settings','library.accounts'])assert.ok(bound.has(command),command);
});
