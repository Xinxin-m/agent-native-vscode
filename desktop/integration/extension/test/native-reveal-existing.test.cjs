'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../../../vscode/node_modules/typescript');

function loadCommand() {
 const source = fs.readFileSync(path.join(__dirname, '../../../vscode/src/vs/workbench/browser/parts/editor/editorCommands.ts'), 'utf8');
 const marker = "CommandsRegistry.registerCommand('_library.revealExisting'";
 const start = source.indexOf(marker);
 assert.notEqual(start, -1, 'native reveal command must be registered');
 const commands = new Map();
 class WebviewInput { constructor(resource, providerId = 'library.conversation') { this.providerId = providerId; this.webview = { state: JSON.stringify({ resource }) }; } }
 const IEditorGroupsService = {}, IEditorService = {};
 const scope = { CommandsRegistry: { registerCommand: (id, fn) => commands.set(id, fn) }, WebviewInput, IEditorGroupsService, IEditorService, GroupsOrder: { GRID_APPEARANCE: 1 }, EditorsOrder: { SEQUENTIAL: 1 } };
 vm.runInNewContext(ts.transpileModule(source.slice(start), { compilerOptions: { module: ts.ModuleKind.None } }).outputText, scope);
 return { command: commands.get('_library.revealExisting'), WebviewInput, IEditorGroupsService, IEditorService };
}
function harness(resource) {
 const h = loadCommand(), input = new h.WebviewInput(resource);
 const groups = [{ id: 10, editors: [input] }, { id: 20, editors: [] }];
 for (const group of groups) {
  group.getEditors = () => group.editors;
  group.moveEditor = (editor, target) => { group.editors.splice(group.editors.indexOf(editor), 1); target.editors.push(editor); return true; };
 }
 const opens = [], groupService = { groups, getGroups: () => groups };
 const accessor = { get: token => token === h.IEditorGroupsService ? groupService : token === h.IEditorService ? { openEditor: async (...args) => { opens.push(args); return {}; } } : undefined };
 return { ...h, input, groups, opens, accessor };
}
test('restored settings and accounts are revealed without creating another editor', async () => {
 for (const kind of ['settings', 'accounts']) { const h = harness({ kind }); assert.equal(await h.command(h.accessor, { kind }), true); assert.equal(h.opens.length, 1); assert.equal(h.opens[0][0], h.input); assert.equal(h.opens[0][2], 10); }
});
test('chat key and draft identity are exact, and requested column moves the existing editor', async () => {
 const h = harness({ kind: 'chat', key: 'codex-1' });
 assert.equal(await h.command(h.accessor, { kind: 'chat', key: 'codex-2' }, 2), false);
 assert.equal(await h.command(h.accessor, { kind: 'chat', key: 'codex-1' }, 2), true);
 assert.equal(h.groups[1].editors[0], h.input); assert.equal(h.opens[0][2], 20);
 const d = harness({ kind: 'chat', draftId: 'draft-a' });
 assert.equal(await d.command(d.accessor, { kind: 'chat', draftId: 'draft-b' }), false);
 assert.equal(await d.command(d.accessor, { kind: 'chat', draftId: 'draft-a' }), true);
});
test('other extensions and invalid state are never mistaken for Library tabs', async () => {
 const h = harness({ kind: 'settings' }); h.input.providerId = 'other.extension';
 assert.equal(await h.command(h.accessor, { kind: 'settings' }), false);
 h.input.providerId = 'library.conversation'; h.input.webview.state = '{';
 assert.equal(await h.command(h.accessor, { kind: 'settings' }), false);
});
test('a submitted draft is revealed by native history key while other drafts remain distinct', async () => {
 const h = harness({ kind: 'chat', draftId: 'draft-a', meta: { nativeKey: 'claude-real' } });
 assert.equal(await h.command(h.accessor, { kind: 'chat', draftId: 'draft-b' }), false);
 assert.equal(await h.command(h.accessor, { kind: 'chat', key: 'claude-real' }), true);
});
test('concurrent opens create only one panel after a shared lookup miss', async () => {
 const src = fs.readFileSync(path.join(__dirname, '../extension.cjs'), 'utf8');
 const start = src.indexOf(' async function open(resource,destination,explicitColumn){');
 const end = src.indexOf('\n function connect(', start);
 assert.ok(start >= 0 && end > start);
 let release; const barrier = new Promise(resolve => { release = resolve; });
 const panels = new Map(), created = [], revealed = [];
 const vscode = { Uri: { joinPath: () => ({}) }, window: { tabGroups: { all: [{ viewColumn: 1 }], activeTabGroup: { viewColumn: 1 } }, createWebviewPanel: () => { const panel = { reveal: column => revealed.push(column) }; created.push(panel); return panel; } }, commands: { executeCommand: async () => { await barrier; return false; } }, ThemeIcon: class {} };
 const scope = { preferenceWrites:{settled:Promise.resolve()},utilityDestination:require('../panes.cjs').utilityDestination, vscode, panels, preferences: { maxAutoPanes: 3,utilityPopups:false }, targetColumn: () => 1, resourceKey: r => r.kind + ':', revealExisting: (...args) => vscode.commands.executeCommand('_library.revealExisting', ...args.slice(1)), context: { extensionUri: {} }, connect() {} };
 vm.runInNewContext(src.slice(start, end) + '\nthis.open = open;', scope);
 const first = scope.open({ kind: 'settings' }), second = scope.open({ kind: 'settings' });
 release(); await Promise.all([first, second]);
 assert.equal(created.length, 1); assert.equal(revealed.length, 1);
});
test('restored overview and project maps match only their own provider and project', async () => {
 const overview = harness({ kind: 'overview' }); assert.equal(await overview.command(overview.accessor, { kind: 'overview' }), true);
 const map = harness({ kind: 'project-map', project_id: 'market' }); map.input.providerId = 'library.projectMap';
 assert.equal(await map.command(map.accessor, { kind: 'project-map', project_id: 'other' }), false);
 assert.equal(await map.command(map.accessor, { kind: 'project-map', project_id: 'market' }), true);
});
test('file opening uses the configured destination only when one is not supplied', async () => {
 const src = fs.readFileSync(path.join(__dirname, '../extension.cjs'), 'utf8');
 const start = src.indexOf(' async function open(resource,destination,explicitColumn){'), end = src.indexOf('\n function connect(', start);
 const calls = [], group = { viewColumn: 1 };
 const folder={uri:{scheme:'file',fsPath:'/tmp',toString:()=> 'file:///tmp'},name:'tmp'};
 const vscode = { FileType:{Directory:2},workspace:{getWorkspaceFolder:()=>folder,fs:{stat:async()=>({type:1})}},window: { tabGroups: { all: [group], activeTabGroup: group } }, Uri: { file: path => ({ path,fsPath:path,scheme:'file' }) }, commands: { executeCommand: async (...args) => { calls.push(args); } } };
 const scope = { preferenceWrites:{settled:Promise.resolve()},utilityDestination:require('../panes.cjs').utilityDestination, vscode,require:id=>{assert.equal(id,'./file-context.cjs');return require('../file-context.cjs');},filesTab:{rootId:uri=>uri.toString()}, fs: { stat: async () => ({ isFile: () => true }) }, path, preferences: { maxAutoPanes: 3, openDefault: 'right' }, targetColumn: () => 2, besideFileColumn:require('../panes.cjs').besideFileColumn, panels: new Map() };
 vm.runInNewContext(src.slice(start, end) + '\nthis.open = open;', scope);
 await scope.open({ kind: 'file', path: '/tmp/a.md' });
 assert.deepEqual(calls.map(c=>c[0]),['_library.besideFileColumn','workbench.action.newGroupRight','vscode.open']);
 calls.length = 0; await scope.open({ kind: 'file', path: '/tmp/a.md' }, 'tab');
 assert.deepEqual(calls.map(call => call[0]), ['vscode.open']);
});
test('older installed core falls back only when the native reveal command is absent', async () => {
 const src=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'), start=src.indexOf('async function revealExisting('), end=src.indexOf('\nasync function choosePaneChat(',start);
 assert.ok(start>=0&&end>start);const ctx={};vm.runInNewContext(src.slice(start,end)+'\nthis.revealExisting=revealExisting;',ctx);
 let vscode={commands:{executeCommand:async()=>{throw Error("command '_library.revealExisting' not found")}}};
 assert.equal(await ctx.revealExisting(vscode,{kind:'settings'}),false);
 vscode={commands:{executeCommand:async()=>{throw Error('editor restore failed')}}};
 await assert.rejects(ctx.revealExisting(vscode,{kind:'settings'}),/editor restore failed/);
});
