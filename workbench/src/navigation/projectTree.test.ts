import {describe,it,expect} from 'vitest';
import {projectTree,projectMembers} from './projectTree';
describe('project hierarchy',()=>{
 it('aggregates children and keeps a distinct child project',()=>{const projects=[{id:'parent',name:'Applications',pinned:true},{id:'child',parent_id:'parent',name:'Application'}];const tree=projectTree(projects,[{key:'a',ts_last:'2026-11-15'}],{a:'child'});expect(tree.map(p=>[p.id,p.depth,p.chats.length])).toEqual([['parent',0,1],['child',1,1]]);});
 it('bounds malformed cycles',()=>expect([...projectMembers([{id:'a',parent_id:'b'},{id:'b',parent_id:'a'}],'a')]).toEqual(['a','b']));
});
