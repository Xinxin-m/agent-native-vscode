"use strict";
// Only these fixed metadata routes may be shared between webviews. Per-run and
// decision rechecks stay uncached; mutations invalidate both sides of their call.
const TTL=Object.freeze({'/api/state':1000,'/api/revision':500,'/api/accounts':3000,'/api/hosts':3000,'/api/runs':250,'/api/attention':0,'/api/live':500,'/api/capabilities':10000});
// With sendState (bridge.requestState) the window holds one copy of /api/state:
// it is revalidated by ETag at most every stateGap ms, and at once after a
// mutation or an explicit fresh read. onState runs when a new list arrives.
function mergeStateDelta(previous,value){
 if(!value?.delta)return value;
 if(!previous||value.since!==previous.revision||!Array.isArray(value.changed)||!Array.isArray(value.removed)||!value.state)throw Error('Invalid Library delta revision');
 const rows=new Map((previous.chats||[]).map(row=>[row.key,row]));for(const key of value.removed)rows.delete(key);for(const row of value.changed)rows.set(row.key,row);
 return {...value.state,revision:value.revision,chats:[...rows.values()]};
}
function createReadCache(send,{now=Date.now,maxEntries=16,sendState,stateGap=5000,onState}={}){
 const cached=new Map(),pending=new Map();let generation=0,state={};
 const invalidate=()=>{generation++;cached.clear();pending.clear();state.stale=true};
 function prune(){const time=now();for(const [key,entry] of cached)if(entry.until<=time)cached.delete(key)}
 function readState(base,fresh){
  if(state.base!==base)state={base,seq:0,applied:0,stale:true};
  if(state.value&&!fresh&&!state.stale&&now()-state.at<stateGap)return Promise.resolve(state.value);
  if(state.pending&&(!fresh||state.pending.fresh))return state.pending.promise;
  const current=state,seq=++current.seq,version=generation,entry={fresh};
  const baseline=current.value,since=baseline?.revision;
  entry.promise=Promise.resolve().then(()=>sendState(base,current.etagSince===since?current.etag||'':'',since)).then(async reply=>{
   let value,usedSince=since;
   if(!reply.notModified){
    try{value=mergeStateDelta(baseline,reply.value)}catch{
     // A rejected/expired base must recover without discarding the last good list.
     reply=await sendState(base,'',undefined);usedSince=undefined;
     if(reply.notModified)throw Error('Invalid full Library state response');
     value=mergeStateDelta(undefined,reply.value);
    }
   }
   if(seq>current.applied){current.applied=seq;if(!reply.notModified){current.value=value;current.etag=reply.etag;current.etagSince=usedSince;onState?.()}current.at=now();if(version===generation)current.stale=false}
   return current.value;
  }).finally(()=>{if(current.pending===entry)current.pending=undefined});
  current.pending=entry;return entry.promise;
 }
 function request(base,path,body,options={}){
  if(body!==undefined){invalidate();return Promise.resolve().then(()=>send(base,path,body)).finally(invalidate)}
  if(path==='/api/state'&&sendState)return readState(base,options.fresh===true);
  const ttl=Object.hasOwn(TTL,path)?TTL[path]:undefined;
  if(ttl===undefined)return Promise.resolve().then(()=>send(base,path));
  prune();const key=base+'\n'+path,old=cached.get(key);
  if(!options.fresh&&old)return Promise.resolve(old.value);
  const running=pending.get(key);
  if(running&&(!options.fresh||running.fresh))return running.promise;
  const version=generation,entry={fresh:options.fresh===true,promise:null};
  entry.promise=Promise.resolve().then(()=>send(base,path)).then(value=>{
   if(version===generation&&ttl>0){cached.delete(key);cached.set(key,{value,until:now()+ttl});while(cached.size>maxEntries)cached.delete(cached.keys().next().value)}
   return value;
  }).finally(()=>{if(pending.get(key)===entry)pending.delete(key)});
  pending.set(key,entry);return entry.promise;
 }
 return {request,invalidate};
}
const parallelActions=new Set(['subscribe','unsubscribe','tabIdentityReady','title','terminal','toggleSecondarySidebar','toggleSidebar','advancedEditor','confirm','sessionDetails','models','imageResource']);
function createDispatcher(handle,preferenceWrites={settled:Promise.resolve()}){
 let mutations=Promise.resolve(),opens=Promise.resolve();
 return message=>{
  // Preferences affect native commands as well as opens from other webviews.
  if(message.action==='preferences'){
   const write=preferenceWrites.settled.then(()=>handle(message));
   preferenceWrites.settled=write.catch(()=>{});
   return write;
  }
  if(message.action==='open'){const operation=opens.then(()=>handle(message));opens=operation.catch(()=>{});return operation}
  // A model update check writes no Library data and an install can take minutes, so it must not hold the send lane.
  const parallel=(message.action==='api'&&(message.body===undefined||message.path==='/api/models/refresh'))||parallelActions.has(message.action)||(message.action==='githubAccounts'&&!message.connect);
  if(parallel)return Promise.resolve().then(()=>handle(message));
  const operation=mutations.then(()=>handle(message));
  mutations=operation.catch(()=>{});return operation;
 };
}
// What one webview receives of the chat list. A chat pane (keys given) gets only
// its own chat rows, their read marks and the model options; other views get
// the whole list. A view that already holds this revision gets {unchanged}.
function scopeState(state,keys,since){
 if(!Array.isArray(keys))return since&&since===state.revision?{unchanged:true,revision:state.revision}:state;
 const wanted=new Set(keys.filter(k=>typeof k==='string'&&k));
 const scoped={chats:(state.chats||[]).filter(c=>wanted.has(c.key)),views:Object.fromEntries(Object.entries(state.views||{}).filter(([k])=>wanted.has(k))),compose_options:state.compose_options};
 const revision='chat:'+require('node:crypto').createHash('sha1').update(JSON.stringify(scoped)).digest('hex').slice(0,16);
 return since===revision?{unchanged:true,revision}:{...scoped,revision};
}
module.exports={createReadCache,createDispatcher,scopeState,mergeStateDelta};
