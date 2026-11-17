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
CommandsRegistry.registerCommand('_library.createTabGroup', async accessor => {
 const group = accessor.get(IEditorGroupsService).activeGroup; const quick = accessor.get(IQuickInputService); const storage = accessor.get(IStorageService);
 const editors = group.getEditors(EditorsOrder.SEQUENTIAL).filter(editor => !!tabIdentity(editor));
 const picks = editors.map(editor => ({label:editor.getName(), description:editor.getDescription(), picked:group.selectedEditors.includes(editor), editor}));
 const selected = await quick.pick(picks, {canPickMany:true, title:'Select tabs for a new group'});
 if (!selected?.length) return;
 const name = (await quick.input({title:'Name tab group', validateInput:async value => value.trim() ? undefined : 'Enter a name'}))?.trim();
 if (!name) return;
 const chosen = selected.map(item => item.editor); makeContiguous(group, chosen);
 tabGroups(storage).assign(String(group.id), generateUuid(), name, chosen.map(editor => tabIdentity(editor)!).filter(Boolean)); saveTabGroups(storage);
});
CommandsRegistry.registerCommand('_library.addTabToGroup', async accessor => {
 const pane = accessor.get(IEditorGroupsService).activeGroup; const quick = accessor.get(IQuickInputService); const storage = accessor.get(IStorageService);
 const model = tabGroups(storage); const options = model.groups(String(pane.id)).map(group => ({label:group.name, group}));
 const selected = await quick.pick(options, {title:'Add selected tabs to a group'}); if (!selected) return;
 const keys = pane.selectedEditors.map(tabIdentity).filter((key): key is string => !!key); if (!keys.length) return;
 const members = new Set([...selected.group.keys, ...keys]);
 const editors = pane.getEditors(EditorsOrder.SEQUENTIAL).filter(editor => { const key = tabIdentity(editor); return !!key && members.has(key); });
 makeContiguous(pane, editors); model.assign(String(pane.id), selected.group.id, selected.group.name, keys); saveTabGroups(storage);
});
CommandsRegistry.registerCommand('_library.removeTabFromGroup', accessor => {
 const group = accessor.get(IEditorGroupsService).activeGroup; const storage = accessor.get(IStorageService);
 const keys = group.selectedEditors.map(tabIdentity).filter((key): key is string => !!key);
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
