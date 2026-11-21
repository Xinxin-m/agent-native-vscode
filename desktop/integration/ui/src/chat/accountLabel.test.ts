import {describe,it,expect} from 'vitest';
import {accountLabel} from './helpers';
const accounts=[{id:'claude-default',tool:'claude',label:'Main',email:'me@example.test',plan:'Max 20x'},{id:'claude-alt',tool:'claude',label:'Alt',email:'me@example.test',plan:'Max 20x'},{id:'claude-solo',tool:'claude',label:'Solo',email:'solo@example.test'}];
describe('account labels',()=>{
 it('names the profile when two accounts share one email',()=>{
  expect(accounts.map(a=>accountLabel(a,accounts))).toEqual(['Main · me@example.test (Max 20x)','Alt · me@example.test (Max 20x)','solo@example.test']);
 });
 it('keeps the email alone without a peer list',()=>expect(accountLabel(accounts[0])).toBe('me@example.test (Max 20x)'));
});
