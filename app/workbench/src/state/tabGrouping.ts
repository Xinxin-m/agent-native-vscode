import type {Resource} from './types';
import {planPaneArrangement} from './paneArrangement';
export {planPaneArrangement} from './paneArrangement';
export function resourceTabBucket(resource:Resource,mode:'provider'|'project',includeFiles:boolean,state:any,projects:any[]):{id:string;label:string}|undefined {
 const chat=(state.chats||[]).find((item:any)=>item.key===resource.key)||resource.meta||{};
 const provider=String(chat.source||chat.provider||resource.meta?.provider||resource.key?.split('-')[0]||'').toLowerCase();
 if(mode==='project'&&(resource.kind==='chat'||(resource.kind==='file'&&includeFiles))){
  let projectId=resource.kind==='chat'?(state.projects?.assignments||{})[resource.key||resource.meta?.nativeKey||'']:undefined;
  const path=resource.kind==='file'?resource.path:chat.cwd||resource.meta?.cwd,host=resource.host_id||chat.host_id||chat.node||'mac';let longest=-1;
  if(!projectId&&path)for(const project of projects)for(const mapping of project.paths||project.workspace_mappings||[]){const root=String(mapping.path||'').replace(/\/$/,'');if(root&&(host===mapping.host||(['mac','local'].includes(host)&&['mac','local'].includes(mapping.host)))&&(path===root||path.startsWith(root+'/'))&&root.length>longest){projectId=project.id;longest=root.length;}}
  const project=projects.find((item:any)=>item.id===projectId);if(project)return {id:'project:'+project.id,label:project.display_name||project.name||project.id};
 }
 if(resource.kind==='file'&&(mode==='provider'||includeFiles))return {id:'files',label:'Files'};
 if(resource.kind==='chat')return {id:'provider:'+(provider||'other'),label:provider==='codex'?'Codex':provider==='claude'?'Claude':'Other chats'};
}
export function arrangeWorkspaceTabs(dock:any,mode:'provider'|'project',includeFiles:boolean,state:any,projects:any[]){
 const panes=[...dock.groups],active=dock.activePanel,records:any[]=[],saved=new Map<string,any>();
 for(const pane of panes){
  for(const group of dock.getTabGroups({groupId:pane.id}))if(group.componentParams?.libraryAutomatic)saved.set(pane.id+':'+group.componentParams.libraryKey,{label:group.label,color:group.color,collapsed:group.collapsed});
  for(const panel of pane.panels){const group=dock.getTabGroupForPanel({groupId:pane.id,panelId:panel.id});if(group&&!group.componentParams?.libraryAutomatic)continue;const bucket=resourceTabBucket(panel.params.resource,mode,includeFiles,state,projects);if(bucket)records.push({panel,pane:pane.id,bucket:bucket.id,label:bucket.label});}
 }
 const targets=planPaneArrangement(panes.map(pane=>pane.id),records);
 dock.updateOptions({closeEmptyGroups:false});
 try{
  for(const record of records){const target=panes.find(pane=>pane.id===targets.get(record.bucket));if(target&&record.panel.group.id!==target.id)record.panel.api.moveTo({group:target,position:'center',skipSetActive:true});}
  for(const pane of panes){
   const buckets=new Map<string,any>();for(const record of records)if(record.panel.group.id===pane.id){const bucket=buckets.get(record.bucket)||{label:record.label,panels:[]};bucket.panels.push(record.panel);buckets.set(record.bucket,bucket);}
   const existing=[...dock.getTabGroups({groupId:pane.id})];
   for(const group of existing)if(group.componentParams?.libraryAutomatic&&!buckets.has(String(group.componentParams.libraryKey)))dock.dissolveTabGroup({groupId:pane.id,tabGroupId:group.id});
   for(const [key,bucket] of buckets){let group=dock.getTabGroups({groupId:pane.id}).find((g:any)=>g.componentParams?.libraryAutomatic&&g.componentParams.libraryKey===key);const previous=saved.get(pane.id+':'+key)||[...saved].find(([id])=>id.endsWith(':'+key))?.[1];if(!group)group=dock.createTabGroup({groupId:pane.id,label:previous?.label||bucket.label,color:previous?.color,componentParams:{libraryAutomatic:mode,libraryKey:key}});for(const panel of bucket.panels)dock.addPanelToTabGroup({groupId:pane.id,tabGroupId:group.id,panelId:panel.id});if(previous?.collapsed&&!group.collapsed)group.collapse();}
  }
  active?.api.setActive();
 }finally{dock.updateOptions({closeEmptyGroups:undefined});}
}
