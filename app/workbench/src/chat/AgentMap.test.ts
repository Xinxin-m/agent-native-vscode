import {describe,it,expect} from 'vitest';
import {agentMapStatus,agentStatusLabel,orderAgentTree,agentCounts} from './AgentMap';
describe('agent status evidence',()=>{
 const node={key:'claude-a',source:'claude',id:'a'};
 it('requires matching explicit identity and fresh observations',()=>{
  expect(agentMapStatus(node,[],{sessions:[{chat_key:'claude-a',alive:true,status:'running',observed_at:100}],nodes:[{name:'mac',ok:true}]},100)).toBe('running');
  expect(agentMapStatus(node,[],{sessions:[{chat_key:'claude-a',alive:true,status:'running',observed_at:100}],nodes:[{name:'mac',ok:true}]},300)).toBe('unknown');
  expect(agentMapStatus(node,[],{sessions:[{chat_key:'claude-a',alive:true,status:'?',observed_at:100}],nodes:[]},100)).toBe('unknown');
  expect(agentMapStatus(node,[],{sessions:[{chat_key:'other',alive:true,status:'running',observed_at:100}],nodes:[]},100)).toBe('unknown');
 });
 it('recognizes recorded completion but lets current verified activity win',()=>{
  const completed={...node,status:'complete'};
  expect(agentMapStatus(completed,[],null,100)).toBe('finished');
  expect(agentMapStatus({...node,status:'working'},[],null,100)).toBe('unknown');
  expect(agentMapStatus(completed,[{key:node.key,status:'running'}],null,100)).toBe('running');
  expect(agentMapStatus(completed,[],{sessions:[{chat_key:node.key,alive:true,status:'running',observed_at:100}],nodes:[]},100)).toBe('running');
 });
 it('distinguishes explicit broker states',()=>{
  expect(agentMapStatus(node,[{conversation_key:'claude-a',status:'waiting_input'}],null,100)).toBe('waiting');
  expect(agentMapStatus(node,[{conversation_key:'claude-a',status:'completed'}],null,100)).toBe('finished');
  expect(agentMapStatus(node,[{conversation_key:'claude-a',status:'completed'}],{sessions:[{chat_key:'claude-a',alive:true,observed_at:100}],nodes:[]},100)).toBe('unknown');
  expect(agentMapStatus(node,[{conversation_key:'claude-a',status:'running'}],null,100)).toBe('running');
 });
});
describe('transcript activity and ordering',()=>{
 it('maps server activity for subagents that have no run or process',()=>{
  expect(agentMapStatus({key:'c',activity:'running'},[],null,100)).toBe('running');
  expect(agentMapStatus({key:'c',activity:'finished',status:'working'},[],null,100)).toBe('finished');
  expect(agentMapStatus({key:'c',activity:'stopped'},[],null,100)).toBe('finished');
  expect(agentStatusLabel({activity:'stopped'},'finished')).toBe('Stopped');
  expect(agentMapStatus({key:'c',activity:'failed'},[],null,100)).toBe('failed');
  expect(agentMapStatus({key:'c',activity:'unknown',status:'working'},[],null,100)).toBe('unknown');
  expect(agentMapStatus({key:'c',activity:'finished'},[{key:'c',status:'waiting_input'}],null,100)).toBe('waiting');
 });
 it('puts running branches first, then waiting, newest first within a group',()=>{
  const nodes=[{key:'root',ts_last:'1'},{key:'old-done',ts_last:'2',activity:'finished'},{key:'new-done',ts_last:'9',activity:'finished'},{key:'wait',ts_last:'3'},{key:'busy',ts_last:'1',activity:'running'},{key:'parent',ts_last:'0',activity:'finished'},{key:'deep',ts_last:'0',activity:'running'}];
  const edges=['old-done','new-done','wait','busy','parent'].map(k=>({parent_key:'root',child_key:k})).concat([{parent_key:'parent',child_key:'deep'}]);
  const tree=orderAgentTree(nodes,edges,n=>n.key==='wait'?'waiting':agentMapStatus(n,[],null,100));
  expect(tree.map(i=>i.node.key)).toEqual(['root']);
  expect(tree[0].children.map(i=>i.node.key)).toEqual(['busy','parent','wait','new-done','old-done']);
  expect(tree[0].children[1].status).toBe('finished');
  expect(agentCounts(tree)).toEqual({running:2,finished:3,waiting:1,unknown:1});
 });
 it('survives a lineage cycle',()=>{
  const tree=orderAgentTree([{key:'a'},{key:'b'}],[{parent_key:'a',child_key:'b'},{parent_key:'b',child_key:'a'}],()=>'unknown');
  expect(tree).toEqual([]);
 });
});
