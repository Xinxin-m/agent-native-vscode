import {parseFileLink} from '../open/links';
import type {Resource} from '../state/types';
export function shouldSubmitOnEnter(event:{key:string;shiftKey?:boolean;isComposing?:boolean;keyCode?:number;which?:number},compositionEndedAt:number,now:number){return event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229&&event.which!==229&&now-compositionEndedAt>100;}
export function conversationOutputs(chat:any,events:any[],text:string,host_id:string,cwd:string,chat_key?:string):Resource[]{
 const found=new Map<string,Resource>();
 const add=(value:any)=>{if(typeof value!=='string'||value.length>3000||!/(?:^|\/)\.?[^\n/]+\.[a-zA-Z0-9]{1,12}(?::\d+(?::\d+)?)?(?:#L\d+)?$/.test(value))return;const resource=parseFileLink(value,cwd,host_id);if(resource?.path)found.set(resource.path,{...resource,meta:{chat_key}})};
 const walk=(value:any,depth=0)=>{if(depth>8||!value)return;if(Array.isArray(value)){value.forEach(v=>{if(typeof v==='string')add(v);else walk(v,depth+1)});return}if(typeof value==='object')for(const [key,v]of Object.entries(value)){if(['path','file_path','file','new_path','old_path','filename'].includes(key))add(v);else if(typeof v==='object')walk(v,depth+1)}};
 walk(chat?.files_changed||[]);for(const event of events)if(['file_change','tool','tool_result'].includes(event.event))walk(event.data);
 const sources=[text,...(chat?.turns||[]).filter((t:any)=>t.role==='assistant').map((t:any)=>t.text||'')].join('\n');
 for(const match of sources.matchAll(/\[[^\]]*\]\(([^\n)]+)\)|`([^`\n]+)`/g))add((match[1]||match[2]).replace(/^<|>$/g,''));
 return [...found.values()];
}

/** Broker profile IDs and native provider identity UUIDs are separate namespaces. */
export const nativeAccount=(meta:any)=>meta?.account_id||meta?.account||'';
export const accountLabel=(account:any)=>`${account.email||account.label||account.id}${account.plan?' ('+account.plan+')':''}`;
export function executionDefaults(state:any,defaults:any){
 const changes:any={...(defaults.nativeAccount?{nativeAccount:defaults.nativeAccount}:{}),...(defaults.selectedAccount?{selectedAccount:defaults.selectedAccount}:{})};
 for(const key of ['provider','model','effort','account','host_id','cwd'])if(defaults[key]!==undefined&&!state.executionOverrides?.[key])changes[key]=defaults[key];
 return changes;
}

export function restoreNativeSelections(state:any,defaults:any,metadata:any={}){
 const changes:any=executionDefaults(state,defaults),identity=defaults.native_provider_account_id||metadata.provider_account_id;
 if(identity&&state.account===identity&&state.account!==nativeAccount(metadata)){
  changes.account=defaults.account||'';changes.executionOverrides={...state.executionOverrides,account:false};
 }
 if(identity&&state.pending?.payload.account===identity)changes.pending={...state.pending,payload:{...state.pending.payload,account:defaults.account||''}};
 return changes;
}
export const nativeOwner=(state:any,meta:any)=>state.nativeAccount||nativeAccount(meta);
export const needsHandoff=(state:any,key:string|undefined,provider:string,meta:any)=>{const host=meta.execution_host||meta.host_id||String(meta.node||'mac').split('+')[0];return Boolean(key&&((meta.is_child&&meta.native_resumable===false)||state.provider!==provider||(state.account&&nativeOwner(state,meta)&&state.account!==nativeOwner(state,meta))||(meta.cwd&&state.cwd!==meta.cwd)||(state.host_id&&state.host_id!==host)))};
