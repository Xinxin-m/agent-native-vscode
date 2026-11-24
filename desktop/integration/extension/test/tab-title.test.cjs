'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {tabTitle,tabIcon,TAB_TITLE_MAX}=require('../tab-title.cjs');
const read=p=>fs.readFileSync(path.join(__dirname,p),'utf8');
test('chat tab title is the full title (tooltip); the core tab bar cuts the label to 24 characters plus an ellipsis',()=>{
 assert.equal(TAB_TITLE_MAX,24);
 assert.equal(tabTitle('x'.repeat(40)),'x'.repeat(40));
 assert.equal(tabTitle('z'.repeat(200)).length,150);
 assert.equal(tabTitle('  Fix   the\nsidebar  '),'Fix the sidebar');
 assert.equal(tabTitle(''),'New chat');assert.equal(tabTitle(undefined),'New chat');
 const core=read('../../../vscode/src/vs/workbench/browser/parts/editor/multiEditorTabsControl.ts');
 assert.match(core,/name\.length > 24 && editor\.editorId === 'mainThreadWebview-library\.conversation'\) \{\n\t\t\tname = name\.slice\(0, 24\)\.trimEnd\(\) \+ '\\u2026';/);
 assert.match(core,/title: this\.getHoverTitle\(editor\)/,'tooltip keeps the full title');
});
test('a chat tab icon is its status mark; no status keeps the chat icon',()=>{
 const vscode={ThemeIcon:class{constructor(id,color){this.id=id;this.color=color?.id;}},ThemeColor:class{constructor(id){this.id=id;}}};
 assert.deepEqual(['waiting','working','new','failed','live',null].map(s=>{const i=tabIcon(vscode,s);return [i.id,i.color];}),[['circle-filled','charts.blue'],['loading~spin','charts.blue'],['circle-filled','charts.green'],['circle-filled','charts.red'],['comment-discussion',undefined],['comment-discussion',undefined]]);
 assert.match(read('../extension.cjs'),/if\(kind==='chat'&&'iconPath' in view&&'status' in m\)view\.iconPath=tabIcon\(vscode,m\.status\)/);
 assert.match(read('../../ui/src/native/main.tsx'),/status=rowStatus\(activity\.live,activity\.unread,\{\.\.\.activity,failed:activity\.failed\|\|activity\.recovery\}\);useEffect\(\(\)=>\{if\(key\)void rpc\('title',\{status\}\)/);
});
test('every place that names a Library chat tab uses tabTitle',()=>{
 const host=read('../extension.cjs');
 assert.match(host,/createWebviewPanel\('library\.conversation',resource\.kind==='chat'\?tabTitle\(resource\.title/);
 assert.match(host,/m\.action==='title'\)\{if\('title' in view&&typeof m\.title==='string'\)view\.title=kind==='chat'\?tabTitle\(m\.title\)/);
 assert.equal((host.match(/view\.title=tabTitle\(next\.title\)/g)||[]).length,2,'history pick and agent tree replace');
 assert.doesNotMatch(host,/view\.title=next\.title/);
});
test('a new chat tab is "New chat" and the first message never becomes its title',()=>{
 const host=read('../extension.cjs'),conv=read('../../ui/src/chat/Conversation.tsx');
 assert.doesNotMatch(host,/New conversation/);
 assert.match(host,/createWebviewPanel\('library\.conversation',resource\.kind==='chat'\?tabTitle\(resource\.title\):/);
 assert.doesNotMatch(conv,/updateTitle\?\.\(resource,\(result\.run\.message/);
 assert.doesNotMatch(conv,/title_seed\)wb\.updateTitle/);
 assert.match(conv,/if\(value\.title\)wb\.updateTitle\?\.\(resource,value\.title\)/);
});
test('an open chat tab follows retitling: it re-sends the server title whenever it changes',()=>{
 const ui=read('../../ui/src/native/main.tsx');
 assert.match(ui,/function ChatTabSync\(\)/);
 assert.match(ui,/useEffect\(\(\)=>\{if\(title\)wb\.updateTitle\?\.\(boot\.resource,title\);\},\[title\]\)/);
 assert.match(ui,/boot\.kind==='chat'&&<ChatTabSync\/>/);
});
test('the seen time is reported through the bridge without needing workspace trust',()=>{
 const host=read('../extension.cjs');
 assert.match(host,/inboxAction=.*m\.path==='\/api\/seen'/);
});
