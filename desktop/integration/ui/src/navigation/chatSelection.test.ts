import {describe,it,expect,vi} from 'vitest';
import {selectRange,toggleSelection,applyToEach,createdProjectId} from './chatSelection';
describe('chat selection',()=>{
 it('toggles identities and selects a visible range, deduplicating repeated rows',()=>{
  expect([...toggleSelection(new Set(['a']),'b')]).toEqual(['a','b']);
  expect([...toggleSelection(new Set(['a']),'a')]).toEqual([]);
  expect([...selectRange(['a','b','a','c'],1,3)]).toEqual(['b','a','c']);
 });
 it('reports successes and keeps only failed keys on partial mutation failure',async()=>{
  const call=vi.fn(async(key:string)=>{if(key==='b')throw Error('denied');});
  const result=await applyToEach(['a','b','c'],call);expect(result.succeeded).toEqual(['a','c']);expect(result.failed.map(x=>x.key)).toEqual(['b']);expect(result.failed[0].message).toBe('denied');
 });
 it('identifies only a unique newly-created project with the requested name',()=>{
  expect(createdProjectId([{id:'a',name:'Old'},{id:'b',name:'New'}],new Set(['a']),'New')).toBe('b');
  expect(()=>createdProjectId([{id:'b',name:'New'},{id:'c',name:'New'}],new Set(),'New')).toThrow();
 });
});
