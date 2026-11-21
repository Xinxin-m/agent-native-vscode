import {describe,it,expect} from "vitest";
import {chatActivity} from "./activity";
const resource:any={kind:"chat",key:"codex-a"};
describe("unread assistant replies",()=>{
 it("ignores newer user activity after an already seen assistant reply",()=>{const data={chats:[{key:resource.key,last_assistant_ts:"2026-01-01T00:00:00Z",ts_last:"2026-01-03T00:00:00Z"}]};expect(chatActivity(resource,data,[],[],{[resource.key]:"2026-01-02T00:00:00Z"}).unread).toBe(false)});
 it("shows unseen assistant reply even when later user activity exists",()=>{const data={chats:[{key:resource.key,last_assistant_ts:"2026-01-02T00:00:00Z",ts_last:"2026-01-03T00:00:00Z"}]};expect(chatActivity(resource,data,[],[],{[resource.key]:"2026-01-01T00:00:00Z"}).unread).toBe(true)});
 it("treats replies older than the client's tracking start as read, so a fresh client is not blue on every chat",()=>{const data={chats:[{key:resource.key,last_assistant_ts:"2026-01-02T00:00:00Z"}]};expect(chatActivity(resource,data,[],[],{"*":"2026-01-05T00:00:00Z"}).unread).toBe(false)});
 it("clears unread once the chat is viewed after the reply",()=>{const data={chats:[{key:resource.key,last_assistant_ts:"2026-01-06T00:00:00Z"}]};const base={"*":"2026-01-05T00:00:00Z"};expect(chatActivity(resource,data,[],[],base).unread).toBe(true);expect(chatActivity(resource,data,[],[],{...base,[resource.key]:"2026-01-06T00:01:00Z"}).unread).toBe(false)});
 it("marks a completed run finished until it is viewed, and never while running",()=>{const data={chats:[{key:resource.key}]};const done=[{id:"r1",key:resource.key,status:"completed",finished_at:"2026-01-06T00:00:00Z"}];expect(chatActivity(resource,data,done,[],{}).finished).toBe(true);expect(chatActivity(resource,data,done,[],{[resource.key]:"2026-01-06T00:01:00Z"}).finished).toBe(false);expect(chatActivity(resource,data,[...done,{id:"r2",key:resource.key,status:"running"}],[],{}).finished).toBe(false)});
 it("does not invent unread output when assistant timestamp is missing",()=>{expect(chatActivity(resource,{chats:[{key:resource.key,ts_last:"2026-01-03T00:00:00Z"}]}).unread).toBe(false)});
});

describe("live activity evidence",()=>{
 it("keeps a running broker active through quiet gaps",()=>{expect(chatActivity(resource,{},[{key:resource.key,status:"running"}]).running).toBe(true)});
 it("does not animate unknown, disconnected, stopped or idle runs",()=>{for(const status of ["unknown","disconnected","stopped","remote_idle"])expect(chatActivity(resource,{},[{key:resource.key,status}]).running,status).toBe(false)});
 it("shows a waiting state for an explicit decision even without an attention record",()=>{const a=chatActivity(resource,{},[{key:resource.key,status:"waiting_input"}]);expect(a.awaiting).toBe(true);expect(a.running).toBe(false)});
 it("does not revive a terminal run from an older live observation",()=>{const now=Date.now();const a=chatActivity(resource,{},[{key:resource.key,status:"completed",finished_at:new Date(now).toISOString()}],[],{},[{chat_key:resource.key,alive:true,status:"running",observed_at:(now-5000)/1000}]);expect(a.running).toBe(false)});
});
