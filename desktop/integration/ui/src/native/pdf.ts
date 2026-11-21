import {getDocument,GlobalWorkerOptions} from 'pdfjs-dist';
import PdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?worker&inline';
import './document.css';
GlobalWorkerOptions.workerPort=new PdfWorker();
const boot=(window as any).__PDF_BOOT__;
const content=document.getElementById('document')!,status=document.getElementById('status')!;
let current=1,scale=1.4,rendering=false,pending=false;
const pdf=await getDocument({data:Uint8Array.from(atob(boot.data),c=>c.charCodeAt(0))}).promise;
async function render(){if(rendering){pending=true;return;}rendering=true;
 try{const page=await pdf.getPage(current);const viewport=page.getViewport({scale});const canvas=document.createElement('canvas');canvas.setAttribute('aria-label',`PDF page ${current}`);canvas.style.cssText='display:block;margin:20px auto;max-width:100%;height:auto;background:white';canvas.width=viewport.width;canvas.height=viewport.height;
 await page.render({canvas,viewport}).promise;const text=await page.getTextContent();const accessible=document.createElement('div');accessible.className='pdf-accessible';accessible.textContent=text.items.map(i=>'str' in i?i.str:'').join(' ');content.replaceChildren(canvas,accessible);status.textContent=`Page ${current} of ${pdf.numPages}`;
 }catch(e){status.textContent=String(e);}finally{rendering=false;if(pending){pending=false;void render();}}}
for(const [id,action] of [['previous',()=>current=Math.max(1,current-1)],['next',()=>current=Math.min(pdf.numPages,current+1)],['zoomIn',()=>scale=Math.min(4,scale+.2)],['zoomOut',()=>scale=Math.max(.5,scale-.2)]] as const)document.getElementById(id)!.onclick=()=>{action();void render();};
void render();
