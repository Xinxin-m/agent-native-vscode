import {parse,parseFragment,serialize} from 'parse5';

type Editable={id:string;text:string;start:number;end:number};
export type HtmlModel={source:string;nodes:Editable[]};
export type HtmlChange={id:string;original:string;text:string};
const excluded=new Set(['head','script','style','template','noscript','textarea','svg','math','iframe','object','embed','applet','frame','frameset','portal']);
export function inspectHtml(source:string):HtmlModel{
 const root:any=parse(source,{sourceCodeLocationInfo:true}),nodes:Editable[]=[];
 const walk=(node:any,blocked=false)=>{blocked=blocked||excluded.has(node.tagName);if(node.nodeName==='#text'&&!blocked&&node.value.trim()&&node.sourceCodeLocation){const l=node.sourceCodeLocation;nodes.push({id:'text-'+nodes.length,text:node.value,start:l.startOffset,end:l.endOffset})}for(const child of node.childNodes||[])walk(child,blocked)};walk(root);return {source,nodes};
}
export function editHtml(source:string,model:HtmlModel,changes:HtmlChange[]){
 if(source!==model.source)throw Error('Document changed since this preview. Your edits are retained.');
 const seen=new Set<string>(),byId=new Map(model.nodes.map(n=>[n.id,n]));
 const edits=changes.map(c=>{const node=byId.get(c.id);if(!node||seen.has(c.id)||c.original!==node.text||typeof c.text!=='string'||c.text.length>2000000)throw Error('Invalid or stale rendered HTML edit.');seen.add(c.id);return {...node,replacement:c.text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}}).filter(e=>e.replacement!==e.text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')).sort((a,b)=>b.start-a.start);
 for(const e of edits)source=source.slice(0,e.start)+e.replacement+source.slice(e.end);return source;
}
// The loader may return only a trusted data URL generated from a scoped local file.
export async function previewHtml(source:string,model:HtmlModel,nonce:string,loader:((url:string,kind:'image'|'style')=>Promise<string|null>) & {css?:(value:string)=>Promise<string>},readOnly=false){
 if(source!==model.source||!/^[A-Za-z0-9+/=]+$/.test(nonce))throw Error('Invalid preview revision.');
 const root:any=parse(source,{sourceCodeLocationInfo:true}),byStart=new Map(model.nodes.map(n=>[n.start,n]));
 const drop=new Set(['script','iframe','object','embed','applet','frame','frameset','portal','base']);
 const walk=async(node:any)=>{if(!node.childNodes)return;const next:any[]=[];for(const child of node.childNodes){
  if(drop.has(child.tagName)||child.tagName==='meta'&&child.attrs?.some((a:any)=>a.name==='http-equiv'))continue;
  const item=child.nodeName==='#text'&&child.sourceCodeLocation&&byStart.get(child.sourceCodeLocation.startOffset);
  if(item){const span:any=parseFragment('<span></span>').childNodes[0];span.attrs=[{name:'data-library-text',value:item.id},{name:'contenteditable',value:readOnly?'false':'true'},{name:'spellcheck',value:'true'}];span.parentNode=node;child.parentNode=span;span.childNodes=[child];next.push(span);continue}
  if(child.tagName){child.attrs=child.attrs.filter((a:any)=>!/^on/i.test(a.name)&&!['srcdoc','nonce','integrity','crossorigin','formaction','action','target','srcset','data-library-text','contenteditable'].includes(a.name));
   for(const a of child.attrs){if(['href','src','xlink:href'].includes(a.name)&&/^\s*(?:javascript|vbscript|data):/i.test(a.value)&&!(a.name==='src'&&['img','source'].includes(child.tagName)&&/^data:image\/(?:png|jpeg|gif|webp|avif|bmp|x-icon);base64,[a-z0-9+/=]+$/i.test(a.value)))a.value='';}
   if(child.tagName==='img'||child.tagName==='source'){const a=child.attrs.find((x:any)=>x.name==='src');if(a)a.value=await loader(a.value,'image')||'';}
   const style=child.attrs.find((x:any)=>x.name==='style');if(style)style.value=loader.css?await loader.css(style.value):style.value.replace(/url\([^)]*\)/gi,'url("")');
   if(child.tagName==='style')for(const entry of child.childNodes||[])if(entry.nodeName==='#text')entry.value=loader.css?await loader.css(entry.value):entry.value.replace(/url\([^)]*\)/gi,'url("")').replace(/@import[^;]*;/gi,'');
   if(child.tagName==='link'){const rel=child.attrs.find((x:any)=>x.name==='rel')?.value.toLowerCase(),href=child.attrs.find((x:any)=>x.name==='href');if(href)href.value=rel==='stylesheet'?await loader(href.value,'style')||'':'';}
  }
  await walk(child);next.push(child);
 }node.childNodes=next};await walk(root);
 const head=root.childNodes.find((n:any)=>n.tagName==='html')?.childNodes.find((n:any)=>n.tagName==='head');
 const policy=`default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline' data:; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'`;
 const meta:any=parseFragment('<meta http-equiv="Content-Security-Policy">').childNodes[0];meta.attrs.push({name:'content',value:policy});meta.parentNode=head;head.childNodes.unshift(meta);
 const bridge=`(()=>{const token=${JSON.stringify(nonce)}, changes=new Map();let findVisible=false,findQuery='',findNext=-1,findIndex=0;const report=(type,extra={})=>parent.postMessage({type,token,...extra},'*');const find=(data)=>{const previousNext=findNext;const query=typeof data.query==='string'?data.query.slice(0,2000):'',next=Number.isSafeInteger(data.nextToken)?data.nextToken:0,changed=query!==findQuery;findVisible=!!data.visible;if(changed)findIndex=0;else if(findNext!==-1&&findNext!==next)findIndex++;findQuery=query;findNext=next;const ranges=[];if(findVisible&&query.trim()){const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(walk.nextNode()&&ranges.length<5000){const n=walk.currentNode;if(n.parentElement?.closest('script,style,template,noscript'))continue;const text=n.data.toLocaleLowerCase(),q=query.toLocaleLowerCase();let at=text.indexOf(q);while(at>=0&&ranges.length<5000){const r=document.createRange();r.setStart(n,at);r.setEnd(n,at+q.length);ranges.push(r);at=text.indexOf(q,at+q.length)}}}if(globalThis.CSS?.highlights){CSS.highlights.delete('library-find');if(ranges.length&&globalThis.Highlight)CSS.highlights.set('library-find',new Highlight(...ranges))}findIndex=ranges.length?findIndex%ranges.length:0;if(ranges.length&&(changed||next!==previousNext)){const selection=window.getSelection();selection.removeAllRanges();selection.addRange(ranges[findIndex]);ranges[findIndex].startContainer.parentElement?.scrollIntoView({block:'nearest'})}report('library-html-find-count',{count:ranges.length,index:ranges.length?findIndex+1:0})};window.addEventListener('message',e=>{if(e.source===parent&&e.data?.type==='library-html-find'&&e.data.token===token)find(e.data)});document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&!e.altKey&&e.key.toLowerCase()==='f'){e.preventDefault();report('library-html-find-toggle')}else if(e.key==='Escape'&&findVisible){e.preventDefault();report('library-html-find-close')}},true);window.addEventListener('message',e=>{if(e.source===parent&&e.data?.type==='library-html-disable'&&e.data.token===token)document.querySelectorAll('[data-library-text]').forEach(el=>el.contentEditable='false')});document.addEventListener('input',e=>{const el=e.target.closest('[data-library-text]');if(!el)return;changes.set(el.dataset.libraryText,el.textContent);if(findVisible)find({visible:true,query:findQuery,nextToken:findNext});parent.postMessage({type:'library-html-edit',token,changes:Array.from(changes,([id,text])=>({id,text}))},'*')});document.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;e.preventDefault();parent.postMessage({type:'library-html-link',token,href:a.getAttribute('href')||'',external:e.metaKey||e.ctrlKey},'*')},true);document.addEventListener('submit',e=>e.preventDefault(),true)})();`;
 const script:any=parseFragment('<script></script>').childNodes[0];script.attrs=[{name:'nonce',value:nonce}];script.childNodes=[{nodeName:'#text',value:bridge,parentNode:script}];script.parentNode=head;head.childNodes.push(script);return serialize(root);
}

export function acceptHtmlMessage(event:{source:unknown;data:any},frame:unknown,token:string){return !!frame&&event.source===frame&&!!event.data&&event.data.token===token}

export function htmlSnapshotForFile<T extends {key:string}>(snapshot:T|null,key:string):T|null{return snapshot?.key===key?snapshot:null}
