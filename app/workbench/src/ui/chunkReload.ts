/** A rebuild prunes old hashed chunks, so a tab opened before it can no longer lazy-load a viewer.
 Reload once to pick up the new build (the Suspense fallback stays up meanwhile); a second failure
 within a minute rethrows, so a real outage shows the error instead of a reload loop. */
type Host={sessionStorage:Pick<Storage,'getItem'|'setItem'>;location:{reload():void}};
export function reloadOnStaleChunk<T>(load:()=>Promise<T>,host:Host=window):Promise<T>{
 return load().catch(error=>{
  try{const last=Number(host.sessionStorage.getItem('chunk-reload-at'))||0;if(Date.now()-last>60_000){host.sessionStorage.setItem('chunk-reload-at',String(Date.now()));host.location.reload();return new Promise<T>(()=>{});}}catch{}
  throw error;
 });
}
