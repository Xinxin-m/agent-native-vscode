'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const ts=require('../../../vscode/node_modules/typescript');
const file=path.join(__dirname,'../../ui/src/native/refresh.ts');let createNativeRefresh;
if(fs.existsSync(file)){const box={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,box);createNativeRefresh=box.exports.createNativeRefresh}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};const tick=()=>new Promise(setImmediate);
test('slow attention does not delay history or independent account updates',async()=>{
 assert.ok(createNativeRefresh,'Refresh controller exists');const attention=deferred(),updates=[];
 const refresh=createNativeRefresh(async path=>path==='/api/attention'?attention.promise:path==='/api/state'?{revision:'a',chats:[]}:{accounts:[]},{state:v=>updates.push(['state',v.revision]),accounts:()=>updates.push(['accounts']),error:e=>updates.push(['error',e])});
 const running=refresh.refresh();await tick();assert(updates.some(v=>v[0]==='state'));assert(updates.some(v=>v[0]==='accounts'));
 attention.resolve({items:[]});await running;
});
test('a view sends its revision and keeps its list when the host answers unchanged',async()=>{
 assert.ok(createNativeRefresh);let revision='a';const sent=[],updates=[];
 const refresh=createNativeRefresh(async(path,options)=>{if(path!=='/api/state')return {};sent.push(options?.since);return options?.since===revision?{unchanged:true,revision}:{revision,chats:[]}},{state:v=>updates.push(v.revision)});
 await refresh.refresh();await refresh.refresh();assert.deepEqual(sent,[undefined,'a']);assert.deepEqual(updates,['a']);
 revision='b';await refresh.refresh();assert.deepEqual(updates,['a','b']);
});
test('polls coalesce and an explicit refresh queues one fresh history read',async()=>{
 assert.ok(createNativeRefresh);const slow=deferred();let first=true,stateReads=0,active=0,peak=0;const freshness=[];
 const refresh=createNativeRefresh(async(path,options)=>{if(path==='/api/state'){stateReads++;freshness.push(options?.fresh===true);active++;peak=Math.max(peak,active);if(first){first=false;await slow.promise}active--;return {revision:'a',chats:[]}}return {revision:'a'}},{state(){}});
 const a=refresh.refresh(),b=refresh.refresh(),forced=refresh.refresh(true);await tick();assert.equal(stateReads,1);
 slow.resolve();await Promise.all([a,b,forced]);assert.equal(stateReads,2);assert.equal(peak,1);assert.deepEqual(freshness,[false,true]);
});
test('a malformed list is reported and an explicit refresh recovers',async()=>{
 assert.ok(createNativeRefresh);let broken=true,updates=0;const errors=[];
 const refresh=createNativeRefresh(async path=>path==='/api/state'?(broken?{revision:'a'}:{revision:'a',chats:[]}):{},{state:()=>updates++,error:e=>errors.push(e)});
 await refresh.refresh();assert.equal(updates,0);assert(errors.some(e=>/invalid/i.test(e)));
 broken=false;await refresh.refresh(true);assert.equal(updates,1);
});

test('unmounted views ignore late results and repeated explicit refreshes coalesce',async()=>{
 assert.ok(createNativeRefresh);const pending=deferred();let reads=0,updates=0;
 const refresh=createNativeRefresh(async path=>{if(path==='/api/state'){reads++;if(reads===1)await pending.promise;return {revision:'a',chats:[]}}return {revision:'a'}},{state:()=>updates++});
 const a=refresh.refresh(),b=refresh.refresh(true),c=refresh.refresh(true);refresh.setActive(false);pending.resolve();
 await Promise.all([a,b,c]);assert.equal(reads,1);assert.equal(updates,0);
 refresh.setActive(true);await refresh.refresh();assert.equal(reads,2);assert.equal(updates,1);
});

test('invalid unchanged revisions preserve the list and request a full recovery',async()=>{
 for(const malformed of [undefined,'','other',17]){
  const sent=[],updates=[],errors=[];let read=0;
  const refresh=createNativeRefresh(async(path,options)=>{
   if(path!=='/api/state')return {};
   sent.push(options?.since);read++;
   return read===1?{revision:'a',chats:[{key:'original'}]}:read===2?{unchanged:true,revision:malformed}:{revision:'b',chats:[{key:'recovered'}]};
  },{state:v=>updates.push(v.chats[0].key),error:e=>errors.push(e)});
  await refresh.refresh();await refresh.refresh();
  assert.deepEqual(updates,['original']);assert.match(errors.at(-1),/invalid.*revision/i);
  await refresh.refresh();assert.deepEqual(sent,[undefined,'a',undefined]);assert.deepEqual(updates,['original','recovered']);assert.equal(errors.at(-1),'');
 }
});
test('unchanged without a loaded snapshot is rejected',async()=>{
 const sent=[],errors=[],updates=[];let read=0;
 const refresh=createNativeRefresh(async(path,options)=>{if(path!=='/api/state')return {};sent.push(options?.since);return ++read===1?{unchanged:true,revision:'a'}:{revision:'a',chats:[]}}, {state:v=>updates.push(v),error:e=>errors.push(e)});
 await refresh.refresh();assert.equal(updates.length,0);assert.match(errors.at(-1),/revision/);
 await refresh.refresh();assert.deepEqual(sent,[undefined,undefined]);assert.equal(updates.length,1);
});
test('full snapshots without valid revisions never send a conditional request',async()=>{
 for(const malformed of [undefined,'',17]){
  const sent=[],updates=[];
  const refresh=createNativeRefresh(async(path,options)=>{if(path!=='/api/state')return {};sent.push(options?.since);return {revision:malformed,chats:[]}}, {state:v=>updates.push(v)});
  await refresh.refresh();await refresh.refresh();assert.deepEqual(sent,[undefined,undefined]);assert.equal(updates.length,2);
 }
});

test('unchanged run revision skips summary bodies, changes and explicit refresh fetch them',async()=>{
 let token='boot:1',reads=0;const values=[];
 const refresh=createNativeRefresh(async path=>path==='/api/state'?{revision:'a',chats:[]}:path==='/api/revision'?{runs:token}:path==='/api/runs'?(reads++,{runs:[{id:String(reads)}]}):{}, {state(){},runs:v=>values.push(v.runs[0].id)});
 await refresh.refresh();await refresh.refresh();assert.equal(reads,1);
 token='boot:2';await refresh.refresh();assert.equal(reads,2);
 await refresh.refresh(true);assert.equal(reads,3);assert.deepEqual(values,['1','2','3']);
});
