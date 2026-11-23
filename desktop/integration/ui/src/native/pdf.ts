import {getDocument,GlobalWorkerOptions,type PDFPageProxy} from 'pdfjs-dist';
import PdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?worker&inline';
import './document.css';
GlobalWorkerOptions.workerPort=new PdfWorker();
type ViewMode='continuous'|'single'|'spread';
const boot=(window as any).__PDF_BOOT__;
const content=document.getElementById('document')!,status=document.getElementById('status')!;
const pdf=await getDocument({data:Uint8Array.from(atob(boot.data),c=>c.charCodeAt(0))}).promise;
const pages:PDFPageProxy[]=await Promise.all(Array.from({length:pdf.numPages},(_,i)=>pdf.getPage(i+1)));
let mode:ViewMode=(()=>{try{const v=localStorage.getItem('pdf-view-mode');if(v==='single'||v==='spread'||v==='continuous')return v;}catch{}return 'continuous';})();
let current=1,zoom=0,scale=1,quietUntil=0;// zoom 0 = fit the pane width
const step=()=>mode==='spread'?2:1,start=(n:number)=>mode==='spread'?n-((n-1)%2):n;
const select=document.createElement('select');select.id='mode';select.setAttribute('aria-label','PDF view mode');
for(const [value,label] of [['continuous','Continuous scroll'],['single','Single page'],['spread','Two pages']])select.add(new Option(label,value));
select.value=mode;document.getElementById('zoomOut')!.before(select);content.tabIndex=0;
// Each page is a fixed-size slot; its canvas is drawn only while the slot is within one screen of the viewport.
const slots=new Map<number,HTMLDivElement>();
function slot(n:number){const page=pages[n-1],viewport=page.getViewport({scale}),el=document.createElement('div');el.className='pdf-page';el.dataset.page=String(n);el.style.width=viewport.width+'px';el.style.height=viewport.height+'px';slots.set(n,el);return el;}
async function draw(el:HTMLDivElement){if(el.firstChild)return;const n=Number(el.dataset.page),page=pages[n-1],viewport=page.getViewport({scale}),ratio=Math.min(devicePixelRatio||1,2),canvas=document.createElement('canvas');
 canvas.setAttribute('aria-label',`PDF page ${n}`);canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';el.append(canvas);
 try{await page.render({canvas,viewport,transform:ratio===1?undefined:[ratio,0,0,ratio,0,0]}).promise;const text=await page.getTextContent();const accessible=document.createElement('div');accessible.className='pdf-accessible';accessible.textContent=text.items.map(i=>'str' in i?i.str:'').join(' ');el.append(accessible);}catch(e){status.textContent=String(e);}}
const io=new IntersectionObserver(entries=>{for(const e of entries){const el=e.target as HTMLDivElement;if(e.isIntersecting)void draw(el);else el.replaceChildren();}},{root:content,rootMargin:'100% 0px'});
function layout(){io.disconnect();slots.clear();const shown=mode==='continuous'?pages.map((_,i)=>i+1):mode==='spread'?[start(current),start(current)+1].filter(n=>n<=pages.length):[current];
 const widths=shown.map(n=>pages[n-1].getViewport({scale:1}).width);scale=zoom||Math.max(.15,(content.clientWidth-32-(mode==='spread'?36:0))/(mode==='spread'?widths.reduce((a,b)=>a+b,0):Math.max(...widths)));
 const wrap=document.createElement('div');wrap.className='pdf-pages '+mode;wrap.append(...shown.map(slot));content.replaceChildren(wrap);
 if(mode==='continuous'){slots.forEach(el=>io.observe(el));scrollToCurrent();}else{content.scrollTop=0;slots.forEach(el=>void draw(el));}showStatus();}
function scrollToCurrent(){const el=slots.get(current);if(el){quietUntil=performance.now()+250;content.scrollTop=el.offsetTop-content.offsetTop-12;}}
function showStatus(){const s=start(current),last=mode==='spread'?Math.min(s+1,pages.length):s;status.textContent=last>s?`Pages ${s}-${last} of ${pages.length}`:`Page ${s} of ${pages.length}`;}
function goTo(n:number){n=start(Math.max(1,Math.min(pages.length,n)));current=n;if(mode==='continuous'){scrollToCurrent();showStatus();}else layout();}
content.addEventListener('scroll',()=>{if(mode!=='continuous'||performance.now()<quietUntil)return;const line=content.scrollTop+content.clientHeight*.3+content.offsetTop;let n=1;
 for(const [k,el] of slots){if(el.offsetTop<=line)n=k;else break;}if(content.scrollTop>0&&content.scrollTop+content.clientHeight>=content.scrollHeight-2)n=pages.length;if(n!==current){current=n;showStatus();}});
document.addEventListener('keydown',e=>{const t=e.target as HTMLElement;if(e.altKey||e.ctrlKey||e.metaKey||t.closest?.('input,textarea,select'))return;const paged=mode!=='continuous',s=start(current);let n:number;
 switch(e.key){case 'ArrowLeft':n=s-step();break;case 'ArrowRight':n=s+step();break;case 'Home':n=1;break;case 'End':n=pages.length;break;
  case 'ArrowUp':case 'PageUp':if(!paged)return;n=s-step();break;case 'ArrowDown':case 'PageDown':if(!paged)return;n=s+step();break;default:return;}
 e.preventDefault();if(n>=1&&n<=pages.length)goTo(n);});
select.onchange=()=>{mode=select.value as ViewMode;try{localStorage.setItem('pdf-view-mode',mode);}catch{}current=start(current);layout();};
for(const [id,action] of [['previous',()=>goTo(start(current)-step())],['next',()=>goTo(start(current)+step())],['zoomIn',()=>{zoom=Math.min(4,scale+.2);layout();}],['zoomOut',()=>{zoom=Math.max(.25,scale-.2);layout();}]] as const)document.getElementById(id)!.onclick=action;
new ResizeObserver(()=>{if(!zoom)layout();}).observe(content);
layout();
