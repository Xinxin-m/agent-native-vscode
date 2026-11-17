import {describe,it,expect,vi} from 'vitest';
import {createConversationStore} from '../state/conversations';
import {sendRemoteFollowup} from './remoteFollowup';

describe('remote follow-ups',()=>{
 it('retries the same request on the same run and preserves the changed draft',async()=>{
  const store=createConversationStore();store.get('chat',{text:'first'});
  const control=vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValueOnce({ok:true});
  await expect(sendRemoteFollowup(store,'chat','run-1','first',true,control)).rejects.toThrow('offline');
  const pending=store.get('chat').remotePending!;expect(store.get('chat').text).toBe('first');
  store.patch('chat',{text:'new draft'});
  await sendRemoteFollowup(store,'chat','run-1','new draft',true,control);
  expect(control.mock.calls[1]).toEqual(['run-1','message',{message:'first',request_id:pending.requestId}]);
  expect(store.get('chat').text).toBe('new draft');expect(store.get('chat').remotePending).toBeUndefined();
 });
 it('rejects a retry bound to a different run',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft',remotePending:{runId:'old',requestId:'id',text:'draft'}});const control=vi.fn();
  await expect(sendRemoteFollowup(store,'chat','new','draft',true,control)).rejects.toThrow('different remote connection');expect(control).not.toHaveBeenCalled();expect(store.get('chat').text).toBe('draft');
 });
 it('does not consume a draft when connecting remote control',()=>{
  const store=createConversationStore();store.get('chat',{text:'unsent'});store.prepare('chat',{operation:'remote_control',message:'/remote-control'});store.accepted('chat',{id:'run',provider:'claude'});expect(store.get('chat').text).toBe('unsent');expect(store.get('chat').sent).toEqual([]);
 });
});

describe('durable follow-up receipts',()=>{
 const replacement={runId:'next',requestId:'new-token',text:'new message'};
 it('does not send when synchronous storage fails',async()=>{
  const store=createConversationStore({getItem:()=>null,setItem:()=>{throw Error('full')}});store.get('chat',{text:'draft'});const control=vi.fn();
  await expect(sendRemoteFollowup(store,'chat','run','draft',true,control)).rejects.toThrow('Could not save');expect(control).not.toHaveBeenCalled();expect(store.get('chat').text).toBe('draft');
 });
 it('waits for durable storage acknowledgment before delivery',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});let release!:()=>void;const first=new Promise<void>(resolve=>{release=resolve});
  const flush=vi.fn().mockReturnValueOnce(first).mockResolvedValue(undefined),control=vi.fn().mockResolvedValue({});
  const sending=sendRemoteFollowup(store,'chat','run','draft',true,control,flush);expect(control).not.toHaveBeenCalled();release();await sending;expect(control).toHaveBeenCalledOnce();expect(flush).toHaveBeenCalledTimes(2);
 });
 it('does not send after a failed flush',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});const control=vi.fn();
  await expect(sendRemoteFollowup(store,'chat','run','draft',true,control,async()=>{throw Error('disk failed')})).rejects.toThrow('disk failed');expect(control).not.toHaveBeenCalled();expect(store.get('chat').text).toBe('draft');expect(store.get('chat').remotePending).toBeTruthy();
 });
 it('does not deliver a request superseded during the first flush',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});const control=vi.fn();
  await expect(sendRemoteFollowup(store,'chat','run','draft',true,control,async()=>{store.patch('chat',{remotePending:replacement})})).rejects.toThrow('changed before delivery');expect(control).not.toHaveBeenCalled();expect(store.get('chat').remotePending).toEqual(replacement);
 });
 it('does not clear a newer pending request after the old receipt',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});
  await sendRemoteFollowup(store,'chat','run','draft',true,async()=>{store.patch('chat',{text:'new draft',remotePending:replacement})});expect(store.get('chat').remotePending).toEqual(replacement);expect(store.get('chat').text).toBe('new draft');
 });
 it('retains the request and draft when the receipt cannot be saved',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});const control=vi.fn().mockResolvedValue({}),flush=vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(Error('receipt failed')).mockResolvedValue(undefined);
  await expect(sendRemoteFollowup(store,'chat','run','draft',true,control,flush)).rejects.toThrow('receipt failed');expect(store.get('chat').text).toBe('draft');expect(store.get('chat').remotePending?.requestId).toBe(control.mock.calls[0][2].request_id);
 });
 it('does not replace a newer request while recovering a failed receipt save',async()=>{
  const store=createConversationStore();store.get('chat',{text:'draft'});let calls=0;
  await expect(sendRemoteFollowup(store,'chat','run','draft',true,async()=>{},async()=>{if(++calls===2){store.patch('chat',{text:'new draft',remotePending:replacement});throw Error('receipt failed')}})).rejects.toThrow('receipt failed');expect(store.get('chat').remotePending).toEqual(replacement);expect(store.get('chat').text).toBe('new draft');
 });
});
