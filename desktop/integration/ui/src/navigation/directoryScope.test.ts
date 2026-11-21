import {describe,expect,it} from 'vitest';
import {directoryChats,directoryProjects} from './directoryScope';
import {effectiveWorkspaceChoice,workspaceKey} from './workspaceScope';

const mac={host_id:'mac',path:'/Projects'},cluster={host_id:'cluster',path:'/Projects'},roots=[mac,cluster];
const chat=(key:string,node:string,ts_last:string,cwd='/Projects/sub')=>({key,node,ts_last,cwd});
const chats=[
 chat('mixed-cluster','cluster','2026-11-20'),chat('mixed-mac','mac','2026-11-14'),
 chat('local','mac','2026-11-18'),chat('remote','cluster','2026-11-17'),
 chat('outside','mac','2026-11-19','/Projects-old'),chat('ambiguous','mac+cluster','2026-11-21'),
 {...chat('child','mac','2026-11-21'),parent_key:'local'},
 {...chat('archived','mac','2026-11-21'),archived:true},
 {...chat('automation','mac','2026-11-21'),automation:true}
];
const projects=[{id:'mixed',name:'Mixed'},{id:'local',name:'Local'},{id:'remote',name:'Remote'},
 {id:'empty',name:'Empty'},{id:'outside',name:'Outside',archived:true}];
const assignments={'mixed-cluster':'mixed','mixed-mac':'mixed',local:'local',remote:'remote',outside:'outside',child:'local',archived:'local',automation:'local'};
const scope=(choice:string)=>({roots,choice});

describe('workspace directories',()=>{
 it('lists recent human chats from the selected execution host and folder',()=>{
  expect(directoryChats(chats,scope('added')).map(c=>c.key)).toEqual(['mixed-cluster','local','remote','mixed-mac']);
  expect(directoryChats(chats,scope(workspaceKey(cluster))).map(c=>c.key)).toEqual(['mixed-cluster','remote']);
  expect(directoryChats(chats,scope('entire')).map(c=>c.key)).toContain('outside');
 });
 it('derives visible projects, counts, dates and ordering from scoped chats',()=>{
  const rows=directoryProjects(projects,chats,assignments,scope(workspaceKey(mac)));
  expect(rows.map(p=>[p.id,p.chats.length,p.last])).toEqual([['local',1,'2026-11-18'],['mixed',1,'2026-11-14']]);
  const both=directoryProjects(projects,chats,assignments,scope('added'));
  expect(both[0].chats.map(c=>c.key)).toEqual(['mixed-cluster','mixed-mac']);
  expect(directoryProjects(projects,chats,assignments,scope('entire')).map(p=>p.id)).toContain('empty');
 });
 it('falls back to All chats if the selected root disappears',()=>{
  expect(effectiveWorkspaceChoice(workspaceKey(cluster),[mac])).toBe('entire');
  expect(effectiveWorkspaceChoice('added',[])).toBe('entire');
  expect(effectiveWorkspaceChoice('entire',roots)).toBe('entire');
 });
});
