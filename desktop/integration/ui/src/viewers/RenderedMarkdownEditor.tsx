import {useEffect,useImperativeHandle,useRef,useState,type Ref} from 'react';
import {Editor,Extension} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {Markdown} from '@tiptap/markdown';
import {TableKit} from '@tiptap/extension-table';
import Image from '@tiptap/extension-image';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import {InlineMathWithParens,BlockMathWithBrackets} from '../native/mathExtensions';
import {closeHistory} from '@tiptap/pm/history';
import {editMarkdown,frontmatter,imageUrl,renderedRecoveryKey} from './markdownEdit';
import {parseRenderedLink} from '../open/links';
import {previewLocation} from './helpers';
import type {Resource} from '../state/types';
import {rememberRenderedRecovery,releaseRenderedMemory,recoveryStorage} from './renderedRecovery';
import type {FileRecord} from './helpers';
import 'katex/dist/katex.min.css';
const documentShortcuts=Extension.create({
 name:'documentShortcuts',
 priority:1000,
 addKeyboardShortcuts(){
  return Object.fromEntries(['Ctrl','Meta'].flatMap(modifier=>[
   [modifier+'-z',()=>this.editor.commands.undo()],
   [modifier+'-Shift-z',()=>this.editor.commands.redo()],
   [modifier+'-y',()=>this.editor.commands.redo()],
   [modifier+'-b',()=>this.editor.commands.toggleBold()],
   [modifier+'-i',()=>this.editor.commands.toggleItalic()],
   [modifier+'-u',()=>this.editor.commands.toggleUnderline()],
   [modifier+'-Shift-x',()=>this.editor.commands.toggleStrike()],
   [modifier+'-Shift-s',()=>this.editor.commands.toggleStrike()]
  ]));
 }
});
export type MarkdownParagraphEditor={deleteParagraph(element:HTMLElement):void};
export function RenderedMarkdownEditor({text,file,onChange,onBlocked,onRecovery,onOpen,ref,readOnly=false,assets={}}:{text:string;file:FileRecord;onChange:(text:string)=>void;onBlocked:(blocked:boolean)=>void;onRecovery:()=>void;onOpen:(resource:Resource,external:boolean)=>void;ref?:Ref<MarkdownParagraphEditor>;readOnly?:boolean;assets?:Record<string,string>}){
 const element=useRef<HTMLDivElement>(null),editor=useRef<Editor|null>(null),source=useRef(text),baseline=useRef(''),prefix=useRef(''),blocked=useRef(false),changeRef=useRef(onChange),blockRef=useRef(onBlocked),recoveryRef=useRef(onRecovery),openRef=useRef(onOpen),draft=useRef(''),recoveryOwner=useRef(crypto.randomUUID());
 const [error,setError]=useState('');changeRef.current=onChange;blockRef.current=onBlocked;recoveryRef.current=onRecovery;openRef.current=onOpen;
 const recoveryKey=renderedRecoveryKey(file.host_id,file.path);
 useImperativeHandle(ref,()=>({
  deleteParagraph(paragraph){
   const instance=editor.current;
   if(!instance||blocked.current||paragraph.parentElement!==instance.view.dom)return;
   const position=instance.state.doc.resolve(instance.view.posAtDOM(paragraph,0));
   if(position.parent.type.name!=='paragraph'||position.depth!==1)return;
   instance.view.dispatch(closeHistory(instance.state.tr).delete(position.before(),position.after()));
   instance.commands.focus();
  },
 }),[]);
 useEffect(()=>{if(!element.current)return;blocked.current=false;draft.current='';recoveryOwner.current=crypto.randomUUID();setError('');prefix.current=frontmatter(text);source.current=text;
 const image=Image.extend({renderHTML({HTMLAttributes}){const src=assets[String(HTMLAttributes.src||'')]||'';return ['img',{...HTMLAttributes,src,referrerpolicy:'no-referrer'}]}});
 const instance=new Editor({element:element.current,editable:!readOnly,extensions:[documentShortcuts,StarterKit.configure({link:{openOnClick:false}}),Markdown,TableKit,image,TaskList,TaskItem.configure({nested:true}),InlineMathWithParens,BlockMathWithBrackets],content:text.slice(prefix.current.length),contentType:'markdown',editorProps:{attributes:{'aria-label':'Edit rendered Markdown',spellcheck:'true'},handleClick:(_view,_pos,event)=>{const anchor=(event.target as HTMLElement)?.closest('a');if(!anchor)return false;const href=anchor.getAttribute('href')||'';if(href.startsWith('#')){event.preventDefault();const anchorId=decodeURIComponent(href.slice(1));element.current?.querySelector('#'+CSS.escape(anchorId))?.scrollIntoView({block:'start'});return true}const resource=parseRenderedLink(href,previewLocation(file.path).artifact_directory,file.host_id);if(!resource)return false;event.preventDefault();openRef.current(resource,event.metaKey||event.ctrlKey);return true}},onUpdate:({editor})=>{
  const next=prefix.current+editor.getMarkdown();draft.current=next;
  try{const original=editMarkdown(source.current,baseline.current,next);source.current=original;baseline.current=next;blocked.current=false;setError('');blockRef.current(false);releaseRenderedMemory(recoveryKey,recoveryOwner.current);recoveryRef.current();changeRef.current(original)}catch(e){blocked.current=true;blockRef.current(true);const saved=rememberRenderedRecovery(recoveryKey,recoveryOwner.current,next,recoveryStorage());recoveryRef.current();const suffix=saved.persisted?'':' This draft is held in this window only. Copy or download it before closing the window.';setError((e as Error).message+suffix)}
 }});editor.current=instance;baseline.current=prefix.current+instance.getMarkdown();return()=>{instance.destroy();editor.current=null;blockRef.current(false)};
 },[file.host_id,file.path,file.root_id,file.relative_path]);
 useEffect(()=>{editor.current?.setEditable(!readOnly,false)},[readOnly]);
 useEffect(()=>{const instance=editor.current;if(!instance||text===source.current)return;if(blocked.current){setError('The source changed while a rendered edit needs recovery. Copy your rendered draft before leaving.');return}source.current=text;prefix.current=frontmatter(text);instance.commands.setContent(text.slice(prefix.current.length),{contentType:'markdown',emitUpdate:false});baseline.current=prefix.current+instance.getMarkdown()},[text]);
 return <div className="fv-rendered-edit"><div hidden={readOnly} role="toolbar" aria-label="Markdown formatting">{[['Bold',()=>editor.current?.chain().focus().toggleBold().run()],['Italic',()=>editor.current?.chain().focus().toggleItalic().run()],['Underline',()=>editor.current?.chain().focus().toggleUnderline().run()],['Strikethrough',()=>editor.current?.chain().focus().toggleStrike().run()],['Heading',()=>editor.current?.chain().focus().toggleHeading({level:2}).run()],['Bullet list',()=>editor.current?.chain().focus().toggleBulletList().run()],['Undo',()=>editor.current?.chain().focus().undo().run()],['Redo',()=>editor.current?.chain().focus().redo().run()]].map(([label,action])=><button type="button" key={String(label)} onClick={action as ()=>void}>{String(label)}</button>)}</div>{error&&<p role="alert">{error} <button type="button" onClick={()=>navigator.clipboard.writeText(draft.current)}>Copy rendered draft</button></p>}<div ref={element}/></div>;
}
