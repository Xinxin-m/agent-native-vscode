import {useState} from 'react';
import {api} from '../api/client';
import {useWorkbench} from '../state/context';
import {describeModelRefresh,knownModels,type ModelRefresh} from './modelRefresh';

// Check asks the server what the CLIs now offer (no install, no model call); Install runs the
// CLI self-updates. Either way the model list is fetched again, so menus update without a reload.
export function ModelUpdates({compact=false}:{compact?:boolean}){
 const wb=useWorkbench();
 const [phase,setPhase]=useState<'idle'|'checking'|'installing'|'done'|'error'>('idle');
 const [result,setResult]=useState<ModelRefresh>(),[error,setError]=useState('');
 async function run(install:boolean){
  setPhase(install?'installing':'checking');setError('');
  try{
   const value=await api<ModelRefresh>('/api/models/refresh',{install,known:knownModels(wb.data?.compose_options)});
   await wb.refresh();
   setResult(value);setPhase('done');
  }catch(e:any){setResult(undefined);setError(e?.message||'Check failed');setPhase('error')}
 }
 const busy=phase==='checking'||phase==='installing',text=result&&describeModelRefresh(result);
 const check=<button type="button" className={compact?'chat-link-button':''} disabled={busy} onClick={()=>void run(false)}>{phase==='checking'?'Checking…':'Check for model updates'}</button>;
 const install=text?.canInstall&&<button type="button" className={compact?'chat-link-button':''} disabled={busy} onClick={()=>void run(true)}>{phase==='installing'?'Installing…':'Install updates'}</button>;
 if(compact)return <div className="model-updates compact">{check}{install&&<> · {install}</>}
  <span role="status">{phase==='error'?'Check failed: '+error:phase==='installing'?'Updating the CLIs…':text?[text.models.replace(/\.$/,''),...text.changes].join('. ')+'.':''}</span></div>;
 return <div className="model-updates">
  <div className="model-updates-actions">{check}{install}</div>
  <div role="status" aria-live="polite">
   {phase==='installing'&&<p>Updating the CLIs. This can take a few minutes.</p>}
   {phase==='error'&&<p role="alert">Check failed: {error}</p>}
   {text&&phase!=='installing'&&<><p>{text.models}</p>{text.versions.map(v=><p key={v}>{v}</p>)}{text.notes.map(n=><p key={n} className="model-updates-note">{n}</p>)}<p className="model-updates-note">Checked {new Date(result!.checked_at).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}</p></>}
  </div>
 </div>;
}
