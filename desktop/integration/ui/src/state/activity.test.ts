import {describe,it,expect} from "vitest";
import {chatActivity} from "./activity";
const resource:any={kind:"chat",key:"codex-a"};
describe("unread assistant replies (the server decides)",()=>{
 const chat=(extra:any)=>({chats:[{key:resource.key,last_assistant_ts:"2026-01-02T00:00:00Z",ts_last:"2026-01-03T00:00:00Z",...extra}]});
 it("is blue only when the server says the result is unseen",()=>{expect(chatActivity(resource,chat({unseen:true})).unread).toBe(true);expect(chatActivity(resource,chat({unseen:false})).unread).toBe(false)});
 it("ignores client-side views and any localStorage baseline",()=>{expect(chatActivity(resource,chat({unseen:false}),[],[],{[resource.key]:"2025-01-01T00:00:00Z","*":"2025-01-01T00:00:00Z"}).unread).toBe(false);expect(chatActivity(resource,chat({unseen:true}),[],[],{[resource.key]:"2027-01-01T00:00:00Z"}).unread).toBe(true)});
 it("marks a completed run finished until the server seen time passes it, and never while running",()=>{const data={chats:[{key:resource.key}]},seen={chats:[{key:resource.key,seen_at:"2026-01-06T00:01:00Z"}]};const done=[{id:"r1",key:resource.key,status:"completed",finished_at:"2026-01-06T00:00:00Z"}];expect(chatActivity(resource,data,done,[],{}).finished).toBe(true);expect(chatActivity(resource,seen,done,[],{}).finished).toBe(false);expect(chatActivity(resource,data,[...done,{id:"r2",key:resource.key,status:"running"}],[],{}).finished).toBe(false)});
 it("does not invent unread output when the field is missing (older server)",()=>{expect(chatActivity(resource,{chats:[{key:resource.key,ts_last:"2026-01-03T00:00:00Z",last_assistant_ts:"2026-01-02T00:00:00Z"}]}).unread).toBe(false)});
 it("carries the server live status",()=>{expect(chatActivity(resource,chat({live_status:"idle"})).live).toBe("idle")});
});

describe("live activity evidence",()=>{
 it("keeps a running broker active through quiet gaps",()=>{expect(chatActivity(resource,{},[{key:resource.key,status:"running"}]).running).toBe(true)});
 it("does not animate unknown, disconnected, stopped or idle runs",()=>{for(const status of ["unknown","disconnected","stopped","remote_idle"])expect(chatActivity(resource,{},[{key:resource.key,status}]).running,status).toBe(false)});
 it("shows a waiting state for an explicit decision even without an attention record",()=>{const a=chatActivity(resource,{},[{key:resource.key,status:"waiting_input"}]);expect(a.awaiting).toBe(true);expect(a.running).toBe(false)});
 it("does not revive a terminal run from an older live observation",()=>{const now=Date.now();const a=chatActivity(resource,{},[{key:resource.key,status:"completed",finished_at:new Date(now).toISOString()}],[],{},[{chat_key:resource.key,alive:true,status:"running",observed_at:(now-5000)/1000}]);expect(a.running).toBe(false)});
});
describe("failed (round 3)",()=>{
 it("is the latest run failing, else the server's failed flag; never while a run is active",()=>{const d={chats:[{key:resource.key,failed:true}]};expect(chatActivity(resource,d).failed).toBe(true);expect(chatActivity(resource,{chats:[{key:resource.key}]},[{id:"r",key:resource.key,status:"failed"}],[],{}).failed).toBe(true);expect(chatActivity(resource,d,[{id:"r",key:resource.key,status:"running"}],[],{}).failed).toBe(false);});
});

import {rowStatus} from '../navigation/chats';
it('a chat that was running and then failed shows one mark: failed, until the transcript moves on',()=>{const resource:any={kind:'chat',key:'chat'},session={chat_key:'chat',alive:true,status:'busy',observed_at:Date.now()/1000},run={id:'r',key:'chat',status:'failed',created_at:'2026-11-23T07:03:35Z',finished_at:'2026-11-23T07:03:38Z',failure:{kind:'limit_reached'}};
 const mark=(ts_last:string)=>{const a=chatActivity(resource,{chats:[{key:'chat',ts_last,live_status:'working',unseen:true}]},[run],[],{},[session]);return rowStatus(a.live,a.unread,{...a,failed:a.failed||a.recovery});};
 expect(mark('2026-11-23T07:03:38Z')).toBe('failed');
 expect(mark('2026-11-23T20:28:59Z')).toBe('working');});

it('gives a stalled broker run priority over the live process spinner',()=>{
 const key='codex-s',resource={kind:'chat',key} as const;
 const session={chat_key:key,alive:true,status:'running',observed_at:Date.now()/1000,live_status:'working'};
 const run={key,status:'running',stalled:true};
 const get=(r:any)=>chatActivity(resource,{chats:[{key,live_status:'working'}]},[r],[],{},[session]);
 expect(get(run)).toMatchObject({live:'stalled',running:false});
 expect(rowStatus(get(run).live,true)).toBe('stalled');
 expect(get({...run,stalled:false})).toMatchObject({live:'working',running:true});
});
