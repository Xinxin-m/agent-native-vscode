import {describe,it,expect} from 'vitest';
import {sameFile} from './helpers';
import {documentKey,unassignedDrafts} from './documents';
describe('file reference identity',()=>{
 it('preserves existing path keys and separates pathless exact references',()=>{expect(documentKey({kind:'file',host_id:'cluster',path:'/a.md'})).toBe(JSON.stringify(['cluster','/a.md']));expect(documentKey({kind:'file',host_id:'cluster',file_id:'a'})).not.toBe(documentKey({kind:'file',host_id:'cluster',file_id:'b'}));});
 it('matches canonical, scoped and exact addresses without conflating roots or hosts',()=>{
  const left={kind:'file' as const,root_id:'a',path:'report.md'};
  expect(sameFile(left,{...left,root_id:'b'})).toBe(false);
  expect(documentKey(left)).not.toEqual(documentKey({...left,root_id:'b'}));
  expect(sameFile(left,{root_id:'a',relative_path:'report.md',path:'/workspace/a/report.md'})).toBe(true);
  expect(sameFile({file_id:'exact'},{file_id:'exact',path:'/workspace/report.md'})).toBe(true);
  expect(sameFile({path:'/report.md',host_id:'sc'},{path:'/report.md',host_id:'mac'})).toBe(false);
 });
 it('exposes old empty-path drafts as unassigned without restoring them',()=>{const old={text:'old',baseText:'',writer:'prior',updatedAt:1,storageKey:'key'};const repo={list:(id:string)=>id===JSON.stringify(['cluster',''])?[old]:[]};expect(unassignedDrafts({kind:'file',host_id:'cluster',file_id:'a'},repo)).toEqual([old]);expect(unassignedDrafts({kind:'file',host_id:'cluster',path:'/a.md'},repo)).toEqual([])});
});
