import {describe,it,expect} from 'vitest';
import {createConversationStore} from './conversations';
describe('remote control command draft',()=>{
 for(const text of ['/remote-control','/remote control','/rc','/RC  ',' /rc ',' /remote control '])it('consumes accepted '+text,()=>{
  const store=createConversationStore();store.get('chat',{text});store.prepare('chat',{operation:'remote_control',message:'/remote-control'});store.accepted('chat',{id:'run'});expect(store.get('chat').text).toBe('');
 });
 it('retains an ordinary draft when connecting by button',()=>{
  const store=createConversationStore();store.get('chat',{text:'unfinished'});store.prepare('chat',{operation:'remote_control',message:'/remote-control'});store.accepted('chat',{id:'run'});expect(store.get('chat').text).toBe('unfinished');
 });
 it('retains a newer draft written during connection',()=>{
  const store=createConversationStore();store.get('chat',{text:'/rc'});store.prepare('chat',{operation:'remote_control',message:'/remote-control'});store.patch('chat',{text:'newer draft'});store.accepted('chat',{id:'run'});expect(store.get('chat').text).toBe('newer draft');
 });
 it('does not consume unrelated commands',()=>{
  const store=createConversationStore();store.get('chat',{text:'/compact'});store.prepare('chat',{operation:'remote_control',message:'/remote-control'});store.accepted('chat',{id:'run'});expect(store.get('chat').text).toBe('/compact');
 });
});
