import {describe,it,expect} from 'vitest';
import {agentMapStatus} from './AgentMap';
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
