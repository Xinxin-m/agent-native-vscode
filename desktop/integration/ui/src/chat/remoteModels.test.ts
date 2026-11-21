import {describe,it,expect,vi} from 'vitest';
import {validateRemoteModels,loadRemoteModels,startRemoteModels,remoteModelContext} from './remoteModels';
const context={host_id:'compute',provider:'claude',account_id:'work',cwd:'/workspace'};
const value={...context,source:'provider-initialize',models:[{id:'opus',name:'Opus',efforts:['low','high'],default_effort:'high'}]};
describe('account-bound remote model catalogs',()=>{
 it('retains advertised model and effort metadata',()=>expect(validateRemoteModels(value,context).models).toEqual(value.models));
 it.each(['host_id','provider','account_id','cwd'])('rejects mismatched %s',key=>expect(()=>validateRemoteModels({...value,[key]:'different'},context)).toThrow(/different host/));
 it('rejects empty and duplicate catalogs',()=>{
  expect(()=>validateRemoteModels({...value,models:[]},context)).toThrow(/did not advertise/);
  expect(()=>validateRemoteModels({...value,models:[...value.models,...value.models]},context)).toThrow(/invalid/);
 });
 it('makes only a metadata request, without starting a run',async()=>{
  const calls:any[]=[];await loadRemoteModels(context,async(path,body)=>{calls.push({path,body});return value as any});
  expect(calls).toEqual([{path:'/api/models/discover',body:context}]);
 });
 it('does not request discovery without an explicit account',async()=>{
  let called=false;await expect(loadRemoteModels({...context,account_id:''},async()=>{called=true;return value as any})).rejects.toThrow(/Choose an account/);expect(called).toBe(false);
 });
 it('reports an older service without falling back to local models',async()=>{
  await expect(loadRemoteModels(context,async()=>{throw Object.assign(Error('HTTP 404'),{status:404})})).rejects.toThrow(/shared service update/);
 });
});

describe('discovery request lifetime',()=>{
 it('debounces folder edits and cancels closed settings before sending',async()=>{
  vi.useFakeTimers();
  try{
   const request=vi.fn(async()=>value as any),receive=vi.fn(),fail=vi.fn();
   const cancel=startRemoteModels(context,receive,fail,request);cancel();
   await vi.advanceTimersByTimeAsync(400);expect(request).not.toHaveBeenCalled();
   const close=startRemoteModels(context,receive,fail,request);
   await vi.advanceTimersByTimeAsync(299);expect(request).not.toHaveBeenCalled();
   await vi.advanceTimersByTimeAsync(1);expect(request).toHaveBeenCalledTimes(1);expect(receive).toHaveBeenCalledTimes(1);close();
  }finally{vi.useRealTimers()}
 });
 it('ignores an old in-flight account response after cancellation',async()=>{
  vi.useFakeTimers();
  try{
   let complete:(value:any)=>void=()=>{};let signal:AbortSignal|undefined;
   const receive=vi.fn(),fail=vi.fn();
   const close=startRemoteModels(context,receive,fail,async(_path,_body,s)=>{signal=s;return new Promise(resolve=>complete=resolve) as any});
   await vi.advanceTimersByTimeAsync(300);close();expect(signal?.aborted).toBe(true);
   complete(value);await vi.advanceTimersByTimeAsync(0);expect(receive).not.toHaveBeenCalled();expect(fail).not.toHaveBeenCalled();
  }finally{vi.useRealTimers()}
 });
});

it('uses one native default choice for Claude metadata',()=>{expect(validateRemoteModels({...value,models:[{id:'default',name:'Default',efforts:['high']}]},context).models[0].id).toBe('')});

describe('remote account and binding context',()=>{
 const state={host_id:'compute',provider:'claude',account:'',cwd:'/work',selectedAccount:'stale-local'};
 it('requires explicit account for new or handed-off chats instead of reusing Auto state',()=>{
  expect(remoteModelContext(state,'old-bound',false,{}).account_id).toBe('');
  expect(remoteModelContext({...state,account:'explicit'},'old-bound',false,{}).account_id).toBe('explicit');
  expect(remoteModelContext(state,'native-bound',true,{}).account_id).toBe('native-bound');
 });
 it('invalidates a displayed catalog for host route and account binding changes',()=>{
  const account={id:'explicit',tool:'claude',email:'first',usage:{logged_in:true}};
  const host={execution_alias:'compute-a',connector_path:'/connector',accounts:[account]};
  const selected={...state,account:'explicit'},before=remoteModelContext(selected,'',false,host).identity;
  for(const changed of [{...host,execution_alias:'compute-b'},{...host,connector_path:'/new-connector'},{...host,accounts:[{...account,email:'second'}]},{...host,accounts:[{...account,usage:{logged_in:false}}]}]){
   expect(remoteModelContext(selected,'',false,changed).identity).not.toBe(before);
  }
 });
});
