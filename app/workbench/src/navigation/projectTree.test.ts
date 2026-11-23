import {describe,it,expect} from 'vitest';
import {projectTree,projectMembers} from './projectTree';
describe('project hierarchy',()=>{
 it('aggregates children and keeps a distinct child project',()=>{const projects=[{id:'parent',name:'Applications',pinned:true},{id:'child',parent_id:'parent',name:'Application'}];const tree=projectTree(projects,[{key:'a',ts_last:'2026-11-15'}],{a:'child'});expect(tree.map(p=>[p.id,p.depth,p.chats.length])).toEqual([['parent',0,1],['child',1,1]]);});
 it('bounds malformed cycles',()=>expect([...projectMembers([{id:'a',parent_id:'b'},{id:'b',parent_id:'a'}],'a')]).toEqual(['a','b']));
});

describe('workspace-scoped project activity',()=>{
 it('orders and dates projects by selected-workspace chats while retaining global eligibility',()=>{
  const now=Date.now(),stamp=(days:number)=>new Date(now-days*86400000).toISOString();
  const projects=[{id:'a',name:'A'},{id:'b',name:'B'}],assignments:any={};
  const library=['a','b'].flatMap(id=>Array.from({length:5},(_,i)=>{const key=id+i;assignments[key]=id;return {key,ts_last:stamp(i===0?(id==='a'?10:1):0)};}));
  const scoped=[library.find(c=>c.key==='b0')!,library.find(c=>c.key==='a0')!];
  const result=projectTree(projects,scoped,assignments,library);
  expect(result.map(p=>p.id)).toEqual(['b','a']);expect(result[0].chats).toHaveLength(1);expect(result[0].active).toBe(true);expect(result[1].active).toBe(false);expect(result[1].last).toBe(stamp(10));
 });
});

it('a pinned project stays listed (a drop target) even with few chats',()=>{const tree=projectTree([{id:'p',name:'P',pinned:true},{id:'q',name:'Q'}],[{key:'a',ts_last:'2026-11-15'}],{a:'p'});expect(tree.find(p=>p.id==='p')!.active).toBe(true);expect(tree.find(p=>p.id==='q')!.active).toBe(false);});
