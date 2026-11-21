export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public data?: any) { super(message); }
}
export async function api<T = any>(path: string, body?: any, signal?: AbortSignal): Promise<T> {
  if ((window as any).libraryRpc) {const value=await (window as any).libraryRpc('api',{path,body});if(body!==undefined&&path.split('?')[0]==='/api/files/roots')window.dispatchEvent(new Event('library-workspaces-changed'));(window as any).LibraryNotifications?.observe(value);return value;}
  const response = await fetch(path, {signal, ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const data = await response.json().catch(() => ({error:`HTTP ${response.status}`}));
  if (!response.ok || data.ok === false || data.error) throw new ApiError(data.error || `HTTP ${response.status}`,response.status,data.code,data);
  if(body!==undefined&&path.split('?')[0]==='/api/files/roots')window.dispatchEvent(new Event('library-workspaces-changed'));
  return data as T;
}
export const query = (path:string, values:Record<string,any>) => path + '?' + new URLSearchParams(Object.entries(values).filter(([,v])=>v!==undefined && v!==null).map(([k,v])=>[k,String(v)])).toString();
