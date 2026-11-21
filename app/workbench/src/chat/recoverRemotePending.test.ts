import {describe,it,expect,vi} from 'vitest';
import {createConversationStore} from '../state/conversations';
import {recoverRemotePending} from './recoverRemotePending';
describe('unconfirmed Remote Control recovery',()=>{
 it('keeps the current draft and records provenance before clearing the old token',async()=>{
  const store=createConversationStore(),pending={runId:'old',requestId:'req',text:'unconfirmed'};store.get('chat',{text:'newer',remotePending:pending});
  const flush=vi.fn(async()=>{if(flush.mock.calls.length===1)expect(store.get('chat').remotePending).toEqual(pending)});
  await recoverRemotePending(store,'chat',flush);expect(flush).toHaveBeenCalledTimes(2);expect(store.get('chat').text).toBe('newer');expect(store.get('chat').remotePending).toBeUndefined();expect(store.get('chat').conflicts[0]).toMatchObject({text:'unconfirmed',recoveryId:'req',source:expect.stringContaining('old')});
 });
 it('retains the pending token when durable flush fails',async()=>{
  const store=createConversationStore(),pending={runId:'old',requestId:'req',text:'unconfirmed'};store.get('chat',{text:'newer',remotePending:pending});
  await expect(recoverRemotePending(store,'chat',async()=>{throw Error('storage unavailable')})).rejects.toThrow('storage unavailable');expect(store.get('chat').remotePending).toEqual(pending);expect(store.get('chat').text).toBe('newer');
 });
 it('retains the pending token when local storage fails',async()=>{
  const storage={getItem:()=>null,setItem:()=>{throw Error('full')}};const store=createConversationStore(storage),pending={runId:'old',requestId:'req',text:'unconfirmed'};store.get('chat',{text:'newer',remotePending:pending});const flush=vi.fn();
  await expect(recoverRemotePending(store,'chat',flush)).rejects.toThrow('Could not save');expect(store.get('chat').remotePending).toEqual(pending);expect(flush).not.toHaveBeenCalled();
 });
});

it('does not overwrite a newer request when recovery receipt storage fails',async()=>{
 const store=createConversationStore(),pending={runId:'old',requestId:'old-token',text:'unconfirmed'},newer={runId:'new',requestId:'new-token',text:'new message'};store.get('chat',{text:'draft',remotePending:pending});let calls=0;
 await expect(recoverRemotePending(store,'chat',async()=>{if(++calls===2){store.patch('chat',{remotePending:newer,text:'new draft'});throw Error('disk failed')}})).rejects.toThrow('disk failed');expect(store.get('chat').remotePending).toEqual(newer);expect(store.get('chat').text).toBe('new draft');expect(store.get('chat').conflicts[0].text).toBe('unconfirmed');
});
