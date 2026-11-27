export function windowIdentity(navigation:string,requested:string|null,uuid:()=>string){const previous=requested&&/^[\w-]{1,100}$/.test(requested)?requested:undefined;return {id:navigation==='reload'&&previous?previous:uuid(),restoreId:previous||'main'};}
export const policyDurations=(current:number)=>[...new Set([12,24,72,168,...(Number.isFinite(current)&&current>0?[current]:[])])].sort((a,b)=>a-b);
export function relativeFilePath(path:string,root:string){const base=root.replace(/\/$/,'');if(path===base)return '.';if(!path.startsWith(base+'/'))throw new Error('The file is outside the selected folder.');return path.slice(base.length+1);}
export function createSerialWriter<T>(write:(value:T)=>Promise<any>,report:(state:'saving'|'saved'|'error',value?:any)=>void){let queued:T|undefined,pending:Promise<void>|undefined;async function drain(){while(queued!==undefined){const next=queued;queued=undefined;report('saving');try{const value=await write(next);if(queued===undefined)report('saved',value);}catch(e){if(queued===undefined)report('error',e);}}pending=undefined;}return {submit(value:T){queued=value;if(!pending)pending=drain();return pending;}};}

import type {Resource} from './types';
import {resourceId} from '../open/links';
import {conversationIdentity,conversations} from './conversations';
export const viewIdentity=(r:Resource)=>resourceId(r)+':'+(r.mode||'default');
interface PaneRecord {items:Resource[];index:number}
export function createPaneNavigation(saved:Record<string,PaneRecord>={}){
 const panes:Record<string,PaneRecord>={};for(const [key,value]of Object.entries(saved||{}))if(Array.isArray(value?.items)&&Number.isInteger(value.index))panes[key]={items:value.items.filter(r=>r?.kind),index:value.index};
 const can=(id:string,dir:number)=>!!panes[id]&&panes[id].index+dir>=0&&panes[id].index+dir<panes[id].items.length;
 return {visit(id:string,r:Resource){const p=panes[id]||(panes[id]={items:[],index:-1});if(p.items[p.index]&&viewIdentity(p.items[p.index])===viewIdentity(r))return;p.items=p.items.slice(0,p.index+1);p.items.push({...r});if(p.items.length>100)p.items.shift();p.index=p.items.length-1},can,step(id:string,dir:number){if(!can(id,dir))return;const p=panes[id];p.index+=dir;return p.items[p.index]},serialize:()=>panes,remove:(id:string)=>{delete panes[id]}};
}
interface GroupPosition {id:string;rect:{left:number;right:number;top:number;bottom:number}}
export function adjacentPreviewGroup(groups:GroupPosition[],sourceId:string,direction:'right'|'below',remembered?:string){
 if(remembered&&remembered!==sourceId&&groups.some(g=>g.id===remembered))return remembered;
 const source=groups.find(g=>g.id===sourceId);if(!source)return;
 const horizontal=direction==='right',r=source.rect;
 return groups.filter(g=>{if(g.id===sourceId)return false;const b=g.rect;return horizontal?b.left>=r.right-3&&Math.min(r.bottom,b.bottom)>Math.max(r.top,b.top):b.top>=r.bottom-3&&Math.min(r.right,b.right)>Math.max(r.left,b.left)}).sort((a,b)=>horizontal?(a.rect.left-r.right)-(b.rect.left-r.right)||Math.abs(a.rect.top-r.top)-Math.abs(b.rect.top-r.top):(a.rect.top-r.bottom)-(b.rect.top-r.bottom)||Math.abs(a.rect.left-r.left)-Math.abs(b.rect.left-r.left))[0]?.id;
}
export interface WindowTransfer {request_id:string;target:string;resource:Resource;panelId?:string;move:boolean;placement?:{groupId?:string;position:string;index?:number}}
export function createWindowTransfers(source:string,send:(message:any)=>void,accepted:(transfer:WindowTransfer)=>void,failed:(error:string)=>void,uuid:()=>string=()=>crypto.randomUUID(),timeout=6000){
 const pending=new Map<string,{transfer:WindowTransfer;timer:ReturnType<typeof setTimeout>}>();
 return {offer(target:string,resource:Resource,panelId?:string,move=false,placement?:WindowTransfer['placement']){const request_id=uuid(),transfer={request_id,target,resource:{...resource},panelId,move,placement};const timer=setTimeout(()=>{pending.delete(request_id);failed('The other window did not confirm opening the tab. The source tab is retained.')},timeout);pending.set(request_id,{transfer,timer});try{send({type:'open',id:source,target,resource,request_id,move,placement})}catch(e){clearTimeout(timer);pending.delete(request_id);failed((e as Error).message)}return request_id},ack(message:any){const value=pending.get(message.request_id);if(!value||message.id!==value.transfer.target||message.target!==source)return false;clearTimeout(value.timer);pending.delete(message.request_id);if(message.ok)accepted(value.transfer);else failed(message.error||'The other window could not open the tab. The source tab is retained.');return true},dispose(){for(const {timer}of pending.values())clearTimeout(timer);pending.clear()}};
}
export function createNarrowPaneController(dock:any){let narrow=false,shown:string|undefined,previous:string|undefined;return {sync(value:boolean){if(value===narrow)return;narrow=value;if(value){previous=dock.hasMaximizedGroup()?dock.activePanel?.id:undefined;shown=undefined;this.activate(dock.activePanel)}else{shown=undefined;const original=previous&&dock.getPanel(previous);if(original)dock.maximizeGroup(original);else if(dock.hasMaximizedGroup())dock.exitMaximizedGroup()}},activate(panel:any){if(narrow&&panel&&shown!==panel.group.id){shown=panel.group.id;dock.maximizeGroup(panel)}}};}

/** A conversation has one live view per workspace, so its composer has one owner. */
export function findOpenChat(panels:any[],resource:Resource){return resource.kind==='chat'?panels.find(p=>p.params.resource.kind==='chat'&&conversationIdentity(p.params.resource)===conversationIdentity(resource)):undefined;}
export function dropWorkspaceResource(dock:any,open:(r:Resource,destination?:any,groupId?:string)=>any,resource:Resource,event:{group?:any;position:string;index?:number}){
 const group=event.group, panel=open(resource,'tab',group?.id);if(!panel)return;
 // Dockview's panel API needs a group to honor the workspace-edge split direction.
 panel.api.moveTo({group:group||panel.group,position:event.position,index:event.index});
 panel.api.setActive();return panel;
}
export function mergePaneInto(source:any,target:any,index?:number){if(!source||!target||source.id===target.id)return;for(const panel of [...source.panels]){panel.api.moveTo({group:target,position:'center',index});if(index!==undefined)index++;}}

export function conversationTransferSnapshot(value:any){const {text,attachments,answerDrafts,pending,provider,model,effort,account,host_id,cwd,cwdByHost,executionOverrides,nativeKey,nativeProvider,runId,sent}=value;return JSON.stringify({text,attachments,answerDrafts,pending,provider,model,effort,account,host_id,cwd,cwdByHost,executionOverrides,nativeKey,nativeProvider,runId,sent});}
export function acceptTransferredConversation(resource:Resource,incoming:any,store=conversations,storage:Pick<Storage,'getItem'|'setItem'>=localStorage){
 const id=conversationIdentity(resource);if(!incoming||typeof incoming.text!=='string'||!Array.isArray(incoming.attachments))throw Error('The conversation draft transfer is invalid.');
 const recoveryKey='library.workbench.conversation-transfer.'+id+':'+crypto.randomUUID(),recovery=JSON.stringify({version:1,record:incoming});storage.setItem(recoveryKey,recovery);if(storage.getItem(recoveryKey)!==recovery)throw Error('The destination could not verify durable draft receipt.');
 const local=store.get(id),divergent=Boolean((local.text||local.attachments.length)&&(local.text!==incoming.text||JSON.stringify(local.attachments)!==JSON.stringify(incoming.attachments)));
 if(divergent){const conflicts=[...local.conflicts];if(!conflicts.some(c=>c.text===incoming.text&&JSON.stringify(c.attachments)===JSON.stringify(incoming.attachments)))conflicts.push({text:incoming.text,attachments:incoming.attachments,at:Date.now()});store.hydrate(id,{conflicts});throw Error('The other window contains a different draft. Both drafts are retained; resolve the recovery before moving.');}
 const accepted=store.hydrate(id,{...incoming,id,storageError:undefined,conflicts:[...local.conflicts,...(incoming.conflicts||[])]});if(accepted.storageError)throw Error(accepted.storageError);
 const disk=JSON.parse(storage.getItem('library.workbench.conversation.'+id)||'null');if(!disk?.record||conversationTransferSnapshot(disk.record)!==conversationTransferSnapshot(incoming))throw Error('The destination could not verify durable draft receipt.');
}

/** Automatic previews respect a visual pane limit; explicit splits remain intentional. */
export function filePreviewGroup(groups:GroupPosition[],sourceId:string,direction:'right'|'below',remembered?:string,limit=3){
 const ordered=[...groups].sort((a,b)=>a.rect.left-b.rect.left||a.rect.top-b.rect.top);
 if(limit>0&&groups.length>=limit)return ordered[Math.min(limit,groups.length)-1]?.id;
 return adjacentPreviewGroup(groups,sourceId,direction,remembered);
}
export function fileTargetPane(groups:GroupPosition[],currentId:string):{group:string}|{split:'right'}{
 const current=groups.find(g=>g.id===currentId);if(!current)return {split:'right'};const r=current.rect,near=(g:GroupPosition)=>Math.abs(g.rect.top-r.top);
 const right=groups.filter(g=>g.id!==currentId&&g.rect.left>=r.right-3).sort((a,b)=>a.rect.left-b.rect.left||near(a)-near(b))[0];if(right)return {group:right.id};
 if(groups.length===1)return {split:'right'};
 const left=groups.filter(g=>g.id!==currentId&&g.rect.right<=r.left+3).sort((a,b)=>b.rect.right-a.rect.right||near(a)-near(b))[0];
 const stacked=groups.filter(g=>g.id!==currentId).sort((a,b)=>near(a)-near(b))[0];
 return {group:(left||stacked||current).id};
}
