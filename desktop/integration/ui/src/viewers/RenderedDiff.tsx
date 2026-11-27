import {useMemo,useRef,useState} from 'react';
import DOMPurify from 'dompurify';
import {LcsDiff} from 'monaco-editor/base/common/diff/diff.js';
import MarkdownIt from 'markdown-it';
const markdownRenderer=new MarkdownIt();



const blockNames=new Set(['ARTICLE','BLOCKQUOTE','DIV','H1','H2','H3','H4','H5','H6','LI','P','PRE','SECTION','TABLE','TR']);
function renderedReview(source:string,markdown:boolean){
 const html=markdown?markdownRenderer.render(source):source;
 const fragment=DOMPurify.sanitize(html,{RETURN_DOM_FRAGMENT:true,FORBID_TAGS:['head','title','script','style','template','noscript','form','input','iframe','img','svg'],ALLOWED_ATTR:[]});
 function text(node:Node):string{
  if(node.nodeType===Node.TEXT_NODE)return node.textContent||'';
  const children=[...node.childNodes].map(text).join('');
  if(node.nodeName==='BR')return '\n';
  if(node.nodeName==='TD'||node.nodeName==='TH')return children+' ';
  return blockNames.has(node.nodeName)?children.trim()+'\n\n':children;
 }
 const surface=document.createElement('div');surface.append(fragment);
 return {text:text(surface).replace(/\n{3,}/g,'\n\n').trim(),html:surface.innerHTML};
}

export function RenderedDocumentDiff({previous,current,markdown}:{previous:string,current:string,markdown:boolean}){
 const comparison=useMemo(()=>{
  const old=renderedReview(previous,markdown),next=renderedReview(current,markdown);
  const words=(value:string)=>value.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu)||[];
  const before=words(old.text),after=words(next.text),deadline=performance.now()+180;
  const diff=new LcsDiff({getElements:()=>before},{getElements:()=>after},()=>performance.now()<deadline).ComputeDiff(true);
  let index=0;
  const runs=diff.changes.map((change:any)=>{
   const unchanged=before.slice(index,change.originalStart).join('');
   index=change.originalStart+change.originalLength;
   return {unchanged,removed:before.slice(change.originalStart,index).join(''),inserted:after.slice(change.modifiedStart,change.modifiedStart+change.modifiedLength).join('')};
  });
  return {old,next,runs,tail:before.slice(index).join('')};
 },[previous,current,markdown]);
 if(previous===current)return <p className="fv-empty">No changes since the previous version.</p>;
 const {old,next,runs,tail}=comparison;
 return <article className="document-diff" aria-label="Rendered document changes">
  {old.text===next.text?<div className="document-format-diff">
   <section><h3>Previous</h3><div dangerouslySetInnerHTML={{__html:old.html}}/></section>
   <section><h3>Current</h3><div dangerouslySetInnerHTML={{__html:next.html}}/></section>
  </div>:<div className="document-diff-text">{runs.map(({unchanged,removed,inserted},i)=><span key={i}>{unchanged}{removed&&<del>{removed}</del>}{inserted&&<ins>{inserted}</ins>}</span>)}{tail}</div>}
 </article>;
}

