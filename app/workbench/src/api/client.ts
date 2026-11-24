import {reuseSnapshot} from '../state/pollingSnapshots';
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public data?: any) { super(message); }
}
// Routes added since the November 22 redesign. A server older than that (8744 runs old code until it restarts) has
// none of them: its /api/state rows lack `failed`, and an unknown route answers 404 with an HTML page. Such a route is
// not requested and its feature hides; after a 404 it is tried again after 2 min, then 4, 8, up to 10 min.
export const OPTIONAL_ROUTES=['/api/notifications','/api/notifications/act','/api/seen','/api/refresh','/api/live/stop_idle','/api/project_workspace','/api/placements','/api/accounts/picker','/api/accounts/login/cancel','/api/accounts/rename','/api/accounts/logout'];
const missing=new Map<string,{until:number;wait:number}>();
let olderServer=false,known:()=>void=()=>{};
const serverKnown=new Promise<void>(resolve=>{known=resolve;setTimeout(resolve,15000);});
export function noteServerState(state:any){const row=state?.chats?.[0];if(row)olderServer=!('failed' in row);known();}
export function available(path:string){const route=path.split('?')[0];return !OPTIONAL_ROUTES.includes(route)||!olderServer&&Date.now()>=(missing.get(route)?.until||0);}
export async function api<T = any>(path: string, body?: any, signal?: AbortSignal): Promise<T> {
  const route=path.split('?')[0];
  if(OPTIONAL_ROUTES.includes(route))await serverKnown;
  if(!available(route))throw new ApiError('Not available on this Library server',404,'not_available');
  const response = await fetch(path, {signal, ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const json = !!response.headers.get('Content-Type')?.includes('json');
  if(OPTIONAL_ROUTES.includes(route)){if(response.status===404&&!json){const wait=Math.min(2*(missing.get(route)?.wait||60000),600000);missing.set(route,{until:Date.now()+wait,wait});throw new ApiError('Not available on this Library server',404,'not_available');}missing.delete(route);}
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  if(body!==undefined&&['/api/files/roots','/api/files/roots/remove'].includes(path.split('?')[0]))window.dispatchEvent(new Event('library-workspaces-changed'));
  if (typeof window !== 'undefined') (window as any).LibraryNotifications?.observe(data);
  return data as T;
}
// The chat list is 20+ MB. Revalidate it by ETag; on 304 keep the parsed copy
// instead of re-reading the browser cache. The browser handles gzip.
let stateCache:{etag:string;path:string;value:any}|undefined;
// Unchanged rows and sections stay the same objects: a copy that an old render still holds then shares them.
export function mergeStateDelta(previous:any,value:any){
  if(value?.delta&&(!previous||value.since!==previous.revision||!Array.isArray(value.changed)||!Array.isArray(value.removed)||!value.state))throw Error('Invalid Library delta revision');
  if(!previous)return value;
  const old=new Map<string,any>((previous.chats||[]).map((row:any)=>[row.key,row]));
  let chats;
  if(value.delta){const rows=new Map(old);for(const key of value.removed)rows.delete(key);for(const row of value.changed)rows.set(row.key,reuseSnapshot(old.get(row.key),row));chats=[...rows.values()];}
  else chats=(value.chats||[]).map((row:any)=>reuseSnapshot(old.get(row.key),row));
  const sections=value.delta?value.state:value;
  return {...Object.fromEntries(Object.entries(sections).filter(([key])=>key!=='chats').map(([key,section])=>[key,reuseSnapshot(previous[key],section)])),revision:value.revision,chats};
}
// One request at a time: two overlapping deltas from the same revision made the second fail and reload in full.
let stateRequest:Promise<any>|undefined;
export function getState():Promise<any>{return stateRequest??=loadState().finally(()=>{stateRequest=undefined})}
async function loadState(forceFull=false): Promise<any> {
  const path='/api/state'+(!forceFull&&stateCache?.value?.revision?'?since='+encodeURIComponent(stateCache.value.revision):'');
  const response = await fetch(path, {cache:'no-store', headers: stateCache?.path===path&&stateCache.etag ? {'If-None-Match': stateCache.etag} : {}});
  if (response.status === 304 && stateCache) return stateCache.value;
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  let value;try{value=mergeStateDelta(stateCache?.value,data)}catch(error){if(!forceFull)return loadState(true);throw error}
  stateCache = {etag:response.headers.get('ETag')||'',path,value};
  noteServerState(value);
  return value;
}
// Runs fn now, every ms while the tab is visible, and once when it is shown again.
export function pollWhileVisible(fn:()=>void, ms:number) {
  const tick = () => { if (!document.hidden) fn(); };
  fn(); const timer = setInterval(tick, ms); document.addEventListener('visibilitychange', tick);
  return () => { clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
}
export const query = (path:string, values:Record<string,any>) => path + '?' + new URLSearchParams(Object.entries(values).filter(([,v])=>v!==undefined && v!==null).map(([k,v])=>[k,String(v)])).toString();
