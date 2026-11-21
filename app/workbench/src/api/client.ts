export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public data?: any) { super(message); }
}
export async function api<T = any>(path: string, body?: any, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {signal, ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  if(body!==undefined&&['/api/files/roots','/api/files/roots/remove'].includes(path.split('?')[0]))window.dispatchEvent(new Event('library-workspaces-changed'));
  if (typeof window !== 'undefined') (window as any).LibraryNotifications?.observe(data);
  return data as T;
}
// The chat list is 20+ MB. Revalidate it by ETag; on 304 keep the parsed copy
// instead of re-reading the browser cache. The browser handles gzip.
let stateCache:{etag:string;path:string;value:any}|undefined;
export function mergeStateDelta(previous:any,value:any){
  if(!value?.delta)return value;
  if(!previous||value.since!==previous.revision||!Array.isArray(value.changed)||!Array.isArray(value.removed)||!value.state)throw Error('Invalid Library delta revision');
  const rows=new Map<string,any>((previous.chats||[]).map((row:any)=>[row.key,row]));
  for(const key of value.removed)rows.delete(key);
  for(const row of value.changed)rows.set(row.key,row);
  return {...value.state,revision:value.revision,chats:[...rows.values()]};
}
export async function getState(forceFull=false): Promise<any> {
  const path='/api/state'+(!forceFull&&stateCache?.value?.revision?'?since='+encodeURIComponent(stateCache.value.revision):'');
  const response = await fetch(path, {cache:'no-store', headers: stateCache?.path===path&&stateCache.etag ? {'If-None-Match': stateCache.etag} : {}});
  if (response.status === 304 && stateCache) return stateCache.value;
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  let value;try{value=mergeStateDelta(stateCache?.value,data)}catch(error){if(!forceFull)return getState(true);throw error}
  stateCache = {etag:response.headers.get('ETag')||'',path,value};
  return value;
}
// Runs fn now, every ms while the tab is visible, and once when it is shown again.
export function pollWhileVisible(fn:()=>void, ms:number) {
  const tick = () => { if (!document.hidden) fn(); };
  fn(); const timer = setInterval(tick, ms); document.addEventListener('visibilitychange', tick);
  return () => { clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
}
export const query = (path:string, values:Record<string,any>) => path + '?' + new URLSearchParams(Object.entries(values).filter(([,v])=>v!==undefined && v!==null).map(([k,v])=>[k,String(v)])).toString();
