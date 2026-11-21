import {resourceState} from './resourceState';
declare const acquireVsCodeApi:any;
const host=acquireVsCodeApi();
const boot=(window as any).__LIBRARY_BOOT__;
const pending=new Map<string,{resolve:Function;reject:Function}>();
export function rpc(action:string,args:any={}):Promise<any>{const id=crypto.randomUUID();return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});host.postMessage({type:'rpc',id,action,...args});});}
(window as any).libraryRpc=rpc;
window.addEventListener('message',e=>{const m=e.data;if(m?.type==='reply'&&pending.has(m.id)){const p=pending.get(m.id)!;pending.delete(m.id);if(m.error){const error:any=new Error(m.error);Object.assign(error,m.details);p.reject(error);}else p.resolve(m.value);}if(m?.type==='filesContext'&&boot.kind==='files'){boot.resource=m.resource;host.setState({storage:values,resource:resourceState(boot.resource,values)});window.dispatchEvent(new CustomEvent('library-files-context',{detail:m.resource}));}if(m?.type==='workspaceChoice')window.dispatchEvent(new CustomEvent('library-workspace-choice',{detail:m.choice}));if(m?.type==='search')window.dispatchEvent(new Event('library-search'));if(m?.type==='dismissPopovers')window.dispatchEvent(new Event('workbench-dismiss-popovers'));if(m?.type==='refresh')window.dispatchEvent(new Event('library-refresh'));if(m?.type==='preferences')window.dispatchEvent(new CustomEvent('library-preferences',{detail:m.value}));if(m?.type==='stateChanged')window.dispatchEvent(new Event('library-state'));if(m?.type==='visibility'&&shown!==m.visible){shown=m.visible;document.dispatchEvent(new Event('visibilitychange'));}});
// A webview in a background tab still reports itself visible; fold the tab's
// visibility into document.hidden so every poller pauses there and resumes on show.
let shown=true;
const pageHidden=()=>Reflect.get(Document.prototype,'hidden',document) as boolean;
Object.defineProperty(document,'hidden',{configurable:true,get:()=>!shown||pageHidden()});
Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>!shown||pageHidden()?'hidden':'visible'});
const values:Record<string,string>={...boot.storage,...host.getState()?.storage};
host.setState({storage:values,resource:resourceState(boot.resource,values)});
void rpc('tabIdentityReady');
let storageTail:Promise<any>=Promise.resolve();
function persistStorage(){const snapshot={...values};storageTail=storageTail.catch(()=>{}).then(()=>rpc('storage',{values:snapshot}));return storageTail;}
(window as any).libraryFlushStorage=()=>storageTail;
const storage={get length(){return Object.keys(values).length},key:(i:number)=>Object.keys(values)[i]??null,getItem:(k:string)=>values[k]??null,setItem:(k:string,v:string)=>{values[k]=String(v);host.setState({storage:values,resource:resourceState(boot.resource,values)});void persistStorage().catch(e=>console.error(e));},removeItem:(k:string)=>{delete values[k];host.setState({storage:values,resource:resourceState(boot.resource,values)});void persistStorage().catch(e=>console.error(e));},clear:()=>{throw Error('Bulk draft deletion is unavailable');}};
Object.defineProperty(window,'localStorage',{value:storage});
Object.defineProperty(navigator,'clipboard',{value:{writeText:(text:string)=>rpc('copy',{text})}});
class NativeEvents extends EventTarget {
 readyState=0; onerror:any; token=crypto.randomUUID(); listener:any;
 constructor(url:string){super();const q=new URL(url,'http://local').searchParams;this.listener=(e:MessageEvent)=>{const m=e.data;if(m?.token!==this.token)return;if(m.type==='event'){this.readyState=1;this.dispatchEvent(new MessageEvent(m.entry.event,{data:JSON.stringify(m.entry)}));}else if(m.type==='eventEnd'){this.readyState=2;this.onerror?.(new Event('error'));}};window.addEventListener('message',this.listener);void rpc('subscribe',{token:this.token,runId:q.get('id'),after:Number(q.get('after')||0)}).then(()=>this.readyState=1).catch(()=>this.onerror?.(new Event('error')));}
 close(){this.readyState=2;window.removeEventListener('message',this.listener);void rpc('unsubscribe',{token:this.token});}
}
(window as any).EventSource=NativeEvents;
window.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='b'){e.preventDefault();e.stopImmediatePropagation();void rpc('toggleSidebar');}},true);
void import('./main');

function dismissPopovers(){document.querySelectorAll('details.chat-settings[open],details.chat-scope-menu[open]').forEach(node=>node.removeAttribute('open'));window.dispatchEvent(new Event('workbench-dismiss-popovers'));}
window.addEventListener('blur',dismissPopovers);
document.addEventListener('pointerdown',event=>{document.querySelectorAll('details.chat-settings[open],details.chat-scope-menu[open]').forEach(node=>{if(!node.contains(event.target as Node))node.removeAttribute('open')})},true);
window.addEventListener('keydown',event=>{if(event.key==='Escape'){const trigger=document.activeElement?.closest('details.chat-settings[open]')?.querySelector<HTMLElement>('summary');dismissPopovers();trigger?.focus();}if((event.metaKey||event.ctrlKey)&&['t','l'].includes(event.key.toLowerCase())){event.preventDefault();event.stopImmediatePropagation();void rpc(event.key.toLowerCase()==='t'?'terminal':'showChats')}},true);
