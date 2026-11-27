import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MessageSquare, Trash2} from 'lucide-react';
import {RenderedHtmlEditor,type HtmlParagraph,type HtmlParagraphEditor} from '../viewers/RenderedHtmlEditor';
import {RenderedMarkdownEditor,type MarkdownParagraphEditor} from '../viewers/RenderedMarkdownEditor';
import {previewHtml,type HtmlModel} from '../viewers/htmlEdit';
import {RenderedDocumentDiff} from '../viewers/RenderedDiff';
import './review.css';
declare const acquireVsCodeApi:any;
const host=acquireVsCodeApi();
type Comment={id:string;text:string;quote:string;created:number};
type Review={previous:string|null;comments:Comment[]};
type Paragraph={quote:string;right:number;top:number;target:HTMLElement|HtmlParagraph};
const boot=(window as any).__DOCUMENT_REVIEW__;
const pending=new Map<string,{resolve:(v:any)=>void;reject:(e:Error)=>void}>();
function request(action:string,args:Record<string,any>={}):Promise<any>{
 const id=crypto.randomUUID();
 return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});host.postMessage({type:'document-review',action,id,...args});});
}
window.addEventListener('message',e=>{
 if(e.origin!==window.location.origin)return;
 const m=e.data,call=m?.type==='review-reply'&&pending.get(m.id);
 if(!call)return;
 pending.delete(m.id);
 if(m.error)call.reject(new Error(m.error));else call.resolve(m.value);
});
async function renderPreview(source:string,model:HtmlModel,token:string,readOnly:boolean){
 const loader=Object.assign((value:string,kind:'image'|'style')=>request('asset',{value,kind}),{css:(value:string)=>request('asset',{value,kind:'css'})});
 return previewHtml(source,model,token,loader,readOnly,boot.nonce);
}
function Editor(){
 const restore=useRef(host.getState());
 const [text,setText]=useState<string>(boot.text),[editing,setEditing]=useState(false),[mode,setMode]=useState('Preview');
 const [review,setReview]=useState<Review>(),[error,setError]=useState(''),[status,setStatus]=useState('');
 const [blocked,setBlocked]=useState(false),[comments,setComments]=useState(false),[quote,setQuote]=useState(''),[message,setMessage]=useState(''),[posting,setPosting]=useState(false);
 const [stored,setStored]=useState<{writer:string,text:string}[]>(boot.recoveries||[]);
 const [recover,setRecover]=useState<string|undefined>(restore.current?.draft);
 const [paragraph,setParagraph]=useState<Paragraph|null>(null),[undo,setUndo]=useState<{before:string,after:string}>();
 const [find,setFind]=useState(false),[search,setSearch]=useState(''),[next,setNext]=useState(0);
 const htmlEditor=useRef<HtmlParagraphEditor>(null),mdEditor=useRef<MarkdownParagraphEditor>(null);
 const latest=useRef(text),base=useRef<string>(boot.text),version=useRef(boot.version),revision=useRef(0),conflict=useRef(false);
 const hideTimer=useRef<ReturnType<typeof setTimeout>>(undefined);
 const last=useRef<Paragraph|null>(null),selection=useRef(''),commentId=useRef(''),composer=useRef<HTMLTextAreaElement>(null);
 const html=boot.format==='html';
 const [assets,setAssets]=useState(boot.assets),[renewal,setRenewal]=useState(0),[busy,setBusy]=useState(false);
 const [interactive,setInteractive]=useState<boolean|null>(null),[webAssets,setWebAssets]=useState(false);
 const livePreview=html&&boot.previewUrl&&!editing&&(interactive??/<script[\s>]|\son(?:click|load)\s*=|\btarget\s*=\s*["']?_blank\b|\\[[(]|\$\$|\$[^\s$<][^$<\n]*\$/i.test(text));
 const fail=(e:Error)=>{setError(e.message);};
 function reviewNow(){request('review').then(setReview).catch(fail);}
 function persist(){
  const draft=latest.current===base.current?recover:latest.current;
  host.setState(draft===undefined?{}:{draft});
 }
 function save(){host.postMessage({type:'document-review',id:crypto.randomUUID(),action:'flush'});}
 function change(value:string){
  if(conflict.current)return;
  latest.current=value;setText(value);setStatus('Saving…');persist();
  host.postMessage({type:'document-review',id:crypto.randomUUID(),action:'stage',revision:++revision.current,version:version.current,text:value});
 }
 useEffect(()=>{
  const sync=(e:MessageEvent)=>{
   if(e.origin!==window.location.origin)return;
   const m=e.data;
   if(m?.type==='review-saved'){
    version.current=m.version;
    if(m.revision===revision.current){base.current=latest.current;persist();setStatus('Saved');}
    return;
   }
   if(m?.type==='review-failed'){conflict.current=true;setEditing(false);setRecover(latest.current);setStatus('Draft retained');setError(m.error);return;}
   if(!['review-update','review-conflict'].includes(m?.type))return;
   if(m.type==='review-conflict'||(!m.force&&latest.current!==base.current)){
    conflict.current=true;setEditing(false);setError('The source changed elsewhere. Renew to load the saved version.');if(latest.current!==base.current)setRecover(latest.current);return;
   }
   if(latest.current!==base.current)setRecover(latest.current);
   conflict.current=false;setError('');setAssets(m.assets);setRenewal(n=>n+1);setStatus('Renewed');
   base.current=latest.current=m.text;version.current=m.version;setText(m.text);
  };
  const shortcut=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();void save();}};
  window.addEventListener('message',sync);window.addEventListener('keydown',shortcut);
  return()=>{window.removeEventListener('message',sync);window.removeEventListener('keydown',shortcut);};
 },[]);
 useEffect(()=>{if(comments||mode==='Diff')reviewNow();},[comments,mode,status,renewal]);
 useEffect(()=>{last.current=null;selection.current='';setParagraph(null);},[mode]);
 function hide(){clearTimeout(hideTimer.current);hideTimer.current=setTimeout(()=>setParagraph(null),400);}
 function point(p:Paragraph|null){if(!p){hide();return;}clearTimeout(hideTimer.current);last.current=p;setParagraph(p);}
 function comment(p=last.current,preferSelection=true){
  const selected=preferSelection?(html?selection.current:window.getSelection()?.toString()):'';
  setQuote((selected?.trim()||p?.quote||'').slice(0,2000));setComments(true);setParagraph(null);
  requestAnimationFrame(()=>composer.current?.focus());
 }
 async function post(){
  if(posting||!message.trim())return;
  setPosting(true);commentId.current||=crypto.randomUUID();
  try{setReview(await request('comment',{idempotency:commentId.current,text:message,quote}));commentId.current='';setMessage('');setQuote('');}
  catch(e){fail(e as Error);}finally{setPosting(false);}
 }
 function remove(p:Paragraph){
  if(!editing||blocked||conflict.current)return;
  if(p.target instanceof HTMLElement)mdEditor.current?.deleteParagraph(p.target);
  else {const before=latest.current,after=htmlEditor.current?.deleteParagraph(p.target,before);if(after!=null)setUndo({before,after});}
  setParagraph(null);last.current=null;
 }
 async function fileAction(action:'renew'|'rewind'){
  setBusy(true);setError('');
  try{await request(action,{version:version.current});setEditing(false);}
  catch(e){fail(e as Error);}finally{setBusy(false);}
 }
 function toggle(){
  if(editing)void save();else setMode('Preview');
  setEditing(!editing);
 }
 const props={text,file:boot.file,onChange:change,onBlocked:setBlocked,onRecovery:()=>{},onOpen:(resource:any)=>{void request('open',{resource}).catch(fail);}};
 return <>
  <header className="review-bar">
   <nav aria-label="Document views">{['Preview','Source','Diff'].map(tab=><button key={tab} aria-pressed={mode===tab} disabled={blocked} onClick={()=>{if(editing)void save();setEditing(false);setMode(tab);}}>{tab}</button>)}</nav>
   <button disabled={busy||(!conflict.current&&latest.current!==base.current)} onClick={()=>void fileAction('renew')}>Renew</button>
   {mode==='Diff'&&review?.previous!=null&&<button disabled={busy||boot.readOnly||conflict.current||latest.current!==base.current} onClick={()=>void fileAction('rewind')}>Rewind to previous version</button>}
   <button aria-pressed={editing} disabled={boot.readOnly||blocked||conflict.current} title={boot.readOnly?'Read-only file':editing?'Finish editing':'Edit rendered text'} onClick={toggle}>{editing?'Edit':'Read Only'}</button>
   {html&&mode==='Preview'&&boot.previewUrl&&<button aria-pressed={!!livePreview} onClick={()=>{if(editing)void save();setEditing(false);setInteractive(!livePreview);setFind(false);}}>Interactive preview</button>}
   {livePreview&&mode==='Preview'&&<label><input type="checkbox" checked={webAssets} onChange={e=>{const enabled=e.target.checked;void request('preview-network',{enabled}).then(()=>setWebAssets(enabled)).catch(fail);}}/>Allow web assets</label>}
   <button onMouseDown={e=>e.preventDefault()} onClick={()=>comment()}><MessageSquare size={14}/>Comment</button>
   {undo&&latest.current===undo.after&&editing&&<button onClick={()=>{htmlEditor.current?.replaceParagraphSource(undo.after,undo.before);setUndo(undefined);}}>Undo deletion</button>}
   <span role="status">{status}</span>
  </header>
  {error&&<div className="review-alert" role="alert">{error}</div>}
  {stored.map(d=><div className="review-alert" key={d.writer}>An additional draft is available. <button onClick={()=>request('recover',d).then(()=>setStored(all=>all.filter(x=>x.writer!==d.writer))).catch(fail)}>Open draft as a copy</button></div>)}
  {recover!==undefined&&!stored.some(d=>d.text===recover)&&<div className="review-alert">A draft is available. <button onClick={()=>request('recover',{text:recover}).catch(fail)}>Open draft as a copy</button></div>}
  {html&&find&&mode==='Preview'&&<div className="review-bar"><input autoFocus aria-label="Find in document" value={search} onChange={e=>setSearch(e.target.value)}/><button onClick={()=>setNext(n=>n+1)}>Next</button><button onClick={()=>setFind(false)}>Close</button></div>}
  <div className="review-layout">
  <section className="review-body" onScrollCapture={hide} onPointerMove={e=>{
   if(html||mode!=='Preview')return;
   const el=(e.target as Element).closest<HTMLElement>('.tiptap p');
   if(!el){hide();return;}
   const rect=el.getBoundingClientRect();point({target:el,quote:el.innerText,right:rect.right,top:rect.top});
  }} onPointerLeave={hide}>
   {mode==='Source'?<pre tabIndex={0} aria-label="Document source">{text}</pre>:mode==='Diff'?review?review.previous===null?<p className="empty">The previous version will be kept after the file changes.</p>:<RenderedDocumentDiff previous={review.previous} current={text} markdown={!html}/>:<p className="empty">Loading previous version…</p>:livePreview?<iframe className="review-live-preview" title="Interactive HTML preview" src={boot.previewUrl+'?version='+encodeURIComponent(version.current)+'&renewal='+renewal+'&network='+Number(webAssets)} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer"/>:html?
    <RenderedHtmlEditor {...props} ref={htmlEditor} renderPreview={renderPreview} readOnly={!editing} onSelection={s=>selection.current=s} onParagraph={p=>point(p?{...p,target:p}:null)} findVisible={find} findQuery={search} findNextToken={next} onFindToggle={()=>setFind(v=>!v)} onFindClose={()=>setFind(false)}/>:
    <RenderedMarkdownEditor {...props} ref={mdEditor} readOnly={!editing} assets={assets}/>}
  </section>
  {comments&&<aside className="review-comments"><header><strong>Comments</strong><button aria-label="Close comments" onClick={()=>setComments(false)}>×</button></header>
   {review?.comments.map(c=><article key={c.id}>{c.quote&&<blockquote>{c.quote}</blockquote>}<p>{c.text}</p></article>)}
   <form onSubmit={e=>{e.preventDefault();void post();}}>{quote&&<blockquote>{quote}</blockquote>}
    <textarea ref={composer} aria-label="Comment" placeholder="Write a comment…" value={message} maxLength={12000} onChange={e=>setMessage(e.target.value)}/>
    <button disabled={!message.trim()||posting}>{posting?'Posting…':'Post comment'}</button>
   </form></aside>}
  </div>
  {mode==='Preview'&&paragraph&&<div className="review-paragraph" style={{left:Math.max(2,Math.min(innerWidth-(editing?64:32),paragraph.right-2)),top:Math.max(40,Math.min(innerHeight-30,paragraph.top))}} onPointerEnter={()=>clearTimeout(hideTimer.current)} onPointerLeave={hide}>
   <button aria-label="Comment on paragraph" onMouseDown={e=>e.preventDefault()} onClick={()=>comment(paragraph,false)}><MessageSquare size={15}/></button>
   {editing&&(!(paragraph.target instanceof HTMLElement)||paragraph.target.parentElement?.matches('.tiptap'))&&<button aria-label="Delete paragraph" onClick={()=>remove(paragraph)}><Trash2 size={15}/></button>}
  </div>}
 </>;
}
createRoot(document.getElementById('root')!).render(<Editor/>);
