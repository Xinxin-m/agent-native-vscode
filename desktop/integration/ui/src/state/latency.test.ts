import {describe,it,expect,vi} from 'vitest';
import {emptyRun,replayEvents,runOutput,refreshRun,loadEarlierEvents,ingestRun,subscribeRun} from './runs';
import {createConversationStore} from './conversations';
import {sharedRunPatch} from '../chat/sharedRuns';
describe('bounded initial journal replay',()=>{
 it('keeps the complete snapshot reply and appends only newer deltas',()=>{
  const tail=[{seq:4999,event:'done',data:{text:'previous turn'}},{seq:5000,event:'delta',data:{text:'already in snapshot'}},{seq:5001,event:'tool',data:{name:'Read'}}];
  let state=replayEvents(emptyRun('tail'),tail,{id:'tail',status:'running',text:'Complete answer',last_seq:5001});
  expect(runOutput(state)).toBe('Complete answer');
  state=replayEvents(state,[{seq:5002,event:'delta',data:{text:' continued'}}]);
  expect(runOutput(state)).toBe('Complete answer continued');
  state=replayEvents(state,[],{id:'tail',status:'running',message:'summary only'});
  expect(runOutput(state)).toBe('Complete answer continued');expect(state.events.find(e=>e.event==='tool')?.data.name).toBe('Read');
 });
 it('keeps the earlier-page marker through polling and loads older tools only on demand',async()=>{
  const calls:string[]=[];vi.stubGlobal('window',{});vi.stubGlobal('fetch',async(path:string)=>{
   calls.push(path);const url=new URL(path,'http://fixture');const earlier=url.searchParams.has('before'),initial=url.searchParams.get('after')==='0';
   return new Response(JSON.stringify({run:{id:'tail-pagination',status:'running',text:'Full output',last_seq:5001},events:earlier?[{seq:4998,event:'tool',data:{name:'Earlier tool'}}]:initial?[{seq:5001,event:'tool_result',data:{content:'kept result'}}]:[],events_truncated_before:initial?5001:earlier?null:null}));
  });
  try{let state=await refreshRun('tail-pagination');expect(state.truncatedBefore).toBe(5001);state=await refreshRun('tail-pagination');expect(state.truncatedBefore).toBe(5001);expect(calls).toHaveLength(2);state=await loadEarlierEvents('tail-pagination');expect(calls[2]).toContain('before=5001');expect(state.events.map(e=>e.seq)).toEqual([4998,5001]);expect(state.cursor).toBe(5001);expect(state.truncatedBefore).toBeNull();expect(runOutput(state)).toBe('Full output')}finally{vi.unstubAllGlobals()}
 });
 it('keeps a long-open live run at the same journal size a fresh load gets',()=>{
  // A fresh load receives the newest 2000 events. A tab left open while the run streams must not keep more.
  let state=replayEvents(emptyRun('long'),[],{id:'long',status:'running',text:'',last_seq:0});
  for(let seq=1;seq<=6000;seq++){state=replayEvents(state,[{seq,event:seq%3?'provider_event':'delta',data:{text:'x'}}]);if(seq%1000===0)state=replayEvents(state,[],{id:'long',status:'running',text:'x'.repeat(seq/3),last_seq:seq});}
  state=replayEvents(state,[{seq:6001,event:'delta',data:{text:'!'}}]);
  expect(state.events.length).toBeLessThanOrEqual(2001);expect(state.events.at(-1)?.seq).toBe(6001);
  expect(state.truncatedBefore).toBe(state.events[0].seq);expect(runOutput(state)).toBe('x'.repeat(2000)+'!');
 });
 it('never drops events the output snapshot does not cover',()=>{
  let state=emptyRun('nosnap');for(let seq=1;seq<=3000;seq++)state=replayEvents(state,[{seq,event:'delta',data:{text:'y'}}]);
  expect(state.events.length).toBe(3000);expect(runOutput(state)).toBe('y'.repeat(3000));
 });
 it('renders a burst of streamed events once, not once per event',()=>{
  // The event stream arrives in bursts of hundreds; one render per event blocked the page for seconds.
  vi.useFakeTimers();vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{},length:0,key:()=>null});
  try{let renders=0;const stop=subscribeRun('burst',()=>renders++);
   for(let seq=1;seq<=300;seq++)ingestRun({id:'burst',status:'running'},[{seq,event:'delta',data:{text:'x'}}]);
   expect(renders).toBeLessThanOrEqual(1);vi.advanceTimersByTime(150);expect(renders).toBe(2);
   ingestRun({id:'burst',status:'completed'},[]);vi.advanceTimersByTime(150);expect(renders).toBe(3);stop();
  }finally{vi.useRealTimers();vi.unstubAllGlobals()}
 });
 it('summary reconciliation preserves drafts, attachments and unconfirmed request identity',()=>{
  const values=new Map<string,string>(),disk={getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>{values.set(key,value)}},store=createConversationStore(disk);
  store.get('draft-safe');store.patch('draft-safe',{text:'Keep my next message',attachments:[{id:'a',name:'note',mime:'text/plain',size:1}]});const pending=store.prepare('draft-safe',{message:'Unconfirmed message'});
  const changes=sharedRunPatch(store.get('draft-safe'),{},{id:'summary-run',key:'codex-fixture',provider:'codex',status:'running',message:'Original message'});
  store.hydrate('draft-safe',changes);const state=store.get('draft-safe');expect(state.text).toBe('Keep my next message');expect(state.attachments[0].id).toBe('a');expect(state.pending?.payload.request_id).toBe(pending.request_id);
 });
});
