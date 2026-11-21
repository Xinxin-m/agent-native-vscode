import {describe,it,expect,beforeEach} from 'vitest';
import {submittedMessages} from './sharedRuns';
import {draftRequests} from './pendingMessages';
import {queueAction,queueFromEvents,pendingItems,resetQueueSupport} from './runQueue';
// A steer sent while Claude worked. The transcript holds the
// message and the reply; the broker's `steered` receipt arrives minutes later or never.
const text='Also add a unit test for the date parser.';
const run={id:'r',provider:'claude',status:'running',message:'Refactor the date parser.',created_at:'2026-01-02T22:16:34Z'};
// Claude stamps the message when it reads stdin (22:25:03); the slow steer response returned at 22:25:05.
const steer={text,runId:'r',ts:'2026-01-02T22:25:05Z',sentAt:'2026-01-02T22:25:01Z',operation:'steer',originalText:text,requestId:'q1',cursor:700};
const transcript=[{role:'user',text:run.message,ts:'2026-01-02T22:16:47Z'},{role:'assistant',text:'Answer one',ts:'2026-01-02T22:20:00Z'},
 {role:'user',text:text+'\n',ts:'2026-01-02T22:25:03.500Z'},{role:'assistant',text:'Added the test.',ts:'2026-01-02T22:25:33Z'}];
const now=Date.parse('2026-01-02T22:27:00Z');
describe('screenshot: steer answered before its receipt',()=>{
 it('shows no second copy and no waiting notice once the transcript has the message',()=>{
  const shown=submittedMessages(transcript,[steer],run,[{seq:1161,event:'tool',data:{}}],undefined,now);
  expect(shown).toEqual([]);
  // A receipt arriving much later changes nothing.
  expect(submittedMessages(transcript,[steer],run,[{seq:1300,event:'steered',data:{message:text,request_id:'q1'}}],undefined,now)).toEqual([]);
 });
 it('a steer whose response is still in flight also resolves from the transcript',()=>{
  expect(submittedMessages(transcript,[],run,[],{requestId:'q1',runId:'r',cursor:700,text,message:text,attachments:[],phase:'sending',sentAt:steer.sentAt} as any,now)).toEqual([]);
 });
 it('an unrecorded steer shows once with the plain sent label, after the transcript',()=>{
  const shown=submittedMessages(transcript.slice(0,2),[steer],run,[],undefined,now);
  expect(shown).toEqual([expect.objectContaining({text,delivery:'Sent · Claude reads it when the current tool call finishes'})]);
 });
 it('ignores a transcript copy older than the send (an earlier identical message)',()=>{
  const early=[...transcript.slice(0,2),{role:'user',text,ts:'2026-01-02T22:18:00Z'}];
  expect(submittedMessages(early,[steer],run,[],undefined,now)).toHaveLength(1);
 });
});
describe('the same text sent twice on purpose',()=>{
 const second={...steer,requestId:'q2',ts:'2026-01-02T22:26:05Z',sentAt:'2026-01-02T22:26:01Z',cursor:800};
 it('two pending, one recorded, then both recorded: two copies in total at every step',()=>{
  expect(submittedMessages(transcript.slice(0,2),[steer,second],run,[],undefined,now)).toHaveLength(2);
  expect(submittedMessages(transcript,[steer,second],run,[],undefined,now)).toHaveLength(1);
  expect(submittedMessages([...transcript,{role:'user',text,ts:'2026-01-02T22:26:30Z'}],[steer,second],run,[],undefined,now)).toEqual([]);
 });
 it('receipts with request ids claim their own steer only',()=>{
  const receipt={seq:801,event:'steered',data:{message:text,request_id:'q2'}};
  const shown=submittedMessages(transcript.slice(0,2),[steer,second],run,[receipt],undefined,now);
  expect(shown.map((s:any)=>s.requestId)).toEqual(['q1','q2']);
  expect(shown[0].delivery).toMatch(/^Sent/);expect(shown[1].receiptSeq).toBe(801);expect(shown[1].delivery).toBeUndefined();
 });
});
describe('server modes',()=>{
 beforeEach(()=>{resetQueueSupport();(globalThis as any).window={};});
 it('old server: no queue routes, Send steers and one item shows',async()=>{
  (globalThis as any).fetch=async()=>new Response('<html>404</html>',{status:404});
  expect(await queueAction('enqueue',{key:'k',message:text})).toBeNull();
  expect(submittedMessages(transcript.slice(0,2),[steer],run,[],undefined,now)).toHaveLength(1);
  expect(submittedMessages(transcript,[steer],run,[],undefined,now)).toHaveLength(0);
 });
 it('new server: enqueue then delivery at turn end shows one copy, then none once indexed',()=>{
  const item={id:'q9',key:'k',text,state:'delivered',created_at:'2026-01-02T22:25:01Z'};
  const queued={seq:710,event:'queue',data:{key:'k',revision:'1',items:[{...item,state:'queued'}],change:{action:'enqueue',item_id:'q9',state:'queued'}}};
  const delivered={seq:900,event:'queue',data:{key:'k',revision:'2',items:[],change:{action:'delivered',item_id:'q9',state:'delivered',item}}};
  expect(pendingItems(queueFromEvents([queued] as any,'k'))).toHaveLength(1);
  expect(submittedMessages(transcript.slice(0,2),[],run,[queued],undefined,now)).toEqual([]);
  expect(pendingItems(queueFromEvents([queued,delivered] as any,'k'))).toHaveLength(0);
  const shown=submittedMessages(transcript.slice(0,2),[],run,[queued,delivered],undefined,now);
  expect(shown).toEqual([expect.objectContaining({text,queueItem:'q9'})]);expect(shown[0].delivery).toBeUndefined();
  expect(submittedMessages(transcript,[],run,[queued,delivered],undefined,now)).toEqual([]);
 });
});
describe('double submit',()=>{
 it('a second Enter while the first request is in flight sends nothing; a retry reuses the request id',async()=>{
  const requests:string[]=[],guard=draftRequests();let release=()=>{};
  const submit=async(draft:string)=>{const id=guard.begin(draft);if(!id)return;requests.push(id);let ok=false;try{await new Promise<void>(r=>{release=r});ok=requests.length>1}finally{guard.end(draft,ok)}};
  const first=submit(text),second=submit(text+' ');release();await first;await second;
  expect(requests).toHaveLength(1);
  const retry=submit(text);release();await retry;
  expect(requests).toHaveLength(2);expect(requests[1]).toBe(requests[0]);
  const again=submit(text);release();await again;expect(requests[2]).not.toBe(requests[0]);
 });
});
