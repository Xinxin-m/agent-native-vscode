import {AccountWarning} from '../chat/RecoveryCard';
import {resetCountdown,usageSnapshot} from './quotaReset';
import {useEffect,useRef,useState} from 'react';
import {api,available} from '../api/client';
import {useWorkbench} from '../state/context';
import {accountRows,displayName,secondaryEmail,barHot,cleanPercent,needsAccountSignIn} from './accountRows';
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
 const {refresh,notify,open}=useWorkbench();const [value,setValue]=useState<any>({accounts:[],pending_accounts:[]}),[busy,setBusy]=useState(''),[error,setError]=useState(''),[adding,setAdding]=useState(false),[tool,setTool]=useState('claude'),[name,setName]=useState(''),[addError,setAddError]=useState(''),[login,setLogin]=useState<any>(null),[code,setCode]=useState(''),[showLink,setShowLink]=useState(false),[emailHint,setEmailHint]=useState('');
 const [editing,setEditing]=useState<{id:string,value:string,error?:string}|null>(null),[note,setNote]=useState<{id:string,text:string,force?:boolean}|null>(null),[noRename,setNoRename]=useState(()=>!available('/api/accounts/rename')),[noLogout,setNoLogout]=useState(()=>!available('/api/accounts/logout'));
 const loginRef=useRef<any>(null),skipBlur=useRef(false);loginRef.current=login;
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
   const rows=[...response.accounts,...(response.pending_accounts||[])],cur=loginRef.current,row=cur&&rows.find(a=>a.id===cur.id);
   if(cur?.login_id&&row&&!row.login&&row.login_error&&!cur.failed)setLogin({...cur,failed:true,message:row.login_error});
   else if(cur?.login_id&&!cur.failed&&row?.logged_in&&row.email&&!row.login&&!needsAccountSignIn(row)){setLogin(null);notify('Connected '+row.email);void refresh();}
   else if(cur?.login_id&&!cur.failed&&row&&!row.login&&needsAccountSignIn(row))setLogin({...cur,failed:true,message:'Library still cannot access this account’s credentials. Try signing in again.'});
   else setLogin(current=>{
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
 useEffect(()=>{
  const reloadAccounts=()=>void load();
  const reload=()=>{reloadAccounts();void loadGithub();};
  reload();
  window.addEventListener('library-refresh',reload);
  window.addEventListener('focus',reloadAccounts);
  return()=>{
   window.removeEventListener('library-refresh',reload);
   window.removeEventListener('focus',reloadAccounts);
  };
 },[]);
 const codeRequired=login?.code_required??login?.needs_code??false;
 const waitingId=login&&!login.failed&&(login.renewal_pending||login.login_pending||(login.login_id&&!codeRequired))?login.id:'';
 useEffect(()=>{
  if(!waitingId)return;
  let stopped=false,timer:ReturnType<typeof setTimeout>;
  async function poll(){
   if(!document.hidden)await load();
   if(!stopped)timer=setTimeout(poll,2000);
  }
  timer=setTimeout(poll,2000);
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
 // Cancel stops the sign-in the server started (an older server without the route: the box just closes).
 async function cancelLogin(){const id=login?.id;if(!id)return;setBusy(id);try{await api('/api/accounts/login/cancel',{id});}catch(e:any){if(e.status!==404)notify(e.message);}finally{setBusy('');}setLogin(null);await load();}
 async function copySignInLink(url:string){
  try{
   const rpc=(window as any).libraryRpc;
   if(rpc)await rpc('copy',{text:url});
   else await navigator.clipboard.writeText(url);
   notify('Sign-in link copied');
  }catch{setShowLink(true);}
 }
 async function submitCode(e:any){e.preventDefault();if(!login||!code.trim())return;setBusy(login.id);setNote(null);try{
  await api('/api/accounts/login/code',{id:login.id,login_id:login.login_id,code:code.trim()});setCode('');
  const result=await api('/api/accounts/verify',{id:login.id});if(result.accounts)setValue(result);else await load();
  const verified=result.accounts?.find((a:any)=>a.id===login.id);if(verified?.logged_in===true&&!needsAccountSignIn(verified)){setLogin(null);notify('Connected '+verified.email);}else setNote({id:login.id,text:result.pending_accounts?.find(a=>a.id===login.id)?.error||'Sign-in is not verified yet. Finish the provider login, then check again.'});await refresh();
 }catch(e:any){setNote({id:login.id,text:e.message||'Could not finish sign-in.'});}finally{setBusy('')}}
 async function action(id:string,kind:string,hint?:string){setBusy(id||kind);setError('');setNote(null);try{if(kind==='login'||kind==='login-idle'){await beginLogin(id,kind==='login-idle',hint);}else{const result=await api('/api/accounts/'+kind,id?{id}:{});if(result.accounts)setValue(result);else await load();if(kind==='verify'){const verified=result.accounts?.find(a=>a.id===id);if(verified?.logged_in===true&&!needsAccountSignIn(verified)){setLogin(null);notify('Connected '+verified.email);}else setNote({id,text:result.pending_accounts?.find(a=>a.id===id)?.error||'Sign-in is not verified yet. Finish the provider login, then check again.'});}await refresh();}}catch(e:any){if(id&&kind!=='refresh')setNote({id,text:e.message});else setError(e.message)}finally{setBusy('')}}
 async function signOut(a:any,force=false){setBusy(a.id);setNote(null);try{const result=await api('/api/accounts/logout',{id:a.id,...(force?{force:true}:{})});if(result.accounts)setValue(result);else await load();await refresh();}catch(e:any){if(e.status===404)setNoLogout(true);else if(e.code==='logout_busy')setNote({id:a.id,force:true,text:'Chats may be running on this account.'});else setNote({id:a.id,text:e.message});}finally{setBusy('')}}
 async function saveName(a:any,text:string,fromBlur:boolean){
  const next=text.trim();
  if(next===displayName(a)){setEditing(null);return;}
  if(!next){if(fromBlur){setEditing(null);setNote({id:a.id,text:'Enter a name.'});}else setEditing({id:a.id,value:text,error:'Enter a name.'});return;}
  skipBlur.current=true;
  try{const result=await api('/api/accounts/rename',{id:a.id,label:next});if(result.accounts)setValue(result);else await load();setEditing(null);await refresh();}
  catch(e:any){if(e.status===404){setNoRename(true);setEditing(null);}else{skipBlur.current=false;setEditing({id:a.id,value:text,error:e.message||'Could not save the name.'});}}
 }
 function startEdit(a:any){if(noRename)return;skipBlur.current=false;setNote(null);setEditing({id:a.id,value:displayName(a)});}
 async function add(e:any){e.preventDefault();setBusy('add');setAddError('');try{const created=await api('/api/accounts/add',{tool,...(name.trim()?{label:name.trim()}:{})});await load();setAdding(false);setName('');await beginLogin(created.id);}catch(e:any){setAddError(e.message)}finally{setBusy('')}}
 async function remove(a:any){if(!confirm(`Remove ${a.email||a.label||a.id} from Workspace? Saved conversations and provider credentials will remain on disk.`))return;await action(a.id,'remove');if(login?.id===a.id)setLogin(null)}
 const [now,setNow]=useState(Date.now());
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),60000);return()=>clearInterval(timer)},[]);
 const rows=accountRows(value),pending=rows.incomplete,connected=value.accounts.filter(a=>!a.duplicate_of);
 const loginProvider=login?.id.startsWith('codex-')?'Codex':'Claude';
 const loginBox=(id:string)=>login?.id===id&&<div className="account-login" role="status">
   {login.login_id&&!login.failed?<p className="account-waiting"><i aria-hidden="true"/>Waiting for sign-in in your browser{login.browser_opened===false?'. It did not open here, so use the link.':''}</p>:<strong>{login.failed?'Sign-in did not finish':'Finish '+loginProvider+' sign-in'}</strong>}
   {(login.message||!login.login_id)&&<p className={login.failed?'inline-error':undefined}>{login.message||'Complete provider sign-in, then check the result here.'}</p>}
   <div>
    {login.authorization_url&&<><button type="button" onClick={()=>{if((window as any).libraryRpc)open({kind:'url',url:login.authorization_url},'default-browser');else window.open(login.authorization_url,'_blank','noopener');}}>Open sign-in link</button>
    <button type="button" onClick={()=>void copySignInLink(login.authorization_url)}>Copy link</button></>}
    <button type="button" disabled={!!busy} onClick={()=>void cancelLogin()}>Cancel</button>
   </div>
   {login.authorization_url&&showLink&&<input aria-label="Sign-in link" readOnly value={login.authorization_url} onFocus={e=>e.currentTarget.select()}/>}
   {codeRequired&&login.login_id&&<form onSubmit={submitCode}><label>Paste the code from your browser <input aria-label="Authorization code" value={code} onChange={e=>setCode(e.target.value)} autoComplete="off"/></label><button disabled={!!busy||!code.trim()}>Finish sign-in</button></form>}
   {login.emailRequired&&<form onSubmit={e=>{e.preventDefault();void action(login.id,'login',emailHint.trim());}}>
    <label>Account email <input type="email" value={emailHint} onChange={e=>setEmailHint(e.target.value)} required/></label>
    <button disabled={!!busy||!emailHint.trim()}>Continue in browser</button>
   </form>}
   {login.blocked&&<button disabled={!!busy} onClick={()=>action(login.id,'login-idle')}>Renew when idle</button>}
   {!login.blocked&&!login.emailRequired&&!waitingId&&!codeRequired&&<div>
    <button disabled={!!busy} onClick={()=>action(login.id,'verify')}>{busy===login.id?'Checking…':'Check sign-in'}</button>
    {!login.login_started&&<button disabled={!!busy} onClick={()=>action(login.id,'login')}>Try sign-in again</button>}
   </div>}
   {login.login_cmd&&<details><summary>Manual setup command</summary><code>{login.login_cmd}</code><IconButton label="Copy sign-in command" onClick={()=>navigator.clipboard.writeText(login.login_cmd).then(()=>notify('Sign-in command copied')).catch(()=>notify('Clipboard is unavailable'))}><Copy size={14}/></IconButton></details>}
 </div>;
 const noteBox=(id:string)=>note?.id===id&&<p role="alert" className="inline-error account-note">{note.text}{note.force&&<> <button disabled={!!busy} onClick={()=>{const a=[...rows.claude,...rows.codex].find(x=>x.id===id);if(a)void signOut(a,true);}}>Sign out anyway</button><button onClick={()=>setNote(null)}>Cancel</button></>}</p>;
 return <section className="accounts-panel"><div className="settings-section-heading accounts-heading"><h2 className="sr-only">Connected accounts</h2><div className="account-tools"><IconButton label="Refresh account usage" disabled={!!busy} onClick={()=>action('','refresh')}><RefreshCw size={16}/></IconButton><button className="account-add" aria-expanded={adding} onClick={()=>setAdding(v=>!v)}><Plus size={15}/>Add account</button></div></div>{error&&<p role="alert" className="inline-error">{error}</p>}
 {adding&&<form className="add-account" onSubmit={add}><select aria-label="New account provider" value={tool} onChange={e=>setTool(e.target.value)}><option value="claude">Claude</option><option value="codex">Codex</option></select><input aria-label="Account name (optional)" placeholder="Name (optional)" maxLength={60} value={name} onChange={e=>setName(e.target.value)}/><button disabled={!!busy}>Continue in browser</button><button type="button" onClick={()=>{setAdding(false);setAddError('')}}>Cancel</button>{addError&&<p role="alert" className="inline-error">{addError}</p>}</form>}
 {!loaded&&!error&&<p role="status">Loading accounts…</p>}{loaded&&(['claude','codex'] as const).map(provider=><section className="account-provider" key={provider} aria-label={provider==='claude'?'Claude':'Codex'}><h3>{provider==='claude'?'Claude':'Codex'}</h3>{rows[provider].map(a=>{const signedIn=a.logged_in&&!!a.email,needsSignIn=needsAccountSignIn(a);return <div className="account-row" key={a.id} data-account={a.id}><div className="account-heading"><div className="account-identity">
   {editing?.id===a.id?<input className="nm-in" aria-label="Display name" autoFocus maxLength={60} value={editing.value} onFocus={e=>e.currentTarget.select()} onChange={e=>setEditing({id:a.id,value:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();void saveName(a,editing.value,false);}else if(e.key==='Escape'){skipBlur.current=true;setEditing(null);}}} onBlur={()=>{if(skipBlur.current){skipBlur.current=false;return;}void saveName(a,editing.value,true);}}/>:<span className="nm" role="button" tabIndex={0} title={noRename?undefined:'Click to rename'} onClick={()=>startEdit(a)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();startEdit(a);}}}>{displayName(a)}</span>}
   {a.plan&&<span className="plan">{a.plan}</span>}{secondaryEmail(a)&&<div className="em">{secondaryEmail(a)}</div>}
   {editing?.id===a.id&&editing.error&&<div className="inline-error" role="alert">{editing.error}</div>}
   {signedIn?<div className="snap">{[a.org?.toLowerCase()===a.email?.toLowerCase()+"'s organization"?'':a.org,usageSnapshot(a,now).label].filter(Boolean).join(' · ')}</div>:<div className="snap">Signed out</div>}
  </div><div className="account-actions">{!a.is_default&&!a.global&&<IconButton label={'Remove '+(a.email||a.label)} disabled={!!busy} onClick={()=>remove(a)}><Trash2 size={15}/></IconButton>}{signedIn&&!needsSignIn?!noLogout&&<button disabled={!!busy} onClick={()=>signOut(a)}>Sign out</button>:<button disabled={!!busy} onClick={()=>action(a.id,'login')}>{needsSignIn?'Sign in':'Sign in again'}</button>}</div></div>
  {noteBox(a.id)}{signedIn&&<AccountWarning account={a} accounts={connected} onAccounts={()=>{void load();void refresh()}}/>}{signedIn&&((a.windows||[]).length?<div className="account-quotas">{(a.windows||[]).map((w,i)=><div className="quota" key={w.kind+i}><span>{w.label||w.kind}</span>{w.percent==null?<span/>:<span className="quota-bar" role="img" aria-label={Math.round(w.percent)+'% used'}><i className={barHot(w.percent)?'hot':undefined} style={{width:cleanPercent(w.percent)+'%'}}/></span>}<span className="quota-percent">{w.percent==null?'Unknown':Math.round(w.percent)+'%'}</span><QuotaReset value={w.resets_at} now={now}/></div>)}</div>:<small className="quota-reset">Reset schedule unavailable</small>)}{signedIn&&a.credits?.balance!=null&&<small className="quota-reset">Workspace credits: {a.credits.balance}</small>}{signedIn&&a.error&&<small className="inline-error">{a.error}</small>}{loginBox(a.id)}</div>})}{!rows[provider].length&&<p className="empty-note">No {provider==='claude'?'Claude':'Codex'} accounts yet.</p>}</section>)}
 {onGitHubAccounts&&<section className="account-provider"><h3>GitHub</h3>{githubError?<p role="alert" className="inline-error">{githubError}</p>:github===null?<p>Loading GitHub accounts…</p>:github.length?github.map(a=><div className="account-row" key={a.id}><strong>{a.label}</strong></div>):<p className="empty-note">No connected GitHub accounts.</p>}{github!==null&&!github.length&&<button disabled={githubBusy} onClick={()=>void loadGithub(true)}>Connect GitHub</button>}</section>}
 {login&&![...rows.incomplete,...rows.claude,...rows.codex].some(a=>a.id===login.id)&&loginBox(login.id)}
 {pending.length>0&&<details className="account-pending" open><summary>Incomplete sign-ins ({pending.length})</summary><p>These setups are not connected accounts. Sign in to finish one, or remove it.</p>{pending.map(a=><div className="pending-account" key={a.id}><span>{a.tool==='claude'?'Claude':'Codex'} · {a.label||a.id}<small>{a.renewal_error||(a.renewal_pending?'Waiting for chats to be idle':a.error)||'Sign-in not verified'}</small></span><button disabled={!!busy} onClick={()=>action(a.id,'login')}>Sign in</button>{!a.is_default&&!a.global&&<button disabled={!!busy} onClick={()=>remove(a)}>Remove</button>}{noteBox(a.id)}{loginBox(a.id)}</div>)}</details>}
 </section>;
}

// Accounts page: the account list and the usage analytics that used to live in Settings.
export function AccountsPage(props:Parameters<typeof AccountsPanel>[0]={}){
 const [tab,setTab]=useState('Accounts');
 const {open}=useWorkbench();
 return <div className="settings-page"><header><div className="settings-section-heading"><h1>Accounts</h1><button type="button" onClick={()=>open({kind:"settings",title:"Settings",meta:{settingsTab:"Appearance"}},"tab")}>Settings</button></div><div className="settings-tabs" role="tablist" aria-label="Accounts sections">{['Accounts','Analytics'].map(t=><button key={t} role="tab" aria-selected={t===tab} onClick={()=>setTab(t)}>{t}</button>)}</div></header>
  {tab==='Accounts'?<AccountsPanel {...props}/>:<><Analytics/><Insights mode="analytics"/></>}</div>;
}
