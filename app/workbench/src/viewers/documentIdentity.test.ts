import {describe,it,expect} from 'vitest';
import {documentKey,unassignedDrafts} from './documents';
describe('file reference identity',()=>{
 it('preserves existing path keys and separates pathless exact references',()=>{expect(documentKey({kind:'file',host_id:'cluster',path:'/a.md'})).toBe(JSON.stringify(['cluster','/a.md']));expect(documentKey({kind:'file',host_id:'cluster',file_id:'a'})).not.toBe(documentKey({kind:'file',host_id:'cluster',file_id:'b'}));});
 it('exposes old empty-path drafts as unassigned without restoring them',()=>{const old={text:'old',baseText:'',writer:'prior',updatedAt:1,storageKey:'key'};const repo={list:(id:string)=>id===JSON.stringify(['cluster',''])?[old]:[]};expect(unassignedDrafts({kind:'file',host_id:'cluster',file_id:'a'},repo)).toEqual([old]);expect(unassignedDrafts({kind:'file',host_id:'cluster',path:'/a.md'},repo)).toEqual([])});
});
