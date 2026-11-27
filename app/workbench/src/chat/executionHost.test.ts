import {describe,expect,it,vi} from 'vitest';
import {probeExecutionHost,watchExecutionHost} from './executionHost';

const online={id:'sc',state:'online',can_execute:true,observed_at:42,accounts:[{id:'codex-sc',tool:'codex'}]};
function pendingProbe(){
 let finish:(value:{host:typeof online})=>void=()=>{};
 const response=new Promise<{host:typeof online}>(resolve=>{finish=resolve});
 return {finish,request:vi.fn(()=>response)};
}

describe('execution host checks',()=>{
 it('shares the chooser’s in-flight probe with Send and runs a fresh check after it finishes',async()=>{
  const {finish,request}=pendingProbe(),publish=vi.fn();
  const watching=watchExecutionHost('sc',publish,request);
  const send=probeExecutionHost('sc',request);
  expect(request).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledWith('/api/hosts/probe',{id:'sc'},expect.any(AbortSignal));
  finish({host:online});
  await expect(send).resolves.toBe(online);
  expect(publish).toHaveBeenCalledWith({hostId:'sc',phase:'ready',host:online});
  await probeExecutionHost('sc',request);
  expect(request).toHaveBeenCalledTimes(2);
  watching();
 });

 it('updates a displayed connection if the next launch check fails',async()=>{
  const error='The compute allocation expired',publish=vi.fn();
  const request=vi.fn().mockResolvedValueOnce({host:online}).mockRejectedValueOnce(Error(error));
  const unwatch=watchExecutionHost('sc',publish,request);
  await probeExecutionHost('sc',request);
  await expect(probeExecutionHost('sc',request)).rejects.toThrow(error);
  expect(publish).toHaveBeenLastCalledWith({hostId:'sc',phase:'unavailable',error});
  unwatch();
 });

 it('ignores an in-flight result when the user changes locations or closes the view',async()=>{
  const {finish,request}=pendingProbe(),publish=vi.fn();
  const unwatch=watchExecutionHost('sc',publish,request);
  const inFlight=probeExecutionHost('sc',request);
  unwatch();finish({host:online});
  await inFlight;
  expect(publish).not.toHaveBeenCalled();
 });

 it('never treats a response for a different host as verified',async()=>{
  await expect(probeExecutionHost('sc',async()=>({host:{...online,id:'another-host'}}))).rejects.toThrow(/different execution location/);
 });

 it('shows connector failures as unavailable and preserves the specific reason',async()=>{
  const {finish,request}=pendingProbe(),publish=vi.fn(),error='SSH connector is unavailable';
  const unwatch=watchExecutionHost('sc',publish,request);
  const inFlight=probeExecutionHost('sc',request);
  finish({host:{...online,state:'error',can_execute:false,error} as typeof online});
  await inFlight;
  expect(publish).toHaveBeenCalledWith({hostId:'sc',phase:'unavailable',error});
  unwatch();
 });
});
