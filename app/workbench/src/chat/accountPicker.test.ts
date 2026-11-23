import {describe,it,expect} from 'vitest';
import {pickerText,pickerOptions,autoText} from './accountPicker';
import {accountRows} from '../navigation/accountRows';
const picker={accounts:[{id:'c1',tool:'claude',label:'personal',email:'a@x.test'},{id:'c2',tool:'claude',label:'Easy Labs',email:'b@x.test'},{id:'x1',tool:'codex',label:'',email:'c@x.test'}],auto:{claude:{account_id:'c2',label:'Easy Labs',email:'b@x.test'},codex:null}};
describe('model picker accounts',()=>{
 it('shows the name with the email beside it, the email alone without a name',()=>{expect(picker.accounts.map(pickerText)).toEqual(['personal · a@x.test','Easy Labs · b@x.test','c@x.test']);});
 it('lists one provider',()=>expect(pickerOptions(picker,'codex').map(a=>a.id)).toEqual(['x1']));
 it('names the account Auto resolves to',()=>{expect(autoText(picker,'claude')).toBe('Auto · Easy Labs · b@x.test');expect(autoText(picker,'codex')).toBe('Auto');expect(autoText(null,'claude')).toBe('Auto');});
 it('matches the Accounts tab: signed-in ids, duplicates hidden',()=>{
  const value={accounts:[{id:'c1',tool:'claude',email:'a@x.test',label:'personal'},{id:'c1b',tool:'claude',email:'a@x.test',label:'Easy',duplicate_of:'c1'},{id:'c2',tool:'claude',email:'b@x.test',label:'Easy Labs'},{id:'x1',tool:'codex',email:'c@x.test',label:''}],pending_accounts:[]};
  const rows=accountRows(value),tab=[...rows.claude,...rows.codex].filter(a=>a.logged_in!==false).map(a=>a.id).sort();
  expect(tab).toEqual(picker.accounts.map(a=>a.id).sort());
 });
});
