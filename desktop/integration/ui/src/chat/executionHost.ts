import {useEffect,useState} from 'react';
import {api} from '../api/client';

type ExecutionHost={id:string;state:string;can_execute:boolean;observed_at:number;error?:string;accounts?:any[];default_cwd?:string};
type ProbeRequest=(path:string,body:{id:string},signal:AbortSignal)=>Promise<{host?:ExecutionHost}>;
type HostCheck={hostId:string;phase:'checking'}|{hostId:string;phase:'ready';host:ExecutionHost}|{hostId:string;phase:'unavailable';error:string};
const inFlight=new Map<string,Promise<ExecutionHost>>();
const observers=new Map<string,Set<(result:HostCheck)=>void>>();

function publish(result:HostCheck){observers.get(result.hostId)?.forEach(observer=>observer(result))}

export function probeExecutionHost(hostId:string,request:ProbeRequest=api):Promise<ExecutionHost>{
 const ongoing=inFlight.get(hostId);
 if(ongoing)return ongoing;
 const probe=request('/api/hosts/probe',{id:hostId},AbortSignal.timeout(65_000)).then(({host})=>{
  if(host?.id!==hostId)throw Error('The probe returned a different execution location.');
  if(!host.observed_at||typeof host.can_execute!=='boolean'||(host.can_execute&&host.state!=='online'))throw Error('The execution location did not return a valid status.');
  return host;
 }).then(host=>{
  if(host.can_execute)publish({hostId,phase:'ready',host});
  else publish({hostId,phase:'unavailable',error:host.error||'The connector does not support running agents.'});
  return host;
 },error=>{
  publish({hostId,phase:'unavailable',error:error?.message||'The connection check did not complete.'});
  throw error;
 }).finally(()=>inFlight.delete(hostId));
 inFlight.set(hostId,probe);
 return probe;
}

export function watchExecutionHost(hostId:string,observer:(result:HostCheck)=>void,request:ProbeRequest=api){
 const listeners=observers.get(hostId)||new Set<(result:HostCheck)=>void>();
 listeners.add(observer);
 observers.set(hostId,listeners);
 void probeExecutionHost(hostId,request).catch(()=>{});
 // Leave the shared request running for Send or another tab that selected the same host.
 return()=>{listeners.delete(observer);if(!listeners.size)observers.delete(hostId)};
}

export function useExecutionHostProbe(hostId:string,enabled:boolean):HostCheck|null{
 const [result,setResult]=useState<HostCheck|null>(null);
 useEffect(()=>{
  if(!enabled)return;
  setResult({hostId,phase:'checking'});
  return watchExecutionHost(hostId,setResult);
 },[hostId,enabled]);
 return result?.hostId===hostId?result:enabled?{hostId,phase:'checking'}:null;
}
