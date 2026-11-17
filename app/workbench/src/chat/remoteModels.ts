import {useEffect,useState} from 'react';
import {api} from '../api/client';

export type ModelContext={host_id:string;provider:string;account_id:string;cwd:string;identity?:string};
export type RemoteModel={id:string;name:string;efforts:string[];default_effort?:string};
export function remoteModelContext(state:{host_id:string;provider:string;account:string;cwd:string},binding:string,useBinding:boolean,host:any):ModelContext{
 // Automatic remote routing resolves only at launch. Never reuse selectedAccount
 // from another host or imply that its catalog belongs to the eventual account.
 const account_id=(useBinding&&binding)||state.account||'';
 const account=host?.accounts?.find((a:any)=>a.id===account_id&&(a.tool||a.provider)===state.provider);
 const publicBinding=account?Object.fromEntries(['id','tool','disabled','eligible','logged_in','email','plan','models'].map(key=>[key,account[key]])):null;
 return {host_id:state.host_id,provider:state.provider,account_id,cwd:state.cwd,identity:JSON.stringify([host?.machine_id,host?.job_id,host?.state,host?.can_execute,host?.execution_alias,host?.connector_path,publicBinding,account?.usage?.logged_in])};
}
export function validateRemoteModels(value:any,context:ModelContext){
 for(const key of ['host_id','provider','account_id','cwd'] as const){
  if(value?.[key]!==context[key])throw Error('Model catalog belongs to a different host, account or folder. Reopen model settings to retry.');
 }
 if(!Array.isArray(value.models)||!value.models.length||value.models.length>500)throw Error('The selected host did not advertise a model catalog.');
 const ids=new Set<string>();
 const models:RemoteModel[]=value.models.map((m:any)=>{
  if(typeof m?.id!=='string'||m.id.length>160||typeof m.name!=='string'||m.name.length>240||!Array.isArray(m.efforts)||m.efforts.some((e:any)=>typeof e!=='string'||e.length>40)||ids.has(m.id))throw Error('The selected host returned an invalid model catalog.');
  ids.add(m.id);
  return {id:context.provider==='claude'&&m.id==='default'?'':m.id,name:m.name,efforts:[...m.efforts],...(typeof m.default_effort==='string'&&m.efforts.includes(m.default_effort)?{default_effort:m.default_effort}:{})};
 });
 return {models,commands:Array.isArray(value.commands)?value.commands:[],source:value.source||''};
}
export async function loadRemoteModels(context:ModelContext,request=api,signal?:AbortSignal){
 if(!context.account_id||!context.cwd)throw Error('Choose an account and folder to load models.');
 const {host_id,provider,account_id,cwd}=context;
 try{return validateRemoteModels(await request('/api/models/discover',{host_id,provider,account_id,cwd},signal),context)}
 catch(error:any){if(error.status===404)throw Error('Remote model discovery needs the shared service update. Your saved model is retained.');throw error}
}
export function startRemoteModels(context:ModelContext,receive:(value:ReturnType<typeof validateRemoteModels>)=>void,fail:(error:Error)=>void,request=api){
 const abort=new AbortController();let current=true;
 const timer=setTimeout(()=>{void loadRemoteModels(context,request,abort.signal).then(value=>{if(current)receive(value)}).catch(error=>{if(current)fail(error)})},300);
 return()=>{current=false;clearTimeout(timer);abort.abort()};
}
export function useRemoteModels(context:ModelContext,enabled:boolean){
 const key=JSON.stringify(context);
 const [result,setResult]=useState<{key:string;models:RemoteModel[];commands:any[];notice:string}>({key:'',models:[],commands:[],notice:''});
 useEffect(()=>{
  if(!enabled)return;
  setResult({key,models:[],commands:[],notice:'Loading models…'});
  return startRemoteModels(context,value=>setResult({key,models:value.models,commands:value.commands,notice:''}),error=>setResult({key,models:[],commands:[],notice:error.message||'Remote model catalog unavailable. Your saved model is retained.'}));
 },[key,enabled]);
 return result.key===key?result:{models:[],commands:[],notice:''};
}
