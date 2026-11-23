import {describe,it,expect} from 'vitest';
import {accountRows,displayName,secondaryEmail,barHot} from './accountRows';
describe('account rows',()=>{
 const value={accounts:[{id:'c1',tool:'claude',label:'personal',email:'a@x.test'},{id:'c1d',tool:'claude',label:'dup',email:'a@x.test',duplicate_of:'c1'}],pending_accounts:[{id:'c2',tool:'claude',label:'Easy Labs',previously_used:true,logged_in:false},{id:'x2',tool:'codex',label:'Account 2',previously_used:false}]};
 it('puts a signed-out used account in its group and an unfinished setup apart',()=>{
  const rows=accountRows(value);
  expect(rows.claude.map(a=>a.id)).toEqual(['c1','c2']);expect(rows.codex).toEqual([]);expect(rows.incomplete.map(a=>a.id)).toEqual(['x2']);
 });
 it('without previously_used (old server) every pending row stays incomplete',()=>expect(accountRows({accounts:[],pending_accounts:[{id:'p',tool:'claude'}]}).incomplete.length).toBe(1));
 it('shows the email beside a name, never twice',()=>{
  expect(displayName({label:'personal',email:'a@x.test'})).toBe('personal');expect(secondaryEmail({label:'personal',email:'a@x.test'})).toBe('a@x.test');
  expect(secondaryEmail({label:'a@x.test',email:'a@x.test'})).toBe('');expect(displayName({email:'a@x.test'})).toBe('a@x.test');
 });
 it('turns a bar red at 90 percent',()=>{expect(barHot(89.9)).toBe(false);expect(barHot(90)).toBe(true);});
});
