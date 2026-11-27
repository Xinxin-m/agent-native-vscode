import {describe,it,expect} from 'vitest';
import {stoppableIdle} from './sessionControl';
import {rowStatus,relativeTime,cutTitle,inSidebarList,statusWords,matchesSessionId,historyChats} from './chats';
describe('row status priority',()=>{
 it('failed beats waiting beats working beats new; live idle has no mark',()=>{
  expect(rowStatus('waiting',true)).toBe('waiting');
  expect(rowStatus('working',true)).toBe('working');
  expect(rowStatus('idle',true)).toBe('new');
  expect(rowStatus(null,true)).toBe('new');
  expect(rowStatus('idle',false)).toBe(null);
  expect(rowStatus('idle',true,{failed:true})).toBe('failed');
  expect(rowStatus('working',false,{failed:true})).toBe('failed');
  expect(rowStatus('waiting',true,{failed:true,running:true})).toBe('failed');
  expect(rowStatus('stalled',true,{running:true})).toBe('stalled');
  expect(Object.keys(statusWords).sort()).toEqual(['failed','new','stalled','waiting','working']);
  expect(rowStatus(null,false)).toBe(null);
 });
 it('local broker state counts as waiting or working',()=>{expect(rowStatus(null,false,{awaiting:true})).toBe('waiting');expect(rowStatus('idle',true,{running:true})).toBe('working');});
});
describe('search by session ID',()=>{
 const chat={key:'claude-abcdef12-3456-4abc-8def-012345678901',id:'abcdef12-3456-4abc-8def-012345678901',title:'Weekly summary'};
 it('full ID, prefix, key prefix, any case; short or non-hex queries do not',()=>{
  expect(matchesSessionId(chat,'abcdef12-3456-4abc-8def-012345678901')).toBe(true);
  expect(matchesSessionId(chat,'ABCDEF')).toBe(true);
  expect(matchesSessionId(chat,'claude-abcdef12')).toBe(true);
  expect(matchesSessionId(chat,'abcde')).toBe(false);
  expect(matchesSessionId(chat,'weekly')).toBe(false);
  expect(matchesSessionId(chat,'abcdef12-3456-4abc-8def-012345678902')).toBe(false);
 });
 it('history search finds a sub-agent by its ID, first',()=>{
  const rows=[{key:'claude-root',title:'abcdef12 notes',ts_last:'2026-11-23'},{...chat,parent_key:'claude-root',ts_last:'2026-11-01'}];
  expect(historyChats(rows,'','abcdef12').map(c=>c.key)).toEqual([chat.key,'claude-root']);
 });
});
describe('relative time',()=>{
 const now=Date.parse('2026-11-22T12:00:00Z');
 it('minutes and hours inside 24 h',()=>{expect(relativeTime('2026-11-22T11:48:00Z',now)).toBe('12 min ago');expect(relativeTime('2026-11-22T09:00:00Z',now)).toBe('3 h ago');expect(relativeTime('2026-11-22T11:59:40Z',now)).toBe('just now');});
 it('older than 24 h shows the date, never Today',()=>{const text=relativeTime('2026-11-20T12:00:00Z',now);expect(text).not.toMatch(/ago|Today/);expect(text).toMatch(/\d/);});
});
describe('title cut',()=>{
 it('keeps 24 characters plus an ellipsis',()=>{expect(cutTitle('x'.repeat(40))).toBe('x'.repeat(24)+'…');expect(cutTitle('short')).toBe('short');expect(cutTitle('y'.repeat(24))).toBe('y'.repeat(24));});
});
describe('sidebar list filter',()=>{
 const chats=[{key:'a'},{key:'b',starred:true},{key:'c',archived:true},{key:'d',project_id:'p'},{key:'e',is_child:true}];
 const keys=(list:string)=>chats.filter(c=>inSidebarList(c,list)).map(c=>c.key);
 it('lists',()=>{expect(keys('recent')).toEqual(['a','b','d','e']);expect(keys('starred')).toEqual(['b']);expect(keys('archive')).toEqual(['c']);expect(keys('unclassified')).toEqual(['a','b']);});
});
describe('stoppable idle sessions',()=>{
 const base:any={alive:true,live_status:'idle',stop_allowed:true,observed_at:1000};
 it('keeps only fresh, idle, stop-allowed rows',()=>{
  const rows=[{...base,id:'a'},{...base,id:'busy',live_status:'working'},{...base,id:'wait',live_status:'waiting'},{...base,id:'locked',stop_allowed:false},{...base,id:'old',observed_at:100},{...base,id:'dead',alive:false}];
  expect(stoppableIdle(rows as any,1050).map((s:any)=>s.id)).toEqual(['a']);
 });
});
