import {AccountWarning} from '../chat/RecoveryCard';
import {resetCountdown,usageSnapshot} from './quotaReset';
import {useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {useWorkbench} from '../state/context';
import {accountLabel} from '../chat/helpers';
import {Plus,RefreshCw,Trash2,Copy} from 'lucide-react';
import {IconButton} from './Sidebar';
import './accounts.css';
import {Analytics} from './Analytics';
import {Insights} from './Insights';
function QuotaReset({value,now}:{value:unknown;now:number}){
 const reset=resetCountdown(value,now),date=reset.at?new Date(reset.at):undefined;
 return <span className="quota-reset" title={date?.toLocaleString()}>{reset.label}{date&&<time dateTime={date.toISOString()}> · {date.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</time>}</span>;
}
export function AccountsPanel({onGitHubAccounts}:{onGitHubAccounts?:(connect?:boolean)=>Promise<{accounts:{id:string,label:string}[]}>}={}){
 const githubRequest=useRef(0);
 const [github,setGithub]=useState<{id:string,label:string}[]|null>(null),[githubError,setGithubError]=useState(''),[githubBusy,setGithubBusy]=useState(false),[loaded,setLoaded]=useState(false);
 async function loadGithub(connect=false){if(!onGitHubAccounts)return;const request=++githubRequest.current;setGithubBusy(true);setGithubError('');try{const response=await onGitHubAccounts(connect);if(!Array.isArray(response?.accounts))throw Error('GitHub returned an invalid account list. Try refreshing.');if(request===githubRequest.current)setGithub(response.accounts);}catch(e:any){if(request===githubRequest.current)setGithubError(e.message||'Could not load GitHub accounts');}finally{if(request===githubRequest.current)setGithubBusy(false);}}
 const {refresh,notify,open}=useWorkbench();const [value,setValue]=useState<any>({accounts:[],pending_accounts:[]}),[busy,setBusy]=useState(''),[error,setError]=useState(''),[adding,setAdding]=useState(false),[tool,setTool]=useState('claude'),[email,setEmail]=useState(''),[login,setLogin]=useState<any>(null),[code,setCode]=useState(''),[showLink,setShowLink]=useState(false),[emailHint,setEmailHint]=useState('');
 function accountLogin(row:any){
  if(!row)return null;
  const id=row.id;
  if(row.login?.login_id)return {...row.login,id,login_started:true};
  if(row.renewal_error)return {id,message:row.renewal_error};
  if(row.renewal_pending)return {id,renewal_pending:true,message:'Sign-in will open when chats are safely idle.'};
  if(row.login_starting)return {id,login_pending:true,message:'Starting sign-in…'};
  if(row.login_error)return {id,message:row.login_error};
  return null;
 }
 async function load(){
  try{
   const response=await api('/api/accounts');
   if(!Array.isArray(response?.accounts))throw Error('The account service returned an invalid account list. Try refreshing.');
   setValue(response);
   const rows=[...response.accounts,...(response.pending_accounts||[])];
   setLogin(current=>{
    if(!current){
     const active=rows.find(a=>a.login?.login_id||a.renewal_pending||a.login_starting);
     return accountLogin(active);
    }
    if(!current.renewal_pending&&!current.login_pending)return current;
    const found=accountLogin(rows.find(a=>a.id===current.id));
    return found||{id:current.id,message:'Sign-in is no longer queued. Check it if you finished in your browser, or try again.'};
   });
   setError('');
  }catch(e){setError(e.message)}
  finally{setLoaded(true)}
 }
 useEffect(()=>{void load();void loadGithub();const refreshGithub=()=>void loadGithub();window.addEventListener('library-refresh',refreshGithub);return()=>window.removeEventListener('library-refresh',refreshGithub);},[]);
 const waitingId=login?.renewal_pending||login?.login_pending?login.id:'';
 useEffect(()=>{
  if(!waitingId)return;
  let stopped=false,timer:ReturnType<typeof setTimeout>;
  async function poll(){
   if(!document.hidden)await load();
   if(!stopped)timer=setTimeout(poll,3000);
  }
  timer=setTimeout(poll,3000);
  return()=>{stopped=true;clearTimeout(timer);};
 },[waitingId]);
 async function beginLogin(id:string,whenIdle=false,hint?:string){
  try{
   const result=await api('/api/accounts/login',{id,open_browser:true,...(whenIdle?{when_idle:true}:{}),...(hint?{email_hint:hint}:{})});
   setLogin({...result,id,authorization_url:result.authorization_url||result.url});
  }catch(e:any){
   if(e.code==='login_email_required'){
    setEmailHint('');
    setLogin({id,emailRequired:true,message:e.message});
   }else if(e.code==='login_busy'||e.message?.includes('Renew when idle')){
    setLogin({id,blocked:true,message:e.message});
   }else throw e;
  }
  setCode('');
  setShowLink(false);
 }
 async function copySignInLink(url:string){
  try{
   const rpc=(window as any).libraryRpc;
   if(rpc)await rpc('copy',{text:url});
   else await navigator.clipboard.writeText(url);
   notify('Sign-in link copied');
  }catch{setShowLink(true);}
 }
 async function submitCode(e:any){e.preventDefault();if(!login||!code.trim())return;setBusy(login.id);setError('');try{
  await api('/api/accounts/login/code',{id:login.id,login_id:login.login_id,code:code.trim()});setCode('');
  const result=await api('/api/accounts/verify',{id:login.id});if(result.accounts)setValue(result);else await load();
  const verified=result.accounts?.find((a:any)=>a.id===login.id);if(verified?.logged_in===true){setLogin(null);notify('Connected '+verified.email);}else setError(result.pending_accounts?.find(a=>a.id===login.id)?.error||'Sign-in is not verified yet. Finish the provider login, then check again.');await refresh();
 }catch(e:any){setError(e.message||'Could not finish sign-in.');}finally{setBusy('')}}
 async function action(id:string,kind:string,hint?:string){setBusy(id||kind);setError('');try{if(kind==='login'||kind==='login-idle'){await beginLogin(id,kind==='login-idle',hint);}else{const result=await api('/api/accounts/'+kind,id?{id}:{});if(result.accounts)setValue(result);else await load();if(kind==='verify'){const verified=result.accounts?.find(a=>a.id===id);if(verified?.logged_in===true){setLogin(null);notify('Connected '+verified.email);}else setError(result.pending_accounts?.find(a=>a.id===id)?.error||'Sign-in is not verified yet. Finish the provider login, then check again.');}await refresh();}}catch(e){setError(e.message)}finally{setBusy('')}}
 async function add(e:any){e.preventDefault();setBusy('add');setError('');try{const created=await api('/api/accounts/add',{tool,label:email.trim()});await load();setAdding(false);setEmail('');await beginLogin(created.id);}catch(e){setError(e.message)}finally{setBusy('')}}
 async function remove(a:any){if(!confirm(`Remove ${a.email||a.label||a.id} from Workspace? Saved conversations and provider credentials will remain on disk.`))return;await action(a.id,'remove');if(login?.id===a.id)setLogin(null)}
 const [chartView,setChartView]=useState(false);
 const [now,setNow]=useState(Date.now());
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),60000);return()=>clearInterval(timer)},[]);
 const pending=value.pending_accounts||[];
 const connected=value.accounts.filter(a=>!a.duplicate_of&&((a.logged_in&&a.email)||a.warnings?.length));
 const codeRequired=login?.code_required??login?.needs_code??false;
 const loginProvider=login?.id.startsWith('codex-')?'Codex':'Claude';
 return <section className="accounts-panel"><div className="settings-section-heading"><h2>Connected accounts</h2><IconButton label="Refresh account usage" disabled={!!busy} onClick={()=>action('','refresh')}><RefreshCw size={16}/></IconButton></div><p>Manage provider sign-in and usage. Defaults apply to new chats within the selected provider and model.</p>{error&&<p role="alert" className="inline-error">{error}</p>}<div className="quota-view-toggle" role="group" aria-label="Quota chart style"><button aria-pressed={!chartView} onClick={()=>setChartView(false)}>Bars</button><button aria-pressed={chartView} onClick={()=>setChartView(true)}>Donut</button></div>
 {!loaded&&!error&&<p role="status">Loading accounts…</p>}{loaded&&(['claude','codex'] as const).map(provider=><section className="account-provider" key={provider}><h3>{provider==='claude'?'Claude':'Codex'}</h3>{connected.filter(a=>a.tool===provider).map(a=><div className="account-row" key={a.id}><div className="account-heading"><div className="account-identity"><strong>{accountLabel(a,connected)}</strong><span>{[a.org?.toLowerCase()===a.email?.toLowerCase()+"'s organization"?'':a.org,usageSnapshot(a,now).label].filter(Boolean).join(' · ')}</span></div><div className="account-actions">{!a.is_default&&!a.global&&<IconButton label={'Remove '+a.email} disabled={!!busy} onClick={()=>remove(a)}><Trash2 size={15}/></IconButton>}<button disabled={!!busy} onClick={()=>action(a.id,'login')}>Sign in again</button></div></div><AccountWarning account={a} accounts={connected} onAccounts={()=>{void load();void refresh()}}/>{(a.windows||[]).length?<div className="account-quotas">{(a.windows||[]).map((w,i)=><div className="quota" key={w.kind+i}><span>{w.label||w.kind}</span>{w.percent==null?<span/>:chartView?<span className="quota-donut" role="img" aria-label={Math.round(w.percent)+'% used'} style={{background:`conic-gradient(var(--bar) ${Math.max(0,Math.min(100,w.percent))}%, var(--line) 0)`}}><span/></span>:<span className="quota-bar" role="img" aria-label={Math.round(w.percent)+'% used'}><i style={{width:Math.max(0,Math.min(100,w.percent))+'%'}}/></span>}<span className="quota-percent">{w.percent==null?'Unknown':Math.round(w.percent)+'%'}</span><QuotaReset value={w.resets_at} now={now}/></div>)}</div>:<small className="quota-reset">Reset schedule unavailable</small>}{a.credits?.balance!=null&&<small className="quota-reset">Workspace credits: {a.credits.balance}</small>}{a.error&&<small className="inline-error">{a.error}</small>}</div>)}{!connected.some(a=>a.tool===provider)&&<p className="empty-note">No connected {provider==='claude'?'Claude':'Codex'} accounts yet.</p>}</section>)}
 {onGitHubAccounts&&<section className="account-provider"><h3>GitHub</h3>{githubError?<p role="alert" className="inline-error">{githubError}</p>:github===null?<p>Loading GitHub accounts…</p>:github.length?github.map(a=><div className="account-row" key={a.id}><strong>{a.label}</strong></div>):<p className="empty-note">No connected GitHub accounts.</p>}{github!==null&&!github.length&&<button disabled={githubBusy} onClick={()=>void loadGithub(true)}>Connect GitHub</button>}</section>}
 <button className="text-button" onClick={()=>setAdding(v=>!v)}><Plus size={15}/>Connect Claude or Codex</button>{adding&&<form className="add-account" onSubmit={add}><select aria-label="New account provider" value={tool} onChange={e=>setTool(e.target.value)}><option value="claude">Claude</option><option value="codex">Codex</option></select><input type="email" aria-label="Account email" placeholder="you@example.com" value={email} onChange={e=>setEmail(e.target.value)} required/><button disabled={!!busy}>Continue in browser</button></form>}
 {login&&<div className="account-login" role="status">
   <strong>Finish {loginProvider} sign-in</strong><p>{login.message||'Complete provider sign-in, then check the result here.'}</p>
   {login.authorization_url&&<div>
    <button type="button" onClick={()=>{if((window as any).libraryRpc)open({kind:'url',url:login.authorization_url},'default-browser');else window.open(login.authorization_url,'_blank','noopener');}}>Open sign-in</button>
    <button type="button" onClick={()=>void copySignInLink(login.authorization_url)}>Copy sign-in link</button>
    {showLink&&<input aria-label="Sign-in link" readOnly value={login.authorization_url} onFocus={e=>e.currentTarget.select()}/>}
   </div>}
   {codeRequired&&<form onSubmit={submitCode}><label>Authorization code <input aria-label="Authorization code" value={code} onChange={e=>setCode(e.target.value)} autoComplete="off"/></label><button disabled={!!busy||!code.trim()}>Complete sign-in</button></form>}
   {login.emailRequired&&<form onSubmit={e=>{e.preventDefault();void action(login.id,'login',emailHint.trim());}}>
    <label>Account email <input type="email" value={emailHint} onChange={e=>setEmailHint(e.target.value)} required/></label>
    <button disabled={!!busy||!emailHint.trim()}>Continue in browser</button>
   </form>}
   {login.blocked&&<button disabled={!!busy} onClick={()=>action(login.id,'login-idle')}>Renew when idle</button>}
   {!login.blocked&&!login.emailRequired&&!waitingId&&<div>
    <button disabled={!!busy} onClick={()=>action(login.id,'verify')}>{busy===login.id?'Checking…':'Check sign-in'}</button>
    {!login.login_started&&<button disabled={!!busy} onClick={()=>action(login.id,'login')}>Try sign-in again</button>}
   </div>}
   {login.login_cmd&&<details><summary>Manual setup command</summary><code>{login.login_cmd}</code><IconButton label="Copy sign-in command" onClick={()=>navigator.clipboard.writeText(login.login_cmd).then(()=>notify('Sign-in command copied')).catch(()=>notify('Clipboard is unavailable'))}><Copy size={14}/></IconButton></details>}
 </div>}
 {pending.length>0&&<details className="account-pending" open={!!login}><summary>Incomplete sign-ins ({pending.length})</summary><p>These setups are not connected accounts. Complete sign-in or remove the setup.</p>{pending.map(a=><div className="pending-account" key={a.id}><span>{a.tool==='claude'?'Claude':'Codex'} · {a.label||a.id}<small>{a.renewal_error||(a.renewal_pending?'Waiting for chats to be idle':a.error)||'Sign-in not verified'}</small></span><button disabled={!!busy} onClick={()=>action(a.id,'login')}>{a.login?'Continue sign-in':'Sign in'}</button><button disabled={!!busy} onClick={()=>action(a.id,'verify')}>Check</button>{!a.is_default&&!a.global&&<IconButton label={'Remove incomplete '+(a.label||a.id)} disabled={!!busy} onClick={()=>remove(a)}><Trash2 size={15}/></IconButton>}</div>)}</details>}
 </section>;
}

// Accounts page: the account list and the usage analytics that used to live in Settings.
export function AccountsPage(props:Parameters<typeof AccountsPanel>[0]={}){
 const [tab,setTab]=useState('Accounts');
 return <div className="settings-page"><header><h1>Accounts</h1><div className="settings-tabs" role="tablist" aria-label="Accounts sections">{['Accounts','Analytics'].map(t=><button key={t} role="tab" aria-selected={t===tab} onClick={()=>setTab(t)}>{t}</button>)}</div></header>
  {tab==='Accounts'?<AccountsPanel {...props}/>:<><Analytics/><Insights mode="analytics"/></>}</div>;
}
