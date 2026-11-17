'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function load() {
 const src = fs.readFileSync(path.join(__dirname, '../extension.cjs'), 'utf8');
 const start = src.indexOf('async function choosePaneChat('), end = src.indexOf('\nfunction activate(', start);
 assert.ok(start >= 0 && end > start, 'pane chat picker must exist');
 const ctx = {}; vm.runInNewContext(src.slice(start, end) + '\nthis.choosePaneChat = choosePaneChat;', ctx); return ctx.choosePaneChat;
}
function fixture(script, saved='all') {
 const group={viewColumn:2},other={viewColumn:1}, opened=[],updates=[];let handlers={};
 const picker={items:[],selectedItems:[],value:'',buttons:[],onDidAccept(fn){handlers.accept=fn},onDidHide(fn){handlers.hide=fn},onDidTriggerButton(fn){handlers.button=fn},show(){script({picker,handlers,group,other,vscode,updates})},hide(){handlers.hide()},dispose(){this.disposed=true}};
 const vscode={ThemeIcon:class{constructor(id){this.id=id}},window:{tabGroups:{activeTabGroup:group,all:[group,other]},createQuickPick:()=>picker,showErrorMessage:()=>{}}};
 const storage={get:()=>saved,update:async(key,value)=>updates.push([key,value])};
 const api=async()=>({chats:[{key:'a',title:'Alpha',source:'claude'},{key:'b',title:'Beta',source:'codex'}],projects:{projects:[{id:'p',name:'Market'}],assignments:{a:'p'}}});
 return {choose:load(),vscode,storage,api,opened,updates,picker,group,other};
}
test('provider filter is persistent and keeps search text and captured pane',async()=>{
 let chosen;
 const f=fixture(({picker,handlers,vscode,other})=>{assert.equal(picker.matchOnDescription,true);assert.equal(picker.matchOnDetail,true);assert.match(picker.items[0].description,/Market/);picker.value='Beta';const button=picker.buttons.find(x=>x.tooltip.includes('Codex'));handlers.button(button);assert.equal(picker.value,'Beta');assert.deepEqual(Array.from(picker.items,x=>x.chat.key),['b']);assert.match(picker.prompt,/Codex/);vscode.window.tabGroups.activeTabGroup=other;picker.selectedItems=[picker.items[0]];handlers.accept();});
 await f.choose(f.vscode,f.api,(...args)=>{chosen=args},f.storage);
 assert.equal(chosen[0].key,'b');assert.equal(chosen[2],2);assert.deepEqual(f.updates,[['paneHistoryProvider','codex']]);assert.equal(f.picker.disposed,true);
});
test('stored provider is restored and current target column is used',async()=>{
 let selected;
 const f=fixture(({picker,handlers,group})=>{assert.deepEqual(Array.from(picker.items,x=>x.chat.key),['a']);assert.match(picker.prompt,/Claude/);group.viewColumn=3;picker.selectedItems=[picker.items[0]];handlers.accept();},'claude');
 await f.choose(f.vscode,f.api,(...args)=>{selected=args},f.storage);assert.equal(selected[2],3);
});
test('filter changes persist in order even when picker is cancelled',async()=>{
 const f=fixture(({picker,handlers})=>{handlers.button(picker.buttons.find(x=>x.tooltip==='Claude'));handlers.button(picker.buttons.find(x=>x.tooltip==='Codex'));handlers.button(picker.buttons.find(x=>x.tooltip==='All agents'));handlers.hide();});
 await f.choose(f.vscode,f.api,()=>assert.fail('cancel opened a chat'),f.storage);
 assert.deepEqual(f.updates,[['paneHistoryProvider','claude'],['paneHistoryProvider','codex'],['paneHistoryProvider','all']]);
});
test('cancel or removed target never opens an editor',async()=>{
 const f=fixture(({picker,handlers,vscode})=>{vscode.window.tabGroups.all=[];picker.selectedItems=[picker.items[0]];handlers.accept();});
 await f.choose(f.vscode,f.api,(...args)=>f.opened.push(args),f.storage);assert.equal(f.opened.length,0);
 const g=fixture(({handlers})=>handlers.hide());await g.choose(g.vscode,g.api,(...args)=>g.opened.push(args),g.storage);assert.equal(g.opened.length,0);assert.equal(g.picker.disposed,true);
});
test('project map picker routes selected project through native dedup and cancellation is inert', async () => {
 const src=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),start=src.indexOf('async function chooseProjectMap('),end=src.indexOf('\nfunction activate(',start);
 assert.ok(start>=0&&end>start);const ctx={};vm.runInNewContext(src.slice(start,end)+'\nthis.chooseProjectMap=chooseProjectMap;',ctx);
 const calls=[],group={viewColumn:1};let cancel=false;
 const vscode={window:{showQuickPick:async items=>cancel?undefined:items[0]}};
 const api=async()=>({projects:[{id:'market',name:'Market'}]});
 await ctx.chooseProjectMap(vscode,api,(...a)=>calls.push(a));
 assert.equal(calls[0][0].kind,'project-map');assert.equal(calls[0][0].project_id,'market');
 cancel=true;await ctx.chooseProjectMap(vscode,api,(...a)=>calls.push(a));assert.equal(calls.length,1);
});
test('history excludes subagent and automation chats like the workbench',async()=>{
 const f=fixture(({picker,handlers})=>{assert.deepEqual(Array.from(picker.items,x=>x.chat.key),['a','b']);handlers.hide();});
 const api=async()=>({chats:[{key:'a',title:'Alpha',source:'claude'},{key:'b',title:'Beta',source:'codex',root_key:'b'},{key:'c',is_child:true},{key:'d',parent_key:'a'},{key:'e',parent_agent_id:'x'},{key:'f',parent_session_id:'x'},{key:'g',root_key:'a'},{key:'h',automation:{id:'daily'}}],projects:{}});
 await f.choose(f.vscode,api,()=>{},f.storage);
});
