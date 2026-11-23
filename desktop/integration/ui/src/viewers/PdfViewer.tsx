import {useEffect,useLayoutEffect,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {getDocument,GlobalWorkerOptions,TextLayer,type PDFDocumentProxy} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import 'pdfjs-dist/web/pdf_viewer.css';
GlobalWorkerOptions.workerSrc=workerUrl;
type ViewMode='continuous'|'single'|'spread';
type Size={w:number;h:number};
const MODES:[ViewMode,string][]=[['continuous','Continuous scroll'],['single','Single page'],['spread','Two pages']];
function savedViewMode():ViewMode{try{const v=localStorage.getItem('pdf-view-mode');if(MODES.some(([m])=>m===v))return v as ViewMode;}catch{}return 'continuous';}
const spreadStart=(n:number)=>n-((n-1)%2);
const typing=(el:EventTarget|null)=>el instanceof HTMLElement&&(el.isContentEditable||!!el.closest('input,textarea,select,[role=menu],[role=listbox]'));
function mark(layer:HTMLElement,find:string){const q=find.trim().toLocaleLowerCase();layer.querySelectorAll('span').forEach(span=>span.classList.toggle('fv-pdf-match',!!q&&!!span.textContent?.toLocaleLowerCase().includes(q)));}

function PdfPage({pdf,n,size,zoom,live,find,onError}:{pdf:PDFDocumentProxy;n:number;size:Size;zoom:number;live:boolean;find:string;onError:(message:string)=>void}){
 const canvas=useRef<HTMLCanvasElement>(null),text=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(!live)return;const c=canvas.current!,t=text.current!;let active=true,render:any,layer:TextLayer|undefined;
  pdf.getPage(n).then(async page=>{if(!active)return;const viewport=page.getViewport({scale:zoom}),ratio=Math.min(devicePixelRatio||1,2);c.width=Math.floor(viewport.width*ratio);c.height=Math.floor(viewport.height*ratio);t.replaceChildren();t.style.setProperty('--scale-factor',String(zoom));t.style.setProperty('--total-scale-factor',String(zoom));
   render=page.render({canvas:c,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]});await render.promise;if(!active)return;
   layer=new TextLayer({textContentSource:await page.getTextContent(),container:t,viewport});await layer.render();if(active)mark(t,find);
  }).catch(e=>{if(active&&e?.name!=='RenderingCancelledException')onError(e.message);});
  return()=>{active=false;render?.cancel();layer?.cancel();c.width=0;c.height=0;t.replaceChildren();};
 },[pdf,n,zoom,live]);
 useEffect(()=>{if(text.current)mark(text.current,find);},[find]);
 const style={width:size.w*zoom+'px',height:size.h*zoom+'px'};
 return <div className="fv-pdf-page" data-page={n} data-live={live||undefined} style={style}><canvas ref={canvas} aria-label={'PDF page '+n} style={style}/><div ref={text} className="textLayer"/></div>;
}

export function PdfViewer({url,identity,initialPage,actions}:{url:string;identity:string;initialPage?:number;actions:ReactNode}){
 const [pdf,setPdf]=useState<PDFDocumentProxy>();const [sizes,setSizes]=useState<Size[]>([]);
 const [page,setPage]=useState(()=>{try{return initialPage||Number(sessionStorage.getItem('pdf-page:'+identity))||1;}catch{return initialPage||1;}});
 const [mode,setMode]=useState<ViewMode>(savedViewMode);const [near,setNear]=useState<Set<number>>(()=>new Set());const [jump,setJump]=useState(0);
 const [scale,setScale]=useState(1);const [fit,setFit]=useState(true);const [width,setWidth]=useState(800);const [error,setError]=useState('');const [busy,setBusy]=useState(true);const [find,setFind]=useState('');const [searching,setSearching]=useState(false);const [matches,setMatches]=useState<number[]>([]);const [searched,setSearched]=useState(false);
 const stage=useRef<HTMLDivElement>(null),searchEpoch=useRef(0),pageRef=useRef(page),quietUntil=useRef(0),last=useRef({zoom:0,mode:'',jump:0});
 const count=sizes.length,step=mode==='spread'?2:1;
 useEffect(()=>{let active=true;setBusy(true);setError('');setSizes([]);const base=import.meta.env.BASE_URL+'pdfjs/';const task=getDocument({url,cMapUrl:base+'cmaps/',cMapPacked:true,standardFontDataUrl:base+'standard_fonts/',wasmUrl:base+'wasm/'});
  task.promise.then(async doc=>{const all=await Promise.all(Array.from({length:doc.numPages},(_,i)=>doc.getPage(i+1).then(p=>{const v=p.getViewport({scale:1});return {w:v.width,h:v.height};})));if(!active)return;
   const n=Math.max(1,Math.min(pageRef.current,doc.numPages));pageRef.current=n;setPage(n);setPdf(doc);setSizes(all);setBusy(false);}).catch(e=>{if(active){setError(e.message);setBusy(false);}});
  return()=>{active=false;searchEpoch.current++;task.destroy();};},[url]);
 useEffect(()=>{if(!stage.current)return;const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));observer.observe(stage.current);return()=>observer.disconnect();},[]);
 useEffect(()=>{try{sessionStorage.setItem('pdf-page:'+identity,String(page));}catch{}},[page,identity]);
 function goTo(n:number,as=mode){if(!count)return;n=Math.max(1,Math.min(count,n));if(as==='spread')n=spreadStart(n);pageRef.current=n;setPage(n);setJump(j=>j+1);}
 useEffect(()=>{if(initialPage&&count)goTo(initialPage);},[initialPage,count]);
 function chooseMode(m:ViewMode){setMode(m);try{localStorage.setItem('pdf-view-mode',m);}catch{}if(m==='spread')goTo(pageRef.current,m);}
 const start=mode==='spread'?spreadStart(page):page;
 const shown=!count?[]:mode==='continuous'?sizes.map((_,i)=>i+1):mode==='spread'?[start,start+1].filter(n=>n<=count):[page];
 const fitWidth=mode==='spread'?shown.reduce((sum,n)=>sum+sizes[n-1].w,0)+12:Math.max(1,...(mode==='continuous'?sizes:shown.map(n=>sizes[n-1])).map(s=>s.w));
 const zoom=fit?Math.max(.15,(width-32)/fitWidth):scale;
 function zoomBy(delta:number){setFit(false);setScale(Math.min(4,Math.max(.25,Math.round((zoom+delta)*4)/4)));}
 // Scroll to the current page after a jump, a zoom change or a mode change; otherwise leave the scroll position alone.
 useLayoutEffect(()=>{const s=stage.current;if(!s||!count)return;const prev=last.current;last.current={zoom,mode,jump};if(prev.zoom===zoom&&prev.mode===mode&&prev.jump===jump)return;
  if(mode!=='continuous'){s.scrollTop=0;return;}const el=s.querySelector<HTMLElement>(`[data-page="${pageRef.current}"]`);if(el){quietUntil.current=performance.now()+250;s.scrollTop=el.offsetTop-12;}},[zoom,mode,jump,count]);
 // Continuous mode renders only pages within one screen of the viewport.
 useEffect(()=>{const s=stage.current;if(mode!=='continuous'||!s||!count)return;const io=new IntersectionObserver(entries=>setNear(prev=>{const next=new Set(prev);for(const e of entries){const n=Number((e.target as HTMLElement).dataset.page);if(e.isIntersecting)next.add(n);else next.delete(n);}return next;}),{root:s,rootMargin:'100% 0px'});
  s.querySelectorAll('[data-page]').forEach(el=>io.observe(el));return()=>io.disconnect();},[mode,count,zoom]);
 function onScroll(){const s=stage.current;if(mode!=='continuous'||!s||performance.now()<quietUntil.current)return;const line=s.scrollTop+s.clientHeight*.3;let n=1;
  for(const el of s.querySelectorAll<HTMLElement>('[data-page]')){if(el.offsetTop<=line)n=Number(el.dataset.page);else break;}
  if(s.scrollTop>0&&s.scrollTop+s.clientHeight>=s.scrollHeight-2)n=count;if(n!==pageRef.current){pageRef.current=n;setPage(n);}}
 function onKey(e:KeyboardEvent){if(!count||e.altKey||e.ctrlKey||e.metaKey||typing(e.target))return;const paged=mode!=='continuous';let n:number;
  switch(e.key){case 'ArrowLeft':n=start-step;break;case 'ArrowRight':n=start+step;break;case 'Home':n=1;break;case 'End':n=count;break;
   case 'ArrowUp':case 'PageUp':if(!paged)return;n=start-step;break;case 'ArrowDown':case 'PageDown':if(!paged)return;n=start+step;break;default:return;}
  e.preventDefault();if(n>=1&&n<=count)goTo(n);}
 async function search(){if(!pdf||!find.trim())return;const epoch=++searchEpoch.current;setSearching(true);setSearched(false);const found:number[]=[];try{for(let n=1;n<=pdf.numPages;n++){if(searchEpoch.current!==epoch)return;const content=await(await pdf.getPage(n)).getTextContent();const value=content.items.map((item:any)=>item.str||'').join(' ');if(value.toLocaleLowerCase().includes(find.trim().toLocaleLowerCase()))found.push(n);}if(searchEpoch.current===epoch){setMatches(found);setSearched(true);if(found.length)goTo(found[0]);}}catch(e){setError((e as Error).message);}finally{if(searchEpoch.current===epoch)setSearching(false);}}
 const pages=pdf?shown.map(n=><PdfPage key={n} pdf={pdf} n={n} size={sizes[n-1]} zoom={zoom} live={mode!=='continuous'||near.has(n)} find={find} onError={setError}/>):null;
 return <div className="fv-special" onKeyDown={onKey}><div className="fv-toolbar" aria-label="PDF controls"><button aria-label="Previous PDF page" disabled={!count||start<=1} onClick={()=>goTo(start-step)}>‹</button><label className="fv-page"><input aria-label="PDF page" type="number" min={1} max={count||1} value={page} onChange={e=>{const n=Number(e.target.value);if(n>=1&&n<=count)goTo(n);}}/><span>/ {count||'…'}</span></label><button aria-label="Next PDF page" disabled={!count||start+step>count} onClick={()=>goTo(start+step)}>›</button><select className="fv-pdf-mode" aria-label="PDF view mode" value={mode} onChange={e=>chooseMode(e.target.value as ViewMode)}>{MODES.map(([m,label])=><option key={m} value={m}>{label}</option>)}</select><button title="Zoom out" onClick={()=>zoomBy(-.25)}>−</button><button title="Zoom in" onClick={()=>zoomBy(.25)}>+</button><button aria-pressed={fit} onClick={()=>setFit(f=>!f)}>Fit</button><form className="fv-find" onSubmit={e=>{e.preventDefault();search();}}><input type="search" aria-label="Find text in PDF" placeholder="Find in PDF" value={find} onChange={e=>{searchEpoch.current++;setSearching(false);setFind(e.target.value);setSearched(false);}}/><button disabled={searching||!pdf||!find.trim()}>{searching?'Searching…':'Find'}</button></form>{searched&&<span className="fv-status" role="status">{matches.length?`${matches.length} matching pages`:'No matches'}</span>}{matches.length>1&&<button onClick={()=>goTo(matches.find(n=>n>start+step-1)||matches[0])}>Next match</button>}<span className="fv-spacer"/>{actions}</div>{error&&<div role="alert" className="fv-error">{error}</div>}<div className={'fv-pdf-stage '+mode} ref={stage} tabIndex={0} aria-label="PDF document" aria-busy={busy} onScroll={onScroll}>{busy&&<div className="fv-loading" role="status">Rendering PDF…</div>}{mode==='spread'?<div className="fv-pdf-spread">{pages}</div>:pages}</div></div>;
}
