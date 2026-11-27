/* Named groups within the native editor tab strip. */
import { Emitter } from '../../../../base/common/event.js';
import { generateUuid } from '../../../../base/common/uuid.js';
import { CommandsRegistry } from '../../../../platform/commands/common/commands.js';
import { IQuickInputService } from '../../../../platform/quickinput/common/quickInput.js';
import { IStorageService, StorageScope, StorageTarget } from '../../../../platform/storage/common/storage.js';
import { EditorsOrder } from '../../../common/editor.js';
import { EditorInput } from '../../../common/editor/editorInput.js';
import { CustomEditorInput } from '../../../contrib/customEditor/browser/customEditorInput.js';
import { WebviewInput } from '../../../contrib/webviewPanel/browser/webviewEditorInput.js';
import { IEditorGroupsService, IEditorGroup, GroupsOrder } from '../../../services/editor/common/editorGroupsService.js';
import { libraryChatIdentity } from './libraryChatIdentity.js';
import { TabGroupModel } from './libraryTabGroupModel.js';
import { planPaneArrangement } from './libraryPaneArrangement.js';

const STORAGE_KEY = 'library.namedTabGroups';
let model: TabGroupModel | undefined;
const changed = new Emitter<void>();
export const onDidChangeLibraryTabGroups = changed.event;
export function tabGroups(storage: IStorageService): TabGroupModel {
 return model ??= new TabGroupModel(storage.get(STORAGE_KEY, StorageScope.WORKSPACE));
}
export function saveTabGroups(storage: IStorageService): void {
 storage.store(STORAGE_KEY, tabGroups(storage).serialize(), StorageScope.WORKSPACE, StorageTarget.MACHINE); changed.fire();
}
export function tabIdentity(editor: EditorInput): string | undefined {
 const chat = libraryChatIdentity(editor);
 if (chat.key) return `chat:${chat.key}`;
 if (editor instanceof CustomEditorInput) {
  // An official Codex panel is a chat, keyed like the Library chat of the same thread.
  if (editor.resource.scheme === 'openai-codex') { const thread = /^\/local\/([\w-]+)$/.exec(editor.resource.path)?.[1]; return thread ? `chat:codex-${thread}` : undefined; }
  return `file:${editor.resource.toString()}`;
 }
 if (editor instanceof WebviewInput) {
  try {
   const state = JSON.parse(editor.webview.state ?? '{}');
   if (editor.providerId === 'claudeVSCodePanel') return typeof state.sessionID === 'string' && state.sessionID ? `chat:claude-${state.sessionID}` : undefined;
   const resource = state.resource;
   if (resource?.kind === 'chat') return resource.key ? `chat:${resource.key}` : resource.draftId ? `draft:${resource.draftId}` : undefined;
  } catch { return undefined; }
  return undefined;
 }
 return editor.resource ? `file:${editor.resource.toString()}` : undefined;
}
let arranging = false;
export function reconcileTabMove(storage: IStorageService, group: IEditorGroup, editor: EditorInput, allowRemove = true): void {
 if (arranging) return;
 const key = tabIdentity(editor);
 if (key && tabGroups(storage).reconcileMove(String(group.id), key, group.getEditors(EditorsOrder.SEQUENTIAL).map(tabIdentity), allowRemove)) saveTabGroups(storage);
}
function makeContiguous(group: IEditorGroup, editors: EditorInput[]): void {
 const all = group.getEditors(EditorsOrder.SEQUENTIAL); const selected = all.filter(editor => editors.includes(editor));
 if (!selected.length) return;
 const start = all.indexOf(selected[0]);
 arranging = true;
 try { for (let offset = 0; offset < selected.length; offset++) group.moveEditor(selected[offset], group, {index:start + offset, preserveFocus:true}); }
 finally { arranging = false; }
}
CommandsRegistry.registerCommand('_library.refreshTabGroups', accessor => {
 const groups = accessor.get(IEditorGroupsService); const storage = accessor.get(IStorageService); const model = tabGroups(storage); let modified = false;
 for (const pane of groups.groups) {
  const keys = pane.getEditors(EditorsOrder.SEQUENTIAL).map(tabIdentity);
  for (const key of keys) if (key && model.reconcileMove(String(pane.id), key, keys, false)) modified = true;
 }
 if (modified) saveTabGroups(storage); else changed.fire();
});
interface TabContext {groupId:number; editorIndex:number}
function groupSelection(groups: IEditorGroupsService, context?: TabContext) {
 const group=context ? groups.getGroup(context.groupId) : groups.activeGroup;
 if (!group) return;
 const clicked=context && group.getEditorByIndex(context.editorIndex);
 if (context && !clicked) return;
 return {group,editors:clicked && !group.selectedEditors.includes(clicked) ? [clicked] : [...group.selectedEditors]};
}
CommandsRegistry.registerCommand('_library.createTabGroup', async (accessor, context?: TabContext) => {
 const selection=groupSelection(accessor.get(IEditorGroupsService), context);
 if (!selection) return;
 const group = selection.group; const quick = accessor.get(IQuickInputService); const storage = accessor.get(IStorageService);
 const editors = group.getEditors(EditorsOrder.SEQUENTIAL).filter(editor => !!tabIdentity(editor));
 const picks = editors.map(editor => ({label:editor.getName(), description:editor.getDescription(), picked:selection.editors.includes(editor), editor}));
 const selected = await quick.pick(picks, {canPickMany:true, title:'Select tabs for a new group'});
 if (!selected?.length) return;
 const name = (await quick.input({title:'Name tab group', validateInput:async value => value.trim() ? undefined : 'Enter a name'}))?.trim();
 if (!name) return;
 const chosen = selected.map(item => item.editor); makeContiguous(group, chosen);
 tabGroups(storage).assign(String(group.id), generateUuid(), name, chosen.map(editor => tabIdentity(editor)!).filter(Boolean)); saveTabGroups(storage);
});
CommandsRegistry.registerCommand('_library.addTabToGroup', async (accessor, context?: TabContext) => {
 const groups = accessor.get(IEditorGroupsService);
 const quick = accessor.get(IQuickInputService);
 const storage = accessor.get(IStorageService);
 const selection = groupSelection(groups, context);
 if (!selection) return;
 const model = tabGroups(storage);
 const options = groups.getGroups(GroupsOrder.GRID_APPEARANCE).flatMap((pane,index) =>
  model.groups(String(pane.id)).map(group => ({label:group.name,description:`Pane ${index+1}`,group,pane})));
 const selected = await quick.pick(options, {title:'Add selected tabs to a group'});
 if (!selected) return;
 const source = selection.group, target = selected.pane;
 for (const editor of [...selection.editors]) {
  if (source !== target && source.contains(editor)) await source.moveEditor(editor,target,{pinned:true,preserveFocus:true});
 }
 const keys = selection.editors.filter(editor => target.contains(editor)).map(tabIdentity).filter((key): key is string => !!key);
 if (!keys.length) return;
 const members = new Set([...selected.group.keys, ...keys]);
 const editors = target.getEditors(EditorsOrder.SEQUENTIAL).filter(editor => { const key = tabIdentity(editor); return !!key && members.has(key); });
 makeContiguous(target, editors); model.assign(String(target.id), selected.group.id, selected.group.name, keys); saveTabGroups(storage);
});
CommandsRegistry.registerCommand('_library.removeTabFromGroup', (accessor, context?: TabContext) => {
 const selection=groupSelection(accessor.get(IEditorGroupsService), context);
 if (!selection) return;
 const group = selection.group; const storage = accessor.get(IStorageService);
 const keys = selection.editors.map(tabIdentity).filter((key): key is string => !!key);
 tabGroups(storage).remove(String(group.id), keys); saveTabGroups(storage);
});

interface ProjectGrouping { assignments: Record<string, string>; projects: { id: string; name: string; prefixes: string[] }[]; providers?: Record<string,string>; includeFiles: boolean }
CommandsRegistry.registerCommand('_library.groupTabsByProject', async (accessor, data: ProjectGrouping) => {
 if (!data || !Array.isArray(data.projects) || !data.assignments) return;
 const groups = accessor.get(IEditorGroupsService); const storage = accessor.get(IStorageService); const model = tabGroups(storage);
 const panes = groups.getGroups(GroupsOrder.GRID_APPEARANCE); // Grid appearance keeps routing stable across reruns.
 const records: {editor:EditorInput; pane:number; bucket:string; label:string}[] = [];
 for (const pane of panes) for (const editor of pane.getEditors(EditorsOrder.SEQUENTIAL)) {
  const key=tabIdentity(editor);if(!key)continue;
  const own=model.groupFor(String(pane.id),key);if(own&&!own.id.startsWith('project:'))continue;
  let projectId:string|undefined, provider:string|undefined;
  if(editor instanceof WebviewInput){try{const resource=JSON.parse(editor.webview.state ?? '{}').resource;const chatKey=resource?.key||resource?.meta?.nativeKey;projectId=data.assignments[chatKey];provider=data.providers?.[chatKey]||resource?.meta?.source||resource?.meta?.provider||chatKey?.split('-')[0];}catch{/* Unrestored webviews are left in place. */}}
  if(!projectId&&!provider&&key.startsWith('chat:')){const chatKey=key.slice(5);projectId=data.assignments[chatKey];provider=data.providers?.[chatKey]||chatKey.split('-')[0];}
  if(data.includeFiles&&key.startsWith('file:')){const uri=key.slice(5);let longest=-1;for(const project of data.projects)for(const prefix of project.prefixes||[]){const root=prefix.replace(/\/$/,'');if((uri===root||uri.startsWith(root+'/'))&&root.length>longest){projectId=project.id;longest=root.length;}}}
  const project=data.projects.find(item=>item.id===projectId);
  if(project)records.push({editor,pane:pane.id,bucket:project.id,label:project.name});
  else if(key.startsWith('chat:')||key.startsWith('draft:'))records.push({editor,pane:pane.id,bucket:`provider:${provider||'other'}`,label:provider==='codex'?'Codex':provider==='claude'?'Claude':'Other chats'});
  else if(data.includeFiles&&key.startsWith('file:'))records.push({editor,pane:pane.id,bucket:'files',label:'Files'});
 }
 const targets=planPaneArrangement(panes.map(pane=>pane.id),records),active=groups.activeGroup,options=groups.enforcePartOptions({closeEmptyGroups:false});
 arranging=true;
 try{
  for(const record of records){const source=groups.groups.find(pane=>pane.contains(record.editor)),target=groups.getGroup(targets.get(record.bucket)!);if(source&&target&&source!==target){const moved=await source.moveEditor(record.editor,target,{preserveFocus:true,pinned:true});if(moved){const key=tabIdentity(record.editor);if(key)model.transfer(String(source.id),String(target.id),key);}}}
  for(const pane of panes){
   const buckets=new Map<string,{name:string;editors:EditorInput[]}>();for(const record of records)if(pane.contains(record.editor)){const bucket=buckets.get(record.bucket)||{name:record.label,editors:[]};bucket.editors.push(record.editor);buckets.set(record.bucket,bucket);}
   const replacements:{id:string;name:string;keys:string[]}[]=[];
   for(const [id,bucket]of buckets){const all=pane.getEditors(EditorsOrder.SEQUENTIAL),selected=all.filter(editor=>bucket.editors.includes(editor)),start=all.indexOf(selected[0]);for(let index=0;index<selected.length;index++)pane.moveEditor(selected[index],pane,{index:start+index,preserveFocus:true});replacements.push({id:`project:${id}`,name:bucket.name,keys:bucket.editors.map(editor=>tabIdentity(editor)!).filter(Boolean)});}
   model.replaceAutomatic(String(pane.id),replacements);
  }
  active.focus();saveTabGroups(storage);
 }finally{arranging=false;options.dispose();}
});
