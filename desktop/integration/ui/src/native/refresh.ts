type Request=(path:string,options?:{fresh?:boolean;since?:string})=>Promise<any>;
type Sinks={state:(value:any)=>void;accounts?:(value:any)=>void;hosts?:(value:any)=>void;runs?:(value:any)=>void;attention?:(value:any)=>void;live?:(value:any)=>void;error?:(value:string)=>void};
const revision=(value:any)=>typeof value==='string'&&value.length>0?value:undefined;

/** One refresh per view; independent results never wait for the slowest endpoint.
 * The extension host holds the chat list and answers {unchanged} when this view already has its revision. */
export function createNativeRefresh(request:Request,sinks:Sinks){
 let active:Promise<void>|undefined,forced:Promise<void>|undefined,loaded=false,lastRevision:string|undefined,enabled=true,runsLoaded=false,runsToken:any,runsAt=0;
 const report=(value:string)=>{if(enabled)sinks.error?.(value)};
 async function history(fresh:boolean){
  try{
   const value=await request('/api/state',{fresh,since:loaded?lastRevision:undefined});
   if(value?.unchanged===true){
    if(loaded&&lastRevision&&revision(value.revision)===lastRevision){report('');return}
    // Keep the last valid list, but request a full snapshot on the next cycle.
    // An unbound or mismatched revision cannot prove this view is up to date.
    lastRevision=undefined;
    throw Error('The Library returned an invalid conversation revision. Retrying the full list.');
   }
   if(!Array.isArray(value?.chats))throw Error('The Library returned an invalid conversation list.');
   if(enabled){sinks.state(value);loaded=true;lastRevision=revision(value.revision);report('')}
  }catch(error){report(error instanceof Error?error.message:String(error))}
 }
 async function runSnapshot(fresh:boolean){
  try{
   let token:any;try{token=(await request('/api/revision',{fresh})).runs}catch{}
   if(!fresh&&runsLoaded&&(token!==undefined?token===runsToken:Date.now()-runsAt<15000))return;
   const value=await request('/api/runs',{fresh});
   if(enabled){sinks.runs?.(value);runsLoaded=true;runsToken=token;runsAt=Date.now()}
  }catch{/* Keep the last run summary; retry on the next revision cycle. */}
 }
 async function cycle(fresh:boolean){
  const light=[['/api/accounts','accounts'],['/api/hosts','hosts'],['/api/attention','attention'],['/api/live','live']] as const;
  const requests=light.map(async([path,name])=>{try{const value=await request(path,{fresh});if(enabled)sinks[name]?.(value)}catch{/* Keep the last independently verified value. */}});
  await Promise.all([history(fresh),runSnapshot(fresh),...requests]);
 }
 function refresh(fresh=false):Promise<void>{
  if(!enabled)return Promise.resolve();
  if(active){
   if(!fresh)return active;
   if(!forced)forced=active.then(()=>{forced=undefined;return refresh(true)});
   return forced;
  }
  const operation=cycle(fresh);active=operation;
  operation.then(()=>{if(active===operation)active=undefined},()=>{if(active===operation)active=undefined});
  return operation;
 }
 return {refresh,setActive:(value:boolean)=>{enabled=value}};
}
