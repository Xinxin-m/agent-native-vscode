import {Editor} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {Markdown} from '@tiptap/markdown';
import {TableKit} from '@tiptap/extension-table';
import Image from '@tiptap/extension-image';
import {documentImageSource} from './documentImageSource';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import {InlineMathWithParens,BlockMathWithBrackets} from './mathExtensions';
import 'katex/dist/katex.min.css';
import './document.css';
import {PINCH_JS} from './pinch';
declare const acquireVsCodeApi:any;
const host=acquireVsCodeApi(),boot=(window as any).__DOCUMENT_BOOT__;
let version=boot.version,editor:Editor,loading=false,inFlight=false,queued:any=null,latestDraft:any=null,saveRequested=false,frontmatter="",markdownBase="";
const status=document.getElementById('status')!,content=document.getElementById('document')!;
const toolbar=document.getElementById('toolbar')!;
function send(change:any){latestDraft=change;host.setState({draft:change,baseVersion:version});if(inFlight){queued=change;return;}inFlight=true;status.textContent='Saving changes…';host.postMessage({type:'edit',version,change});}
window.addEventListener('message',e=>{if(e.origin!==window.location.origin)return;const m=e.data;if(m?.type==='saved'){version=m.version;inFlight=false;status.textContent='Unsaved changes';if(queued){const next=queued;queued=null;send(next);}else {host.setState({});if(saveRequested){saveRequested=false;host.postMessage({type:'save'});}}}else if(m?.type==='diskSaved'){status.textContent='Saved';}else if(m?.type==='error'){inFlight=false;status.textContent=m.message;document.getElementById('recover')!.hidden=false;}else if(m?.type==='document'){boot.assets=m.assets||{};if(inFlight||queued){status.textContent='The document changed elsewhere. Your draft is retained.';document.getElementById('recover')!.hidden=false;return;}version=m.version;loading=true;if(editor){frontmatter=extractFrontmatter(m.text);editor.commands.setContent(m.text.slice(frontmatter.length),{contentType:'markdown',emitUpdate:false});markdownBase=frontmatter+editor.getMarkdown();}else renderHtml(m);loading=false;}});
function save(){queueMicrotask(()=>{if(inFlight||queued)saveRequested=true;else host.postMessage({type:'save'});});}
document.getElementById('save')!.onclick=save;
document.getElementById('recover')!.onclick=()=>host.postMessage({type:'recover',draft:latestDraft});
window.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();}});
function extractFrontmatter(text:string){return text.match(/^(?:\uFEFF)?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/)?.[0]||'';}
let disposeFrame:(()=>void)|undefined;
if(boot.format==='markdown'){
 frontmatter=extractFrontmatter(boot.text);
 editor=new Editor({element:content,editable:!boot.readOnly,extensions:[StarterKit,Markdown,TableKit,Image.extend({renderHTML({HTMLAttributes}){return ['img',{...HTMLAttributes,src:documentImageSource(HTMLAttributes.src,boot.assets)}]}}),TaskList,TaskItem.configure({nested:true}),InlineMathWithParens,BlockMathWithBrackets],content:boot.text.slice(frontmatter.length),contentType:'markdown',editorProps:{attributes:{'aria-label':'Editable rendered Markdown',spellcheck:'true'}},onUpdate:()=>{if(!loading)send({text:frontmatter+editor.getMarkdown(),baseText:markdownBase})}});
 markdownBase=frontmatter+editor.getMarkdown();
 for(const [label,run] of [['Bold',()=>editor.chain().focus().toggleBold().run()],['Italic',()=>editor.chain().focus().toggleItalic().run()],['Heading',()=>editor.chain().focus().toggleHeading({level:2}).run()],['Bullet list',()=>editor.chain().focus().toggleBulletList().run()],['Undo',()=>editor.chain().focus().undo().run()],['Redo',()=>editor.chain().focus().redo().run()]] as const){const b=document.createElement('button');b.textContent=label;b.onclick=run;toolbar.prepend(b);}
}else renderHtml(boot);
function renderHtml(snapshot:any){
 disposeFrame?.();content.replaceChildren();
 const frame=document.createElement('iframe');frame.sandbox.add('allow-scripts');frame.title='Editable rendered HTML';frame.referrerPolicy='no-referrer';content.append(frame);
 const nonce=crypto.randomUUID(),payload=JSON.stringify({nodes:snapshot.model.nodes.map((n:any)=>({id:n.id,path:n.path,text:n.text})),nonce,readOnly:boot.readOnly}).replace(/</g,'\\u003c');
 const script=`(()=>{const config=${payload};const changed=new Map();for(const item of config.nodes){let n=document;for(const index of item.path)n=n?.childNodes[index];if(!n||n.nodeType!==3||n.nodeValue!==item.text)continue;const span=document.createElement('span');span.textContent=n.nodeValue;span.contentEditable=config.readOnly?'false':'plaintext-only';span.dataset.libraryEditable=item.id;span.style.whiteSpace='pre-wrap';span.setAttribute('aria-label','Edit text');n.replaceWith(span);span.addEventListener('input',()=>{changed.set(item.id,span.textContent);parent.postMessage({type:'html-edit',nonce:config.nonce,changes:[...changed].map(([id,text])=>({id,text}))},'*')});}document.addEventListener('click',e=>{const a=e.target.closest('a');if(a&&e.target.isContentEditable)e.preventDefault()});})()`;
 // No privileged host API exists in this isolated frame. Only validated text IDs leave it.
 frame.srcdoc=snapshot.preview+`<script nonce="${snapshot.nonce}">${script}<\/script><script nonce="${snapshot.nonce}">${PINCH_JS}<\/script>`;
 const listener=(e:MessageEvent)=>{if(e.source===frame.contentWindow&&e.data?.type==='html-edit'&&e.data.nonce===nonce)send({changes:e.data.changes,baseHash:snapshot.model.hash});};window.addEventListener('message',listener);disposeFrame=()=>window.removeEventListener('message',listener);
}
const recovered=host.getState()?.draft;if(recovered){latestDraft=recovered;document.getElementById('recover')!.hidden=false;status.textContent='An unsaved draft is available.';}

if(boot.readOnly){status.textContent='Read-only · Remote workspace';for(const button of toolbar.querySelectorAll('button'))button.disabled=true;}
