import {describe,it,expect,vi,afterEach} from 'vitest';
import * as client from '../api/client';
import {copyableLink,decodeResource,parseRenderedLink} from '../open/links';
import {resolveFile} from './documents';
afterEach(()=>vi.restoreAllMocks());
describe('exact file reference refresh',()=>{
 const resource={kind:'file' as const,host_id:'cluster',path:'/work/report.md',file_id:'expired',meta:{chat_key:'thread'}};
 it('refreshes only the evidenced exact file after an expired capability',async()=>{const api=vi.spyOn(client,'api').mockRejectedValueOnce(new client.ApiError('expired',404)).mockResolvedValueOnce({resource:{file_id:'fresh',host_id:'cluster',path:'/work/report.md'}});expect((await resolveFile(resource)).file_id).toBe('fresh');expect(api.mock.calls[1]).toEqual(['/api/files/reference',{chat_key:'thread',path:'/work/report.md'}]);});
 it('does not refresh missing provenance or non-expiry failures',async()=>{const api=vi.spyOn(client,'api').mockRejectedValue(new client.ApiError('denied',403));await expect(resolveFile(resource)).rejects.toThrow('denied');expect(api).toHaveBeenCalledTimes(1);api.mockReset().mockRejectedValue(new client.ApiError('expired',404));await expect(resolveFile({...resource,meta:{}})).rejects.toThrow('expired');expect(api).toHaveBeenCalledTimes(1)});
 it('rejects a refreshed reference from a different host',async()=>{vi.spyOn(client,'api').mockRejectedValueOnce(new client.ApiError('expired',404)).mockResolvedValueOnce({resource:{file_id:'fresh',host_id:'mac'}});await expect(resolveFile(resource)).rejects.toThrow('different host')});
 it('resolves a cluster PDF in a Mac conversation through its exact chat reference',async()=>{
  const path='/cluster/data/research/J_windows.pdf';
  const record={file_id:'pdf',host_id:'configured-cluster',path,mime:'application/pdf'};
  const api=vi.spyOn(client,'api').mockResolvedValueOnce({resource:record});
  await expect(resolveFile({kind:'file',host_id:'mac',path,meta:{chat_key:'codex-123'}})).resolves.toEqual(record);
  expect(api.mock.calls[0]).toEqual(['/api/files/reference',{chat_key:'codex-123',path}]);
 });
 it('uses the relocated markout path returned for an old chat link',async()=>{
  const record={file_id:'moved',host_id:'mac',path:'/work/current/markout.html',size:128};
  const api=vi.spyOn(client,'api').mockResolvedValueOnce({resource:record});
  await expect(resolveFile({kind:'file',path:'/work/old.html',meta:{chat_key:'thread'}})).resolves.toEqual(record);
  expect(api).toHaveBeenCalledWith('/api/files/reference',{chat_key:'thread',path:'/work/old.html'});
 });
 it('rejects a root ID paired with a path outside that root',async()=>{
  const api=vi.spyOn(client,'api').mockResolvedValueOnce({roots:[{id:'project',path:'/cluster/project',host_id:'cluster'}]});
  await expect(resolveFile({kind:'file',host_id:'cluster',root_id:'project',path:'/cluster/private/report.pdf'})).rejects.toThrow('outside');
  expect(api).toHaveBeenCalledTimes(1);
 });
});

describe('first-party SC report links',()=>{
 const origin='http://127.0.0.1:8744';
 const folder='/workspace/reports/weekly';
 const path=folder+'/weekly-activity.html';
 const link=copyableLink({kind:'file',host_id:'sc',path,mode:'render',meta:{chat_key:'source-chat'}},origin);

 it('uses the registered folder for clicks in chat and for direct URL navigation',async()=>{
  const file={host_id:'sc',path,root_id:'reports'};
  const api=vi.spyOn(client,'api');
  const resources=[parseRenderedLink(link,'/mac','mac',{origin,chatKey:'receiving-chat'}),decodeResource(new URL(link).hash)];
  for(const resource of resources){
   expect(resource).toMatchObject({host_id:'sc',path,mode:'render',meta:{chat_key:'source-chat'}});
   api.mockClear().mockResolvedValueOnce({roots:[{id:'reports',host_id:'sc',path:folder}]}).mockResolvedValueOnce({resource:file});
   await expect(resolveFile(resource!)).resolves.toEqual(file);
   expect(api.mock.calls).toEqual([
    [client.query('/api/files/roots',{host_id:'sc',chat_key:'source-chat'})],
    [client.query('/api/files/stat',{root_id:'reports',path:'weekly-activity.html'})],
   ]);
  }
 });

 it('falls back to an exact reference from the encoded chat if no folder covers it',async()=>{
  const file={host_id:'sc',path,file_id:'exact'};
  const api=vi.spyOn(client,'api').mockResolvedValueOnce({roots:[]}).mockResolvedValueOnce({resource:file});
  const resource=parseRenderedLink(link,'/mac','mac',{origin,chatKey:'receiving-chat'});
  await expect(resolveFile(resource!)).resolves.toEqual(file);
  expect(api.mock.calls[1]).toEqual(['/api/files/reference',{chat_key:'source-chat',path}]);
  api.mockResolvedValueOnce({roots:[]}).mockResolvedValueOnce({resource:{...file,host_id:'mac'}});
  await expect(resolveFile(resource!)).rejects.toThrow('different host');
 });
});
