export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public data?: any) { super(message); }
}
// Routes added since the November 22 redesign. A server older than that (8744 runs old code until it restarts) has
// none of them: its /api/state rows lack `failed`, and an unknown route answers 404 with an HTML page (the extension
// bridge marks it code not_json). Such a route is not requested and its feature hides; after a 404 it is tried again
// after 2 min, then 4, 8, up to 10 min.
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
  if ((window as any).libraryRpc) {
   let value;
   try{value=await (window as any).libraryRpc('api',{path,body});}
   catch(e:any){if(OPTIONAL_ROUTES.includes(route)&&e?.status===404&&e.code==='not_json'){const wait=Math.min(2*(missing.get(route)?.wait||60000),600000);missing.set(route,{until:Date.now()+wait,wait});throw new ApiError('Not available on this Library server',404,'not_available');}throw e;}
   missing.delete(route);
   if(body!==undefined&&['/api/files/roots','/api/files/roots/remove'].includes(path.split('?')[0]))window.dispatchEvent(new Event('library-workspaces-changed'));(window as any).LibraryNotifications?.observe(value);return value;}
  const response = await fetch(path, {signal, ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  if(body!==undefined&&['/api/files/roots','/api/files/roots/remove'].includes(path.split('?')[0]))window.dispatchEvent(new Event('library-workspaces-changed'));
  return data as T;
}
export const query = (path:string, values:Record<string,any>) => path + '?' + new URLSearchParams(Object.entries(values).filter(([,v])=>v!==undefined && v!==null).map(([k,v])=>[k,String(v)])).toString();
