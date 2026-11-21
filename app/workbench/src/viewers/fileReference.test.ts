import {describe,it,expect,vi,afterEach} from 'vitest';
import * as client from '../api/client';
import {resolveFile} from './documents';
afterEach(()=>vi.restoreAllMocks());
describe('exact file reference refresh',()=>{
 const resource={kind:'file' as const,host_id:'cluster',path:'/work/report.md',file_id:'expired',meta:{chat_key:'thread'}};
 it('refreshes only the evidenced exact file after an expired capability',async()=>{const api=vi.spyOn(client,'api').mockRejectedValueOnce(new client.ApiError('expired',404)).mockResolvedValueOnce({resource:{file_id:'fresh',host_id:'cluster',path:'/work/report.md'}});expect((await resolveFile(resource)).file_id).toBe('fresh');expect(api.mock.calls[1]).toEqual(['/api/files/reference',{chat_key:'thread',path:'/work/report.md'}]);});
 it('does not refresh missing provenance or non-expiry failures',async()=>{const api=vi.spyOn(client,'api').mockRejectedValue(new client.ApiError('denied',403));await expect(resolveFile(resource)).rejects.toThrow('denied');expect(api).toHaveBeenCalledTimes(1);api.mockReset().mockRejectedValue(new client.ApiError('expired',404));await expect(resolveFile({...resource,meta:{}})).rejects.toThrow('expired');expect(api).toHaveBeenCalledTimes(1)});
 it('rejects a refreshed reference from a different host',async()=>{vi.spyOn(client,'api').mockRejectedValueOnce(new client.ApiError('expired',404)).mockResolvedValueOnce({resource:{file_id:'fresh',host_id:'mac'}});await expect(resolveFile(resource)).rejects.toThrow('different host')});
});
