import {describe,it,expect,vi,afterEach} from 'vitest';
import {api,available,noteServerState,ApiError} from './client';
// Item 46: the native app talks to the old running server until it restarts. Routes added since the redesign are not
// requested there, and an HTML 404 (bridge code not_json) backs the route off instead of raising an error banner.
afterEach(()=>{delete (globalThis as any).window;});
describe('optional routes on an older server',()=>{
 it('an older /api/state (rows lack failed) hides the new routes without a bridge call',async()=>{
  const rpc=vi.fn();(globalThis as any).window={libraryRpc:rpc};
  noteServerState({chats:[{key:'k',title:'t'}]});
  expect(available('/api/notifications')).toBe(false);
  expect(available('/api/state')).toBe(true);
  await expect(api('/api/seen',{keys:['k']})).rejects.toBeInstanceOf(ApiError);
  expect(rpc).not.toHaveBeenCalled();
  noteServerState({chats:[{key:'k',title:'t',failed:false}]});
  expect(available('/api/notifications')).toBe(true);
 });
 it('an HTML 404 through the bridge backs the route off: one probe, then no call until the wait ends',async()=>{
  const rpc=vi.fn(async()=>{const e:any=new Error('HTTP 404');e.status=404;e.code='not_json';throw e});(globalThis as any).window={libraryRpc:rpc};
  noteServerState({chats:[{key:'k',failed:false}]});
  await expect(api('/api/placements?key=k')).rejects.toMatchObject({status:404,code:'not_available'});
  await expect(api('/api/placements?key=k')).rejects.toMatchObject({code:'not_available'});
  expect(rpc).toHaveBeenCalledTimes(1);
  expect(available('/api/placements')).toBe(false);
  expect(available('/api/notices')).toBe(true);
 });
 it('a JSON 404 from a new server (unknown item) is passed through, not treated as a missing route',async()=>{
  const rpc=vi.fn(async()=>{const e:any=new Error('not found');e.status=404;e.code='not_found';throw e});(globalThis as any).window={libraryRpc:rpc};
  await expect(api('/api/notifications/act',{id:'x'})).rejects.toMatchObject({code:'not_found'});
  expect(available('/api/notifications/act')).toBe(true);
 });
});
