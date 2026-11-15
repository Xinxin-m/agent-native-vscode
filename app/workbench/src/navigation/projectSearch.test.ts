import {describe,it,expect} from 'vitest';
import {searchProjectPage} from './projectSearch';
describe('parent project transcript search compatibility',()=>{
 it('keeps global order and every match across independently paged children',async()=>{
  const rows={parent:[{key:'b'},{key:'e'}],one:[{key:'a'},{key:'d'}],two:[{key:'c'},{key:'f'}]};
  const chats=['a','b','c','d','e','f'].map((key,i)=>({key,ts_last:String(9-i)}));
  const fetch=async(id:string,cursor:string)=>({results:rows[id].slice(Number(cursor||0),Number(cursor||0)+1),next_cursor:Number(cursor||0)+1<rows[id].length?String(Number(cursor||0)+1):null});
  const result=[];let cursor='';do{const page=await searchProjectPage(['parent','one','two'],fetch,chats,cursor,2);result.push(...page.results.map(r=>r.key));cursor=page.next_cursor||'';}while(cursor);
  expect(result).toEqual(['a','b','c','d','e','f']);
 });
 it('deduplicates descendant matches already returned by a new parent backend',async()=>{
  const rows={parent:[{key:'a'},{key:'b'}],child:[{key:'a'}]};
  const fetch=async(id:string)=>({results:rows[id],next_cursor:null});
  const chats=[{key:'a',ts_last:'2'},{key:'b',ts_last:'1'}];
  const first=await searchProjectPage(['parent','child'],fetch,chats,'',1);
  const second=await searchProjectPage(['parent','child'],fetch,chats,first.next_cursor!,1);
  expect([...first.results,...second.results].map(r=>r.key)).toEqual(['a','b']);
  if(second.next_cursor){const end=await searchProjectPage(['parent','child'],fetch,chats,second.next_cursor,1);expect(end.results).toEqual([]);expect(end.next_cursor).toBeNull();}
 });
 it('handles empty child results',async()=>{
  const calls:string[]=[];
  const page=await searchProjectPage(['parent','empty'],async(id,cursor)=>{calls.push(id+cursor);return {results:id==='parent'?[{key:'a'}]:[],next_cursor:null};},[{key:'a'}]);
  expect(page.results.map(r=>r.key)).toEqual(['a']);expect(page.next_cursor).toBeNull();expect(calls).toEqual(['parent','empty']);
 });
});
