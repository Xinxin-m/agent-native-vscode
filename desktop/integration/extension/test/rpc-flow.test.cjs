'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
let flow;try{flow=require('../rpc-flow.cjs')}catch{}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(setImmediate);
test('slow reads and UI actions bypass the FIFO storage and mutation lane',async()=>{
 assert.ok(flow,'RPC flow helper exists');const storage=deferred(),attention=deferred(),events=[];
 const dispatch=flow.createDispatcher(async m=>{events.push(m.tag);if(m.tag==='storage')await storage.promise;if(m.tag==='attention')await attention.promise;return m.tag});
 const a=dispatch({action:'storage',tag:'storage'}),b=dispatch({action:'api',path:'/api/runs',body:{message:'saved draft'},tag:'send'});
 const c=dispatch({action:'api',path:'/api/attention',tag:'attention'}),d=dispatch({action:'open',tag:'open'});
 await tick();assert.deepEqual([...events].sort(),['attention','open','storage']);assert.equal(await d,'open');
 storage.resolve();await Promise.all([a,b]);assert.equal(events.at(-1),'send');assert(events.indexOf('storage')<events.indexOf('send'));
 attention.resolve();await c;
});
test('mutation rejection does not poison later FIFO persistence',async()=>{
 assert.ok(flow);const events=[];const dispatch=flow.createDispatcher(async m=>{events.push(m.action);if(m.fail)throw Error('failed')});
 await assert.rejects(dispatch({action:'storage',fail:true}));await dispatch({action:'storage'});await dispatch({action:'api',body:{},path:'/api/run/control'});
 assert.deepEqual(events,['storage','storage','api']);
});
test('shared GETs deduplicate in flight, expire, and honor fresh reads',async()=>{
 assert.ok(flow);let now=0,calls=0;const first=deferred();
 const cache=flow.createReadCache(async()=>{calls++;return calls===1?first.promise:{value:calls}},{now:()=>now});
 const a=cache.request('base','/api/state'),b=cache.request('base','/api/state');await tick();assert.equal(calls,1);
 first.resolve({value:1});assert.equal(await a,await b);await cache.request('base','/api/state');assert.equal(calls,1);
 now=2000;await cache.request('base','/api/state');assert.equal(calls,2);
 await cache.request('base','/api/state',undefined,{fresh:true});assert.equal(calls,3);
});
test('POST invalidation prevents older in-flight data from becoming a cached authority',async()=>{
 assert.ok(flow);const old=deferred();let stateReads=0;
 const cache=flow.createReadCache(async(_base,path,body)=>{if(body!==undefined)return {ok:true};if(path==='/api/state'){stateReads++;return stateReads===1?old.promise:{value:'after mutation'}}return {ok:true}});
 const before=cache.request('base','/api/state');await tick();await cache.request('base','/api/run/control',{id:'r'});
 old.resolve({value:'before mutation'});await before;
 assert.deepEqual(await cache.request('base','/api/state'),{value:'after mutation'});assert.equal(stateReads,2);
});
test('authoritative run reads remain uncached and bounded cache evicts older metadata',async()=>{
 assert.ok(flow);const counts={};const cache=flow.createReadCache(async(_base,path)=>{counts[path]=(counts[path]||0)+1;return {path}},{maxEntries:2});
 await Promise.all([cache.request('base','/api/run?id=r'),cache.request('base','/api/run?id=r')]);assert.equal(counts['/api/run?id=r'],2);
 await cache.request('base','/api/accounts');await cache.request('base','/api/hosts');await cache.request('base','/api/live');await cache.request('base','/api/accounts');assert.equal(counts['/api/accounts'],2);
});

test('ordered pane opens do not block reads or durable mutations',async()=>{
 assert.ok(flow);const first=deferred(),events=[];
 const dispatch=flow.createDispatcher(async m=>{events.push(m.tag);if(m.tag==='open-one')await first.promise});
 const a=dispatch({action:'open',tag:'open-one'}),b=dispatch({action:'open',tag:'open-two'});
 await dispatch({action:'storage',tag:'draft'});await dispatch({action:'api',path:'/api/revision',tag:'read'});
 assert(events.includes('draft'));assert(events.includes('read'));assert(!events.includes('open-two'));
 first.resolve();await Promise.all([a,b]);assert(events.indexOf('open-one')<events.indexOf('open-two'));
});
test('failed metadata reads are retried instead of cached as data',async()=>{
 assert.ok(flow);let reads=0;const cache=flow.createReadCache(async()=>{if(++reads===1)throw Error('offline');return {ok:true}});
 await assert.rejects(cache.request('base','/api/state'),/offline/);
 assert.deepEqual(await cache.request('base','/api/state'),{ok:true});assert.equal(reads,2);
});
test('one chat list per window: ETag revalidation at most every 5 s, at once after a mutation',async()=>{
 assert.ok(flow);let now=0;const sent=[];let etag='"1"';
 const cache=flow.createReadCache(async()=>({ok:true}),{now:()=>now,sendState:async(_base,known)=>{sent.push(known);return known===etag?{notModified:true}:{etag,value:{revision:etag,chats:[]}}}});
 const a=await cache.request('base','/api/state');now=4000;const b=await cache.request('base','/api/state');
 assert.deepEqual(sent,['']);assert.equal(a,b);
 now=5000;assert.deepEqual(await cache.request('base','/api/state'),a);assert.deepEqual(sent,['','']);
 etag='"2"';await cache.request('base','/api/chat_state',{action:'rename'});now=5100;
 assert.equal((await cache.request('base','/api/state')).revision,'"2"');assert.deepEqual(sent,['','','"1"']);
 await cache.request('base','/api/state',undefined,{fresh:true});assert.equal(sent.length,4);
});
test('a chat pane receives only its own chat and unchanged when nothing it shows changed',()=>{
 assert.ok(flow);const state={revision:'r1',chats:[{key:'a',title:'A'},{key:'b',title:'B'}],views:{a:{last:'t'},b:{}},compose_options:{claude:{}},projects:{}};
 assert.equal(flow.scopeState(state),state);assert.deepEqual(flow.scopeState(state,undefined,'r1'),{unchanged:true,revision:'r1'});
 const pane=flow.scopeState(state,['a']);assert.deepEqual(pane.chats,[{key:'a',title:'A'}]);assert.deepEqual(pane.views,{a:{last:'t'}});assert.equal(pane.projects,undefined);
 assert.equal(flow.scopeState({...state,revision:'r2',chats:[state.chats[0],{key:'b',title:'B2'}]},['a'],pane.revision).unchanged,true);
 assert.notEqual(flow.scopeState({...state,chats:[{key:'a',title:'A2'}]},['a'],pane.revision).unchanged,true);
});

test('state deltas merge rows and metadata, recover an invalid base without losing the prior list',async()=>{
 const sent=[];let read=0;
 const cache=flow.createReadCache(async()=>({}),{sendState:async(_base,etag,since)=>{sent.push({etag,since});read++;return read===1?{etag:'full-a',value:{revision:'a',chats:[{key:'keep'},{key:'remove'}],views:{old:true}}}:read===2?{etag:'delta-b',value:{delta:true,since:'a',revision:'b',changed:[{key:'new'}],removed:['remove'],state:{views:{new:true}}}}:read===3?{etag:'bad',value:{delta:true,since:'wrong',revision:'c',changed:[],removed:[],state:{}}}:{etag:'full-c',value:{revision:'c',chats:[{key:'recovered'}]}}}});
 await cache.request('base','/api/state');const merged=await cache.request('base','/api/state',undefined,{fresh:true});
 assert.deepEqual(merged.chats,[{key:'keep'},{key:'new'}]);assert.deepEqual(merged.views,{new:true});
 const recovered=await cache.request('base','/api/state',undefined,{fresh:true});assert.deepEqual(recovered.chats,[{key:'recovered'}]);
 assert.deepEqual(sent.map(x=>x.since),[undefined,'a','b',undefined]);assert.equal(sent[1].etag,'');assert.equal(sent[2].etag,'');
});

test('a model update check does not hold the send lane',async()=>{const slow=deferred(),events=[];const dispatch=flow.createDispatcher(async m=>{events.push(m.tag);if(m.tag==='refresh')await slow.promise;return m.tag});const a=dispatch({action:'api',path:'/api/models/refresh',body:{install:true},tag:'refresh'}),b=dispatch({action:'api',path:'/api/runs',body:{message:'m'},tag:'send'});assert.equal(await b,'send');slow.resolve();assert.equal(await a,'refresh');});

test('opening a utility from another webview waits for the persisted preference despite a slow rail configuration write',async()=>{
 const settings={settled:Promise.resolve()},slowConfig=deferred(),slowSave=deferred();let popups=true;
 const first=flow.createDispatcher(async m=>{
  if(m.action==='config')return slowConfig.promise;
  await slowSave.promise;popups=m.value.utilityPopups;
 },settings);
 const other=flow.createDispatcher(async()=>{await settings.settled;return popups;},settings);
 const rail=first({action:'config'}),write=first({action:'preferences',value:{utilityPopups:false}});
 const opened=other({action:'open'});slowSave.resolve();
 assert.equal(await opened,false);await write;slowConfig.resolve();await rail;
});
