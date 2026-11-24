/* Shared recovery card. Recovery never reads or changes a composer draft. */
(function(root){
'use strict';
const name=p=>p==='codex'?'Codex':'Claude';
const differentLogin=(a,b)=>(a.duplicate_of||a.id)!==(b.duplicate_of||b.id);
const date=v=>{const n=typeof v==='number'?v*(v<1e12?1000:1):Date.parse(v||'');return Number.isFinite(n)?new Date(n).toLocaleString():''};
// A remote run names its host, so an SC account is never confused with this Mac's same-named profile.
const remoteRun=run=>Boolean(run?.host_id&&!['mac','local'].includes(run.host_id));
function where(f,run){const host=f.host_label||run?.host_label||(remoteRun(run)?run.host_id:'');return host?host+' ':''}
function description(f,run){const who=f.account_label||f.account_id||'this account',provider=where(f,run)+name(f.provider),reset=date(f.resets_at);if(f.kind==='limit_reached'&&/workspace is out of credits/i.test(f.raw||''))return `The ${provider} workspace for ${who} is out of credits. Ask its owner to refill or continue with another account.`;return f.kind==='account_required'?`Choose which ${provider} account owns this chat. Your message is kept and is sent once you choose.`:['auth_expired','not_logged_in'].includes(f.kind)?`${provider} sign-in expired for ${who}.`:f.kind==='limit_reached'?`${/week/i.test(f.raw||'')?'Weekly limit':'Usage limit'} reached on ${who}${reset?'; resets '+reset:'. Reset time unavailable.'}`:f.kind==='rate_limited'?`${provider} is temporarily rate limited. Retry shortly.`:`${provider} could not finish this message.`;}
function warnings(a){if(!a)return [];if(Array.isArray(a.warnings))return a.warnings.map(w=>typeof w==='string'?w:[w.message,w.expires_at&&date(w.expires_at)?'Expires '+date(w.expires_at):w.resets_at&&date(w.resets_at)?'Resets '+date(w.resets_at):''].filter(Boolean).join(' · ')).filter(Boolean);const result=[],expires=a.login_expires_at||a.loginExpiresAt;if(expires){const t=typeof expires==='number'?expires*(expires<1e12?1000:1):Date.parse(expires);if(t-Date.now()<=86400000)result.push((t<Date.now()?'Sign-in expired':'Sign-in expires')+' '+date(expires));}for(const w of a.windows||[])if(Number(w.percent)>=90)result.push(`${w.label||w.kind||'Usage'} ${Math.round(w.percent)}% used${date(w.resets_at)?'; resets '+date(w.resets_at):''}`);return result;}
// Several profiles can share one login email; name the profile too so each choice is distinct.
function accountName(a,all=[]){if(!a)return '';const email=a.email||'',label=a.label&&a.label!==email?a.label:'',peers=(all||[]).filter(x=>x&&!x.duplicate_of&&differentLogin(a,x)&&(x.tool||x.provider)===(a.tool||a.provider));
 if(email&&!peers.some(x=>x.email===email))return email;const name=label&&email?label+' ('+email+')':label||email||a.id;return peers.some(x=>(x.email||'')===email&&(x.label||'')===(a.label||''))?name+' · '+a.id:name;}
const mounts=new WeakMap();
function mount(host,options){let card=mounts.get(host);if(card){card.options=options;card.update();return card}card={options,busy:false,login:null,verified:false,code:'',error:'',adding:false,email:'',signature:''};mounts.set(host,card);
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
 const button=(label,fn)=>{const n=el('button',label);n.type='button';n.disabled=card.busy||card.options.disabled;n.onclick=fn;return n};
 async function execute(fn,label='Working…'){if(card.busy)return;card.busy=true;card.busyText=label;card.error='';card.update(true);try{await fn()}catch(e){card.error=e.message||String(e)}finally{card.busy=false;card.update(true)}}
 const continuing='Continuing your message…';
 async function verify(){const account=card.login?.account_id||card.options.run.failure.account_id,result=await card.options.request('/api/accounts/verify',{id:account});if(!(result.accounts||[]).some(a=>a.id===account&&a.logged_in===true))throw Error('Sign-in is not verified yet. Finish signing in, then check again.');card.verified=true;card.verifiedAccount=(result.accounts||[]).find(a=>a.id===account);card.login=null;card.options.onAccounts?.(result);}
 // After a verified sign-in the same failed message continues once; the server's stable request id makes repeats return that run.
 async function verifyAndContinue(){await verify();if(card.options.run.key?.startsWith('account:')||card.continued)return;card.busyText=continuing;card.update(true);await call('retry')}
 async function call(action,extra={}){const opts=card.options,run=opts.run;{const result=action==='login'&&run.key?.startsWith('account:')?{login:await opts.request('/api/accounts/login',{id:run.failure.account_id,open_browser:true}),account_id:run.failure.account_id}:await opts.request('/api/run/recover',{...(run.id?{id:run.id}:{key:run.key}),action,...extra});if(card.options.run.id!==run.id||card.options.run.key!==run.key)return;if(action==='login'){const login=result.login||result;card.login={...login,account_id:result.account_id||run.failure.account_id,host_id:result.host_id||login.host_id};card.verified=false;}else{if(!result.run?.id)throw Error('Recovery did not return a run. Your message and draft are retained.');card.continued=true;opts.onRun(result.run,result.events||[]);}}}
 async function recover(action,extra={}){await execute(()=>call(action,extra),action==='login'?'Starting sign-in…':['retry','bind','account','handoff'].includes(action)?continuing:'Working…')}
 card.update=function(force=false){const opts=card.options,f=opts.run?.failure,identity=opts.run?.id||opts.run?.key;if(card.identity!==identity){card.identity=identity;card.continued=false;card.login=null;card.verified=false;card.verifiedAccount=null;card.code='';card.error='';card.adding=false}const loginAccount=card.login?.host_id?null:(opts.accounts||[]).find(a=>a.id===card.login?.account_id);
 if(card.login&&loginAccount){if(loginAccount.login?.login_id)card.login={...loginAccount.login,account_id:loginAccount.id};else if(loginAccount.renewal_pending===false&&!loginAccount.login_starting){card.login={...card.login,queued:false,renewal_pending:false,login_pending:false}}if(loginAccount.login_error||loginAccount.renewal_error)card.error=loginAccount.login_error||loginAccount.renewal_error;}
 if(card.login&&!card.login.host_id&&!card.verified&&!card.pollTimer){card.pollTimer=setTimeout(async()=>{card.pollTimer=null;if(!host.isConnected||!card.login)return;try{const result=await card.options.request('/api/accounts');if(result.accounts)card.options={...card.options,accounts:result.accounts}}catch{}card.update()},2000)}
 const signature=JSON.stringify([opts.run?.id,opts.run?.key,f,opts.accounts,opts.disabled]);if(!force&&signature===card.signature)return;card.signature=signature;host.replaceChildren();host.hidden=!f||f.kind==='other';if(host.hidden)return;host.className='auth-recovery';host.setAttribute('role','status');host.setAttribute('aria-label','Recover failed message');host.append(el('strong',description(f,opts.run)));if(opts.run.message){const details=el('details');details.append(el('summary','Failed message retained'),el('p',opts.run.message));host.append(details)}
 const actions=el('div');actions.className='auth-recovery-actions';const remote=remoteRun(opts.run);
 if(f.kind==='account_required'){for(const c of f.candidates||[])actions.append(button('Continue on '+accountName(c,f.candidates)+(c.logged_in===false?' (signed out)':''),()=>recover('bind',{account_id:c.id})));}
 if(['auth_expired','not_logged_in'].includes(f.kind))actions.append(button('Sign in again',()=>recover('login')));
 if(!opts.run.key?.startsWith('account:')&&f.kind!=='account_required'&&(!['auth_expired','not_logged_in'].includes(f.kind)||card.verified)){if(card.verifiedAccount&&card.verifiedAccount.id!==f.account_id)actions.append(button('Continue on '+accountName(card.verifiedAccount,opts.accounts),()=>recover('account',{account_id:card.verifiedAccount.id})));else actions.append(button('Retry message',()=>recover('retry')));}
 if(['auth_expired','not_logged_in'].includes(f.kind)&&!card.verified&&!card.login&&!remote)actions.append(button('Check sign-in',()=>execute(verifyAndContinue)));
 if(!opts.run.key?.startsWith('account:')&&(f.kind==='limit_reached'||f.kind==='auth_expired'||f.kind==='not_logged_in')){
  if(!remote)for(const account of opts.accounts||[])if(!account.duplicate_of&&(account.tool||account.provider)===f.provider&&account.id!==f.account_id&&account.logged_in!==false&&!account.disabled)actions.append(button('Continue on '+accountName(account,opts.accounts),()=>recover('account',{account_id:account.id})));
  const target=f.provider==='codex'?'claude':'codex';actions.append(button('Move to '+name(target),()=>recover('handoff',{provider:target})));
  if(!remote)actions.append(button('Add an account',()=>{card.adding=!card.adding;card.update(true)}));
 }host.append(actions);
 if(card.adding){const form=el('form'),input=el('input');input.type='email';input.placeholder='Account email';input.setAttribute('aria-label','New account email');input.value=card.email;input.oninput=()=>{card.email=input.value};const add=button('Continue to sign in',()=>{});add.type='submit';form.onsubmit=e=>{e.preventDefault();execute(async()=>{const created=await opts.request('/api/accounts/add',{tool:f.provider,label:card.email.trim()});const login=await opts.request('/api/accounts/login',{id:created.id,open_browser:true});card.login={...login,account_id:created.id};card.adding=false;})};form.append(input,add);host.append(form)}
 if(card.login){const login=card.login,box=el('div');box.append(el('p',login.message||((login.queued||login.renewal_pending)?'Sign-in will start when chats on this account are idle.':login.login_pending?'Starting sign-in…':'Finish sign-in in your browser, then check here.')));if(login.needs_code!==false&&login.code_required!==false&&(login.needs_code||login.code_required||(f.provider==='claude'&&login.authorization_url))){const form=el('form'),input=el('input');input.setAttribute('aria-label','Authorization code');input.autocomplete='off';input.value=card.code;input.oninput=()=>{card.code=input.value};const send=button('Complete sign-in',()=>{});send.type='submit';form.onsubmit=e=>{e.preventDefault();const code=card.code.trim();if(!code)return;execute(async()=>{card.code='';if(login.host_id)return call('login_code',{login_id:login.login_id,code});await opts.request('/api/accounts/login/code',{id:login.account_id,login_id:login.login_id,code});await verifyAndContinue()},login.host_id?'Finishing sign-in, then continuing your message…':'Finishing sign-in…')};form.append(input,send);box.append(form)}if(login.browser_opened===false&&login.authorization_url){const link=el('a','Open the sign-in page');link.href=login.authorization_url;link.target='_blank';link.rel='noopener';box.append(link)}if(!login.host_id)box.append(button('Check sign-in',()=>execute(verifyAndContinue)));host.append(box)}
 if(card.verified)host.append(el('p',opts.run.key?.startsWith('account:')?'Sign-in verified.':'Sign-in verified. Retry your failed message when ready.'));
 if(card.error){const p=el('p',card.error);p.setAttribute('role','alert');host.append(p)}
 if(card.busy)host.append(el('p',card.busyText||'Working…'));
 if(f.raw){const detail=el('details');detail.append(el('summary','Provider error'),el('pre',f.raw));host.append(detail)}
 };card.update();return card;
}
const warningMounts=new WeakMap();
// Notices from notices.py carry shown/dismissed/muted, shared by every client. An older server
// gets a local record keyed by the reset hour, because provider reset times drift between polls.
const served=w=>typeof w?.shown==='boolean',dismissedNow=new Set();
function noticeKey(account,w){if(served(w))return w.key;return String(w.key||account.id+':'+w.kind+':'+(w.expires_at||w.resets_at)).replace(/\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:\d\d)?/g,t=>{const ms=Date.parse(t);return Number.isFinite(ms)?String(Math.round(ms/3600000)):t});}
const local=k=>{try{return localStorage.getItem('library-notice:'+k)||''}catch{return ''}},remember=(k,v)=>{try{localStorage.setItem('library-notice:'+k,v)}catch{}};
function live(a,only){return (a?.warnings||[]).filter(w=>typeof w==='string'||(!w.muted&&!dismissedNow.has(noticeKey(a,w))&&!w.dismissed&&local(noticeKey(a,w))!=='dismissed'&&(!only||only.includes(noticeKey(a,w)))));}
function mountWarning(host,options){let card=warningMounts.get(host);if(card){card.options=options;card.update();return card}card={options,busy:false,error:'',signature:'',recovery:document.createElement('div')};warningMounts.set(host,card);
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
 card.update=function(force=false){const opts=card.options,a=opts.account,shown=opts.popupWarning?[opts.popupWarning]:Array.isArray(a?.warnings)?live(a,opts.only):null,messages=warnings(shown?{...a,warnings:shown}:a);const sig=JSON.stringify([messages,(opts.accounts||[]).map(x=>[x.id,x.email,x.label,x.tool,x.provider,x.logged_in,x.disabled,x.duplicate_of]),a?.login,a?.renewal_pending,a?.renew_requires_idle,a?.renew_when_idle,opts.disabled,opts.only]);if(!force&&sig===card.signature)return;card.signature=sig;host.replaceChildren();host.hidden=!messages.length;if(host.hidden)return;host.className='auth-warning';host.setAttribute('role','status');const body=el('div');const label=accountName(a,opts.accounts);for(const message of messages)body.append(el('p',opts.showAccountLabel===false||a?.email&&message.includes(a.email)?message:label+': '+message));host.append(body);
 const button=(label,fn)=>{const b=el('button',label);b.type='button';b.disabled=card.busy||opts.disabled;b.onclick=fn;host.append(b);return b};
 async function act(fn){if(card.busy)return;card.busy=true;card.error='';card.update(true);try{await fn()}catch(e){card.error=e.message||String(e)}finally{card.busy=false;card.update(true)}}
 const expiry=(shown||a.warnings||[]).some(w=>w.expires_at||/auth|login|sign.in|expir|not_logged_in/i.test(w.kind||''))||messages.some(m=>/sign.in|expir/i.test(m));
 if(expiry)button((a.renew_when_idle||a.renew_requires_idle||(a.warnings||[]).some(w=>w.renew_requires_idle))?'Renew when idle':'Renew sign-in now',()=>act(async()=>{const login=await opts.request('/api/accounts/login',{id:a.id,open_browser:true,...((a.renew_when_idle||a.renew_requires_idle||(a.warnings||[]).some(w=>w.renew_requires_idle))?{when_idle:true}:{})});card.recovery.hidden=false;mount(card.recovery,{run:{key:'account:'+a.id,failure:{kind:'auth_expired',provider:a.tool||a.provider,account_id:a.id,account_label:a.email||a.label}},accounts:[],request:opts.request,onRun:()=>{},onAccounts:r=>{opts.onAccounts?.(r);card.recovery.hidden=true;if(opts.onDismiss)void opts.onDismiss();else card.update(true)}});const recovery=mounts.get(card.recovery);recovery.login={...login,account_id:a.id};recovery.update(true);}));
 if(shown?shown.some(w=>/usage|quota|limit/.test(w.kind||'')):(a.windows||[]).some(w=>Number(w.percent)>=90)){
  for(const other of opts.accounts||[])if(!other.duplicate_of&&differentLogin(a,other)&&(other.tool||other.provider)===(a.tool||a.provider)&&other.logged_in!==false&&!other.disabled)button('Use '+accountName(other,opts.accounts)+' for new chats',()=>act(async()=>{const result=await opts.request('/api/accounts/default',{tool:a.tool||a.provider,id:other.id});dismiss();opts.onAccounts?.(result);}));
  button('Add an account',()=>{mount(card.recovery,{run:{key:'account:'+a.id,failure:{kind:'limit_reached',provider:a.tool||a.provider,account_id:a.id}},accounts:[],request:opts.request,onRun:()=>{},onAccounts:opts.onAccounts});const recovery=mounts.get(card.recovery);recovery.adding=true;recovery.update(true)});
 }
 function dismiss(){if(opts.onDismiss)return opts.onDismiss();dismissNotice((shown||[]).filter(w=>typeof w==='object').map(w=>noticeKey(a,w)),opts.request);card.update(true)}
 if(!opts.hideClose)host.prepend(closeButton(dismiss));
 if(card.error){const p=el('p',card.error);p.setAttribute('role','alert');host.append(p)}host.append(card.recovery);
 if(a.login?.login_id){const recovery=mount(card.recovery,{run:{key:'account:'+a.id,failure:{kind:'auth_expired',provider:a.tool||a.provider,account_id:a.id,account_label:a.email||a.label}},accounts:[],request:opts.request,onRun:()=>{},onAccounts:opts.onAccounts});if(recovery.login?.login_id!==a.login.login_id){recovery.login={...a.login,account_id:a.id};recovery.update(true)}}else if(a.renewal_pending)host.append(el('p','Renewal is queued until chats on this account are idle.'));
 };card.update();return card;}

function closeButton(close){
 const button=document.createElement('button');
 button.type='button';button.className='notice-close';button.textContent='×';
 button.title='Dismiss account notice';button.setAttribute('aria-label',button.title);button.onclick=close;
 return button;
}
function dismissNotice(keys,request){
 if(!keys.length)return;
 for(const key of keys){dismissedNow.add(key);remember(key,'dismissed')}
 const retry=attempt=>Promise.resolve().then(()=>request('/api/notices',{action:'archive',keys})).catch(()=>{
  if(attempt<2)setTimeout(()=>void retry(attempt+1),1000*(attempt+1));
 });
 void retry(0);
}
const api={mount,mountWarning,description,warnings,date,noticeKey,live,accountName};
root.LibraryRecovery=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
