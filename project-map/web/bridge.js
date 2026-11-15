/* Same map bundle in a browser frame or a restricted native webview. */
(()=>{
 const params=new URLSearchParams(location.search),boot=window.__PROJECT_MAP_BOOT__||{},native=typeof acquireVsCodeApi==='function'?acquireVsCodeApi():null,pending=new Map();let seq=0;
 let parentOrigin='';try{const origin=new URL(document.referrer).origin;if(['http://127.0.0.1:8744','http://localhost:8744'].includes(origin))parentOrigin=origin}catch{}
 const embedded=!!(native||boot.embedded||params.get('embedded')==='1');
 const host=window.ProjectMapHost={boot,embedded,visible:boot.visible!==false};
 host.prefs={getItem:name=>{try{return native?.getState()?.preferences?.[name]??localStorage.getItem(name)}catch{return null}},setItem:(name,value)=>{if(!name.startsWith('project-map-'))return;try{localStorage.setItem(name,value)}catch{}if(native){const state=native.getState()||{};native.setState({...state,preferences:{...state.preferences,[name]:String(value)}})}}};
 function send(message){if(native)native.postMessage(message);else if(parentOrigin&&parent!==window)parent.postMessage(message,parentOrigin)}
 if(native){const previous=native.getState()||{};native.setState({...previous,resource:boot.resource||previous.resource});}
 host.request=async path=>{
  if(!/^\/api\/(projects|graph|session|resource)(\?|$)/.test(path))throw Error('Unsupported map request');
  if(native)return new Promise((resolve,reject)=>{const id=String(++seq),timer=setTimeout(()=>{pending.delete(id);reject(Error('Library map request timed out'))},15000);pending.set(id,{resolve,reject,timer});send({type:'project-map:request',id,path})});
  const r=await fetch(path,{cache:'no-store'}),d=await r.json();if(!r.ok||d.error)throw Error(d.error||r.statusText);return d;
 };
 host.copy=async text=>{if(native){send({type:'project-map:copy',text});return}await navigator.clipboard.writeText(text)};
 host.openSession=key=>send({type:'project-map:open-session',key});
 host.external=url=>{if(native)send({type:'project-map:external',url});else window.open(url,'_blank','noopener')};
 host.ready=()=>send({type:'project-map:ready'});
 addEventListener('message',e=>{
  if(!native&&(e.source!==parent||e.origin!==parentOrigin||!parentOrigin))return;
  const m=e.data;if(!m||typeof m!=='object')return;
  if(m.type==='project-map:response'&&native){const p=pending.get(String(m.id));if(!p)return;pending.delete(String(m.id));clearTimeout(p.timer);m.error?p.reject(Error(String(m.error))):p.resolve(m.data)}
  if(m.type==='project-map:theme'&&['dark','light'].includes(m.theme))document.body.classList.toggle('dark',m.theme==='dark');
  if(m.type==='project-map:visibility')host.visible=m.visible===true;
 });
 if(embedded)document.body.classList.add('embedded');
 document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(native&&a&&/^https?:\/\//.test(a.href)){e.preventDefault();host.external(a.href)}});
})();
