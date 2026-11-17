import {describe,it,expect} from 'vitest';
import {createConversationStore} from './conversations';
describe('submission snapshot while awaiting host probe or lock',()=>{
 it('clears only the sent text and attachment IDs, retaining newer unsent edits',()=>{
  const store=createConversationStore();
  const sent={id:'sent',name:'a.png',mime:'image/png',size:1};
  const later={id:'later',name:'b.png',mime:'image/png',size:1};
  store.patch('a',{text:'original',attachments:[sent]});
  const snapshot=store.get('a');
  store.patch('a',{text:'new unsent text',attachments:[sent,later]});
  const payload=store.prepare('a',{message:snapshot.text,attachments:[sent.id]},snapshot);
  expect(payload.message).toBe('original');
  store.accepted('a',{id:'run',key:'native'});
  expect(store.get('a').text).toBe('new unsent text');
  expect(store.get('a').attachments).toEqual([later]);
 });
 it('keeps the exact original submission on retry even after edits',()=>{
  const store=createConversationStore();store.patch('a',{text:'/goal original'});const snapshot=store.get('a');
  const first=store.prepare('a',{message:'original',operation:'goal'},snapshot);
  store.patch('a',{text:'later'});expect(store.prepare('a',{message:'later'},store.get('a'))).toBe(first);
  store.accepted('a',{id:'run'});expect(store.get('a').text).toBe('later');
 });
});
