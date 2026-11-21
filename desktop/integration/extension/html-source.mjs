import {marked} from '../ui/node_modules/marked/lib/marked.esm.js';
import {parse,serialize,parseFragment} from '../ui/node_modules/parse5/dist/index.js';
import {createHash} from 'node:crypto';
const digest=text=>createHash('sha256').update(text).digest('hex');
const excluded=new Set(['head','script','style','template','noscript','textarea','svg','math']);
export function inspectHtml(source){const root=parse(source,{sourceCodeLocationInfo:true}),nodes=[];
 const walk=(node,path=[],blocked=false)=>{blocked=blocked||excluded.has(node.tagName);if(node.nodeName==='#text'&&!blocked&&node.value.trim()&&node.sourceCodeLocation){const l=node.sourceCodeLocation;nodes.push({id:'text-'+nodes.length,path,text:node.value,start:l.startOffset,end:l.endOffset});}for(const [i,child] of (node.childNodes||[]).entries())walk(child,[...path,i],blocked);};walk(root);return {hash:digest(source),nodes};}
export function editHtml(source,model,changes){if(digest(source)!==model.hash)throw Error('Document changed since this preview. Your edits are retained.');const byId=new Map(model.nodes.map(n=>[n.id,n]));const seen=new Set();const edits=changes.map(c=>{if(!byId.has(c.id)||seen.has(c.id))throw Error('Unknown or repeated editable text');if(typeof c.text!=='string'||c.text.length>2000000)throw Error('Invalid replacement text');seen.add(c.id);return {...byId.get(c.id),text:c.text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')};}).sort((a,b)=>b.start-a.start);for(const e of edits)source=source.slice(0,e.start)+e.text+source.slice(e.end);return source;}

// Keep authored scripts inert in the editable snapshot; only the appended bridge gets a nonce.
// Preserve element/text slots so body paths and original on-disk bytes remain unchanged.
export function previewHtml(source,nonce){
 if(!/^[A-Za-z0-9+/=]+$/.test(nonce))throw Error('Invalid preview nonce');
 const root=parse(source);const walk=node=>{if(node.tagName==='script'){node.attrs=node.attrs.filter(a=>!['src','href','nonce','type','language'].includes(a.name));node.attrs.push({name:'type',value:'application/x-library-inert'});}if(node.tagName==='head'){const policy=parseFragment(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; script-src-elem 'nonce-${nonce}'; script-src-attr 'none'; style-src 'unsafe-inline' data:; img-src data:; font-src data:; connect-src 'none'; form-action 'none'">`).childNodes[0];policy.parentNode=node;node.childNodes.unshift(policy);}for(const child of node.childNodes||[])walk(child);};walk(root);return serialize(root);
}

/** Discover Markdown image destinations, including reference definitions. */
export function markdownImageSources(source){
 const found=new Set();
 marked.walkTokens(marked.lexer(source),token=>{if(token.type==='image'&&typeof token.href==='string')found.add(token.href);});
 return [...found];
}
/** Rewrite presentation bytes only. Body node positions stay unchanged. */
export async function resolveHtmlAssets(source,loader){
 const root=parse(source),attr=(node,name)=>node.attrs?.find(a=>a.name===name),remove=(node,...names)=>{node.attrs=node.attrs.filter(a=>!names.includes(a.name));};
 const walk=async node=>{
  if(node.tagName){
   if(node.tagName==='script')remove(node,'src');
   if(node.tagName==='base')remove(node,'href','target');
   if(node.tagName==='img'||node.tagName==='source'){
    remove(node,'srcset');const src=attr(node,'src');if(src&&!/^data:image\/(?:png|jpeg|gif|webp|avif|svg\+xml|bmp|x-icon)[;,]/i.test(src.value)){const image=await loader.image(src.value);if(image)src.value=image;else remove(node,'src');}
   }
   if(node.tagName==='link'){
    const href=attr(node,'href'),rel=attr(node,'rel')?.value.toLowerCase();
    if(href&&rel==='stylesheet'){const css=await loader.stylesheet(href.value);if(css!==null)href.value='data:text/css;base64,'+Buffer.from(css).toString('base64');else remove(node,'href');remove(node,'integrity','crossorigin');}
    else if(href)remove(node,'href');
   }
   const style=attr(node,'style');if(style)style.value=await loader.css(style.value);
   if(node.tagName==='style')for(const child of node.childNodes||[])if(child.nodeName==='#text')child.value=await loader.css(child.value);
  }
  for(const child of node.childNodes||[])await walk(child);
 };
 await walk(root);return serialize(root);
}
