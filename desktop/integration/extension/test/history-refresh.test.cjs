const assert = require('node:assert/strict');
const test = require('node:test');
const {createHistoryRefresh} = require('../history-refresh.cjs');

test('duplicate clicks share one request and redraw after each completed import', async () => {
 const calls = [], views = [];
 const replies = [
  {id:'record',running:true,phase:'local',local_ready:false},
  {id:'record',running:true,phase:'remote',local_ready:true},
  {id:'record',running:false,phase:'done',local_ready:true,remote_indexed:true,errors:[]}
 ];
 const refresh = createHistoryRefresh({
  api:async (path,body)=>{calls.push([path,body]);return {refresh:replies.shift()};},
  redraw:phase=>views.push(phase),delay:async()=>{},
 });
 const first = refresh(), second = refresh();
 assert.equal(first, second);
 await first;
 assert.deepEqual(calls, [
  ['/api/sync',{include_remote:true}],
  ['/api/history_refresh?id=record',undefined],
  ['/api/history_refresh?id=record',undefined],
 ]);
 assert.deepEqual(views,['local','done']);
});

test('cluster failure warns after local history has loaded and clears progress', async () => {
 const events = [];
 const refresh = createHistoryRefresh({
  api:async (_,body)=>({refresh:body
   ? {id:'partial',running:true,local_ready:true,phase:'remote'}
   : {id:'partial',running:false,local_ready:true,phase:'done',errors:['SC offline']}}),
  redraw:phase=>events.push(phase),
  progress:()=>({dispose:()=>events.push('finished')}),
  warn:message=>events.push(message),delay:async()=>{},
 });
 await refresh();
 assert.deepEqual(events,['local','SC offline','finished']);
});
