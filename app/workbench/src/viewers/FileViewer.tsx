import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronLeft,ChevronRight,MessageSquare,Pencil,Trash2} from 'lucide-react';
import type {Resource} from '../state/types';
import {useWorkbench} from '../state/context';
import {resourceId} from '../open/links';
import type {FileSequence} from '../open/chatFileLinks';
import {Markdown} from '../chat/Markdown';
import {findRanges,renderedRecoveryKey,renderedEditorFind} from './markdownEdit';
import {listRenderedRecoveries,discardRenderedRecovery,recoveryStorage,type RenderedRecovery} from './renderedRecovery';
import {contentUrl,viewerKind,previewLocation,fileViewIdentity} from './helpers';
import {snapshot,changeText,changeRenderedText,scheduleDocumentSave,documentKey,loadDocument,reloadConflict,saveCopy,saveDocument,useFileDocument,loadGitDiff,watchDocument} from './documents';
import {HtmlPreview} from './HtmlPreview';
import {ImageViewer} from './ImageViewer';
import './viewers.css';
import {DocumentComments,loadDocumentReview,RenderedDocumentDiff,type Review} from './DocumentReview';
import {RecoveryPanel} from './RecoveryPanel';
import {reloadOnStaleChunk} from '../ui/chunkReload';
import type {HtmlParagraph,HtmlParagraphEditor} from './RenderedHtmlEditor';
import type {MarkdownParagraphEditor} from './RenderedMarkdownEditor';
const SourceEditor=lazy(()=>reloadOnStaleChunk(()=>import('./SourceEditor')).then(m=>({default:m.SourceEditor})));
const RenderedMarkdownEditor=lazy(()=>reloadOnStaleChunk(()=>import('./RenderedMarkdownEditor')).then(m=>({default:m.RenderedMarkdownEditor})));
const RenderedHtmlEditor=lazy(()=>reloadOnStaleChunk(()=>import('./RenderedHtmlEditor')).then(m=>({default:m.RenderedHtmlEditor})));
const PdfViewer=lazy(()=>reloadOnStaleChunk(()=>import('./PdfViewer')).then(m=>({default:m.PdfViewer})));
type Mode='render'|'source'|'split'|'diff';
type ParagraphTarget={right:number;top:number;quote:string;target:HtmlParagraph|HTMLElement;deletable:boolean};
function savedMode(resource:Resource):Mode{try{const value=resource.mode||sessionStorage.getItem('file-mode:'+fileViewIdentity(resource));if(['render','source','split','diff'].includes(value))return value as Mode;}catch{}return 'render';}
export function FileViewer({resource,onNavigate}:{resource:Resource;onNavigate?:(target:Resource)=>void}){
 const wb=useWorkbench();
 const blocked=useRef(false);
 const sequence=resource.meta?.fileSequence as FileSequence|undefined;
 const viewer=<FileViewerContent key={resourceId(resource)} resource={resource} blocked={blocked}/>;
 if(!onNavigate||!sequence||sequence.files.length<2)return viewer;
 function move(direction:number){
  const index=sequence!.index+direction,target=sequence!.files[index];
  if(!target)return;
  const document=snapshot(documentKey(resource));
  if(blocked.current||document.dirty||document.persistenceError){
   wb.notify('Save or recover the current file before switching.');
   return;
  }
  onNavigate!({...target,meta:{...target.meta,backTo:resource.meta.backTo,fileSequence:{...sequence,index}}});
 }
 return <div className="linked-file-view" onKeyDownCapture={e=>{
  if(e.altKey||e.metaKey||e.ctrlKey||e.shiftKey||e.nativeEvent.isComposing||!['ArrowLeft','ArrowRight'].includes(e.key))return;
  if((e.target as Element).closest('input,textarea,select,[contenteditable],.monaco-editor,[role=separator]'))return;
  e.preventDefault();e.stopPropagation();
  move(e.key==='ArrowLeft'?-1:1);
 }}>
  <div className="linked-file-nav" role="group" aria-label="Files linked in this chat" tabIndex={0}>
   <button type="button" aria-label="Previous linked file" title="Previous linked file (←)" aria-disabled={sequence.index===0} onClick={()=>move(-1)}><ChevronLeft size={15}/></button>
   <span aria-live="polite">{sequence.index+1} / {sequence.files.length}</span>
   <button type="button" aria-label="Next linked file" title="Next linked file (→)" aria-disabled={sequence.index===sequence.files.length-1} onClick={()=>move(1)}><ChevronRight size={15}/></button>
  </div>
  {viewer}
 </div>;
}
function FileViewerContent({resource,blocked}:{resource:Resource;blocked:React.RefObject<boolean>}){
 const [editingMarkdown,setEditingMarkdown]=useState(false),[markdownBlocked,setMarkdownBlocked]=useState(false),[editingHtml,setEditingHtml]=useState(false),[htmlBlocked,setHtmlBlocked]=useState(false),[htmlFindNext,setHtmlFindNext]=useState(0),[renderedRecovery,setRenderedRecovery]=useState<RenderedRecovery[]>([]);
 useEffect(()=>{blocked.current=markdownBlocked||htmlBlocked;return()=>{blocked.current=false}},[blocked,markdownBlocked,htmlBlocked]);
 const [interactiveChoice,setInteractiveHtml]=useState<boolean|null>(null),[commentsOpen,setCommentsOpen]=useState(false),[quote,setQuote]=useState('');
 const [hoveredParagraph,setHoveredParagraph]=useState<ParagraphTarget|null>(null);
 const [paragraphUndo,setParagraphUndo]=useState<{before:string;after:string}|null>(null);
 const lastParagraph=useRef<ParagraphTarget|null>(null),htmlSelection=useRef(''),hoverTimeout=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const htmlEditor=useRef<HtmlParagraphEditor>(null),markdownEditor=useRef<MarkdownParagraphEditor>(null);
 const [review,setReview]=useState<Review>(),[reviewError,setReviewError]=useState('');
 const [diff,setDiff]=useState<{original:string;label:string}|null>(null),[diffError,setDiffError]=useState(''),[allowNetwork,setAllowNetwork]=useState(false);
 const wb=useWorkbench(),doc=useFileDocument(resource),id=documentKey(resource);const [mode,setMode]=useState<Mode>(()=>savedMode(resource));const [refresh,setRefresh]=useState(0),[findToken,setFindToken]=useState(0),[compare,setCompare]=useState(false),[copyPath,setCopyPath]=useState(''),[copyOpen,setCopyOpen]=useState(false),[copyBusy,setCopyBusy]=useState(false),[copyError,setCopyError]=useState(''),[previewUrl,setPreviewUrl]=useState(''),[showFind,setShowFind]=useState(false),[search,setSearch]=useState(''),[matches,setMatches]=useState(0);const [splitRatio,setSplitRatio]=useState(()=>{try{return Math.max(15,Math.min(85,Number(sessionStorage.getItem('file-split:'+fileViewIdentity(resource)))||50));}catch{return 50;}});const frame=useRef<HTMLDivElement>(null);const content=useRef<HTMLDivElement>(null);const rendered=useRef<HTMLDivElement>(null),matchIndex=useRef(0),editableRanges=useRef<Range[]>([]);const [highlightName]=useState(()=>'library-find-'+crypto.randomUUID());
 useEffect(()=>watchDocument(resource,()=>{const element=frame.current;if(!element)return false;const rect=element.getBoundingClientRect();return rect.width>0&&rect.height>0&&rect.bottom>0&&rect.right>0&&rect.top<innerHeight&&rect.left<innerWidth&&(!element.checkVisibility||element.checkVisibility({checkVisibilityCSS:true}));}),[id]);
 useEffect(()=>{loadDocument(resource);},[id,resource.root_id,resource.file_id]);
 useEffect(()=>{setPreviewUrl('');setInteractiveHtml(null);setEditingMarkdown(false);setMarkdownBlocked(false);setEditingHtml(false);setHtmlBlocked(false);setCompare(false);setCopyOpen(false);setMode(savedMode(resource));setAllowNetwork(false);setDiff(null);setDiffError('');},[id]);
 const viewId=fileViewIdentity(resource);
 const [small,setSmall]=useState(()=>matchMedia('(max-width:699px)').matches);
 useEffect(()=>{const media=matchMedia('(max-width:699px)'),update=()=>setSmall(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);
 useEffect(()=>{try{sessionStorage.setItem('file-mode:'+viewId,mode);sessionStorage.setItem('file-split:'+viewId,String(splitRatio));}catch{}},[mode,viewId,splitRatio]);
 function resizeSplit(event:React.PointerEvent){event.preventDefault();const parent=content.current;if(!parent)return;const rect=parent.getBoundingClientRect(),vertical=matchMedia('(max-width:650px)').matches;const move=(e:PointerEvent)=>setSplitRatio(Math.max(15,Math.min(85,((vertical?e.clientY-rect.top:e.clientX-rect.left)/(vertical?rect.height:rect.width))*100)));const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);}
 const file=doc.file,kind=file?viewerKind(file.name,file.mime):'unsupported',rich=kind==='markdown'||kind==='html';
 const activeMode=mode==='diff'?'diff':rich?mode:'source';
 const interactiveHtml=interactiveChoice??(kind==='html'&&!doc.dirty&&(/<script[\s>]/i.test(doc.text)||Boolean(doc.error&&!doc.text)));
 const showInteractiveHtml=kind==='html'&&interactiveHtml&&(activeMode==='render'||activeMode==='split');
 const editorUnavailable=Boolean(doc.error&&!doc.text);
 const useInteractiveFallback=editorUnavailable&&showInteractiveHtml&&!doc.dirty;
 const renderedWritable=file?.document_editable===true;
 const renderedEditing=kind==='html'?editingHtml:editingMarkdown;
 const canReview=Boolean(rich&&file?.document_editable!==undefined);
 function hideParagraphActions(){
  clearTimeout(hoverTimeout.current);
  hoverTimeout.current=setTimeout(()=>setHoveredParagraph(null),450);
 }
 function updateParagraph(paragraph:ParagraphTarget|null){
  if(!paragraph){hideParagraphActions();return}
  clearTimeout(hoverTimeout.current);
  lastParagraph.current=paragraph;
  setHoveredParagraph(paragraph);
 }
 function hoverHtml(paragraph:HtmlParagraph|null){
  updateParagraph(paragraph?{...paragraph,target:paragraph,deletable:true}:null);
 }
 function hoverMarkdown(event:React.PointerEvent<HTMLDivElement>){
  const block=(event.target as Element).closest<HTMLElement>('.workbench-markdown p,.tiptap p');
  if(!block){hideParagraphActions();return}
  const {right,top}=block.getBoundingClientRect();
  const previous=lastParagraph.current;
  if(hoveredParagraph&&previous?.target===block&&previous.right===right&&previous.top===top){clearTimeout(hoverTimeout.current);return}
  updateParagraph({right,top,quote:block.innerText,target:block,deletable:!!block.parentElement?.matches('.tiptap')});
 }
 function startComment(paragraph=lastParagraph.current,useSelection=true){
  let selection='';
  if(useSelection){
   const range=window.getSelection();
   selection=kind==='html'?htmlSelection.current:range?.anchorNode&&content.current?.contains(range.anchorNode)?range.toString():'';
  }
  const excerpt=paragraph?.target instanceof HTMLElement&&paragraph.target.isConnected?paragraph.target.innerText:paragraph?.quote;
  setQuote((selection.trim()||excerpt||'').trim().slice(0,2000));
  setCommentsOpen(true);
  setHoveredParagraph(null);
  requestAnimationFrame(()=>frame.current?.querySelector<HTMLTextAreaElement>('.document-comments textarea')?.focus());
 }
 function deleteParagraph(paragraph:ParagraphTarget){
  if(!renderedEditing||!renderedWritable||markdownBlocked||htmlBlocked||doc.conflict)return;
  if(paragraph.target instanceof HTMLElement)markdownEditor.current?.deleteParagraph(paragraph.target);
  else{
   const next=htmlEditor.current?.deleteParagraph(paragraph.target,doc.text);
   if(next!=null)setParagraphUndo({before:doc.text,after:next});
  }
  lastParagraph.current=null;
  setHoveredParagraph(null);
 }
 useEffect(()=>{
  lastParagraph.current=null;
  htmlSelection.current='';
  setHoveredParagraph(null);
  return()=>clearTimeout(hoverTimeout.current);
 },[activeMode,interactiveHtml,renderedEditing]);
 function changeMode(value:Mode){
  setShowFind(false);
  setMode(value);
  if(value==='source'||value==='diff'){
   setEditingHtml(false);
   setEditingMarkdown(false);
   if(renderedEditing)scheduleDocumentSave(resource,0);
  }
 }
 function toggleEditing(){
  if(!renderedEditing){
   if(activeMode==='source'||activeMode==='diff')changeMode('render');
   setInteractiveHtml(false);
  }else {scheduleDocumentSave(resource,0);setInteractiveHtml(null);}
  if(kind==='html')setEditingHtml(!renderedEditing);
  else setEditingMarkdown(!renderedEditing);
 }
 useEffect(()=>{
  if(!file||!canReview||!(commentsOpen||activeMode==='diff'))return;
  const controller=new AbortController();
  setReviewError('');
  loadDocumentReview(file,controller.signal).then(value=>setReview(value)).catch(error=>{
   if(!controller.signal.aborted)setReviewError(error.message);
  });
  return()=>controller.abort();
 },[id,file?.root_id,file?.file_id,file?.version,commentsOpen,activeMode,canReview]);
 useEffect(()=>{try{setRenderedRecovery(file?listRenderedRecoveries(renderedRecoveryKey(file.host_id,file.path),recoveryStorage()):[])}catch{setRenderedRecovery([])}},[file?.host_id,file?.path]);
 useEffect(()=>{if(activeMode!=='diff'||!file||canReview)return;let active=true;setDiff(null);setDiffError('');loadGitDiff(resource).then(value=>{if(active)setDiff(value);}).catch(e=>{if(active)setDiffError(e.message);});return()=>{active=false;};},[id,activeMode,file?.version,refresh,canReview]);
 const current:Resource={...resource,root_id:file?.root_id||resource.root_id,file_id:file?.file_id||resource.file_id};
 useEffect(()=>{if(file)wb.updateTitle?.(resource,file.name);},[id,file?.name]);
 useEffect(()=>{const root=rendered.current;if(!root||kind!=='markdown'||editingMarkdown)return;root.querySelectorAll('mark[data-file-find]').forEach(mark=>mark.replaceWith(document.createTextNode(mark.textContent||'')));root.normalize();if(!showFind||!search.trim()){setMatches(0);return;}const term=search.toLocaleLowerCase(),walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes:Text[]=[];while(walker.nextNode())nodes.push(walker.currentNode as Text);let count=0;for(const node of nodes){const value=node.data,lower=value.toLocaleLowerCase();let index=lower.indexOf(term),start=0;if(index<0)continue;const fragment=document.createDocumentFragment();while(index>=0){fragment.append(value.slice(start,index));const mark=document.createElement('mark');mark.dataset.fileFind='';mark.textContent=value.slice(index,index+term.length);fragment.append(mark);count++;start=index+term.length;index=lower.indexOf(term,start);}fragment.append(value.slice(start));node.replaceWith(fragment);}setMatches(count);matchIndex.current=0;root.querySelector('mark[data-file-find]')?.scrollIntoView({block:'center'});},[search,showFind,doc.text,mode,kind,editingMarkdown]);
 useEffect(()=>{const highlights=(CSS as any).highlights,HighlightType=(window as any).Highlight;highlights?.delete(highlightName);editableRanges.current=[];if(!renderedEditorFind(activeMode,editingMarkdown)||!showFind||!search.trim())return;const root=rendered.current?.querySelector('.tiptap');if(!root)return;const ranges=findRanges(root,search);editableRanges.current=ranges;matchIndex.current=0;setMatches(ranges.length);let style:HTMLStyleElement|undefined;if(highlights&&HighlightType&&ranges.length){highlights.set(highlightName,new HighlightType(...ranges));style=document.createElement('style');style.textContent='::highlight('+highlightName+'){background:#f5d85e;color:#111}';document.head.append(style)}return()=>{highlights?.delete(highlightName);style?.remove()}},[editingMarkdown,showFind,search,doc.text,highlightName,activeMode]);
 useEffect(()=>{if(activeMode==='source'||activeMode==='diff'){setShowFind(false);editableRanges.current=[]}},[activeMode]);
 useEffect(()=>{if(!markdownBlocked&&!htmlBlocked)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[markdownBlocked,htmlBlocked]);
 useEffect(()=>{if(resource.anchor)rendered.current?.querySelector('#'+CSS.escape(resource.anchor))?.scrollIntoView({block:'start'});},[resource.anchor,doc.loading,mode]);
 async function refreshFile(){try{await loadDocument(resource,true);setRefresh(r=>r+1);}catch(e){wb.notify((e as Error).message);}}
 async function copy(){setCopyBusy(true);setCopyError('');try{const copied=await saveCopy(resource,copyPath);setCopyOpen(false);wb.open({...resource,path:copied.path,root_id:copied.root_id,file_id:copied.file_id,title:copied.name},'tab');wb.notify('Saved a copy. The original draft is still available.');}catch(e){setCopyError((e as Error).message);}finally{setCopyBusy(false);}}
 function find(){if(kind==='html'&&interactiveHtml&&activeMode==='render'){wb.notify('Find is available in rendered or source view.');return}if(kind==='html'&&!interactiveHtml&&['render','split'].includes(activeMode)){setShowFind(v=>!v);return}if(renderedEditorFind(activeMode,editingMarkdown)){setShowFind(v=>!v);return}if(activeMode==='source'||activeMode==='split'||activeMode==='diff')setFindToken(v=>v+1);else setShowFind(v=>!v);}
 function downloadDraft(){const blob=new Blob([doc.text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=(file?.name||'file')+'.draft';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const actions=<><button title="Refresh from disk" aria-label="Refresh file" disabled={doc.loading||doc.dirty} onClick={refreshFile}>Refresh</button><details className="fv-menu"><summary aria-label="File actions" title="File actions">···</summary><div>{rich&&<button disabled={markdownBlocked||htmlBlocked} onClick={event=>{changeMode(activeMode==='split'?'render':'split');event.currentTarget.closest('details')?.removeAttribute('open')}}>{activeMode==='split'?'Exit split view':'Split source and preview'}</button>}<a href={file?contentUrl(file,true):undefined} download={file?.name}>Download file</a>{doc.dirty&&<button onClick={downloadDraft}>Download draft</button>}<button onClick={()=>wb.open(current,'tab')}>Open as workspace tab</button><button onClick={()=>wb.open(current,'default-browser-window')}>Open in default browser window</button><button onClick={()=>wb.open(current,'editor')}>Open in editor</button>{previewUrl&&<a href={previewUrl} target="_blank" rel="noopener noreferrer">Open running preview</a>}{file?.editable&&<button onClick={()=>{setCopyOpen(true);setCopyPath((file.relative_path||file.name).replace(/(\.[^./]+)?$/,'.copy$1'));}}>Save a copy…</button>}</div></details></>;
 if(doc.loading)return <div ref={frame} className="fv-root"><div className="fv-empty" role="status">Opening file…</div></div>;
 if(!file)return <div ref={frame} className="fv-root"><div className="fv-empty" role="alert"><p>{doc.error||'This file is unavailable.'}</p><button onClick={refreshFile}>Try again</button><button onClick={()=>wb.open(resource,'editor')}>Open in editor</button></div></div>;
 const source=<Suspense fallback={<div className="fv-empty" role="status">Opening editor…</div>}><SourceEditor identity={id} name={file.name} text={doc.text} readOnly={!(file.editable||renderedWritable)} onChange={value=>changeText(resource,value)} onSave={()=>saveDocument(resource)} line={resource.line} theme={wb.preferences.theme} fontSize={wb.preferences.codeFontSize||14} findToken={findToken} original={compare?doc.conflict?.text:activeMode==='diff'?diff?.original:undefined}/></Suspense>;
 const preview=kind==='markdown'?<div className="fv-markdown" ref={rendered} onPointerMove={hoverMarkdown} onPointerLeave={hideParagraphActions} onScroll={hideParagraphActions} style={{fontSize:14,lineHeight:wb.preferences.lineHeight}}><div style={{maxWidth:wb.preferences.readingWidth||860}}>{editingMarkdown?<Suspense fallback={<p>Opening rendered editor…</p>}><RenderedMarkdownEditor ref={markdownEditor} text={doc.text} file={file} onChange={value=>changeRenderedText(resource,value)} onBlocked={setMarkdownBlocked} onRecovery={()=>setRenderedRecovery(listRenderedRecoveries(renderedRecoveryKey(file.host_id,file.path),recoveryStorage()))} onOpen={(target,external)=>wb.open({...target,meta:{chat_key:resource.key||resource.meta?.chat_key}},external?'browser-tab':undefined)}/></Suspense>:<Markdown text={doc.text} host_id={file.host_id} cwd={previewLocation(file.path).artifact_directory} chat_key={resource.key||resource.meta?.chat_key}/>}</div></div>:!interactiveHtml?<div className="fv-html-edit"><Suspense fallback={<p>Opening rendered editor…</p>}><RenderedHtmlEditor ref={htmlEditor} readOnly={!renderedWritable||!editingHtml} onSelection={value=>{htmlSelection.current=value}} onParagraph={hoverHtml} findVisible={showFind} findQuery={search} findNextToken={htmlFindNext} onFindToggle={()=>setShowFind(v=>!v)} onFindClose={()=>setShowFind(false)} onFindCount={(count,index)=>{setMatches(count);matchIndex.current=index}} text={doc.text} file={file} onChange={value=>changeRenderedText(resource,value)} onBlocked={setHtmlBlocked} onRecovery={()=>setRenderedRecovery(listRenderedRecoveries(renderedRecoveryKey(file.host_id,file.path),recoveryStorage()))} onOpen={(target,external)=>wb.open({...target,meta:{chat_key:resource.key||resource.meta?.chat_key}},external?'browser-tab':undefined)}/></Suspense></div>:<HtmlPreview host={file.host_id} path={file.path} refresh={refresh} allowNetwork={allowNetwork} version={file.version} registeredRoot={Boolean(file.root_id)} fileId={file.file_id} rootId={file.root_id} onUrl={setPreviewUrl}/>;
 const frameBounds=hoveredParagraph&&frame.current?.getBoundingClientRect();
 const previewBounds=hoveredParagraph&&content.current?.getBoundingClientRect();
 return <div ref={frame} className="fv-root" data-viewer={kind} onKeyDown={event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='f'){event.preventDefault();event.stopPropagation();find()}else if(event.key==='Escape'&&showFind){event.preventDefault();event.stopPropagation();setShowFind(false)}}}>
  {kind!=='pdf'&&kind!=='image'&&<div className="fv-toolbar" aria-label="File controls">
   {rich?<div className="fv-modes" role="group" aria-label="Document view">
    {([['render','Preview'],['source','Source'],['diff','Diff']] as [Mode,string][]).map(([value,label])=>
     <button key={value} aria-pressed={mode===value} disabled={markdownBlocked||htmlBlocked} onClick={()=>changeMode(value)}>{label}</button>)}
   </div>:kind==='source'&&<button aria-pressed={activeMode==='diff'} onClick={()=>{setShowFind(false);setMode(mode==='diff'?'source':'diff')}}>Diff</button>}
   {rich&&(renderedWritable?<button type="button" className="fv-edit-toggle" aria-pressed={renderedEditing}
    title={renderedEditing?'Switch to Read Only':'Edit rendered text'} disabled={markdownBlocked||htmlBlocked} onClick={toggleEditing}>
    <Pencil size={13} aria-hidden="true"/>{renderedEditing?'Edit':'Read Only'}
   </button>:<span className="fv-status" title={canReview?'This file is read-only.':'This Library server does not have rendered editing yet.'}>Read Only</span>)}
   {['source','markdown','html'].includes(kind)&&!(kind==='html'&&interactiveHtml&&activeMode==='render')&&<button onClick={find} title="Find in this document">Find</button>}
   {kind==='markdown'&&<details className="fv-menu"><summary>Outline</summary><div>{doc.text.split('\n').filter(line=>/^#{1,6}\s/.test(line)).slice(0,100).map((line,index)=><button key={index} onClick={()=>{if(mode==='source')setMode('render');setTimeout(()=>{const headings=[...(rendered.current?.querySelectorAll('h1,h2,h3,h4,h5,h6')||[])];headings[index]?.scrollIntoView({block:'start',behavior:'smooth'});},0);}}>{line.replace(/^#+\s*/,'')}</button>)}</div></details>}
   {activeMode==='diff'&&diff&&<span className="fv-status">{diff.label} → open buffer</span>}
   {kind==='html'&&['render','split'].includes(activeMode)&&<button aria-pressed={interactiveHtml} disabled={htmlBlocked} onClick={()=>{setInteractiveHtml(!interactiveHtml);setEditingHtml(false);setShowFind(false)}}>Interactive preview</button>}
   {showInteractiveHtml&&<label className="fv-network"><input type="checkbox" checked={allowNetwork} onChange={e=>setAllowNetwork(e.target.checked)}/>Allow web assets</label>}
   {canReview&&<button aria-expanded={commentsOpen} onMouseDown={event=>event.preventDefault()} onClick={()=>startComment()}>Comment{review?.comments.length?' ('+review.comments.length+')':''}</button>}
   {(file.editable||renderedWritable)&&<button className={doc.dirty?'fv-save':''} disabled={!doc.dirty||doc.saving||!!doc.conflict||markdownBlocked||htmlBlocked} onClick={()=>saveDocument(resource)}>
    {doc.saving?'Saving…':doc.error&&doc.dirty?'Retry save':doc.dirty?(file.document_editable&&renderedEditing&&activeMode!=='source'?'Autosaving…':'Save changes'):'Saved'}
   </button>}
   <span className="fv-spacer"/>{actions}
  </div>}
  {doc.refreshError&&<div className="fv-error" role="status">{doc.refreshError}</div>}
  {editingHtml&&activeMode!=='source'&&activeMode!=='diff'&&!interactiveHtml&&paragraphUndo?.after===doc.text&&!doc.conflict&&
   <div className="fv-paragraph-undo" role="status">Paragraph deleted <button type="button" onClick={()=>{
    if(htmlEditor.current?.replaceParagraphSource(paragraphUndo.after,paragraphUndo.before))setParagraphUndo(null);
   }}>Undo</button></div>}
  {doc.error&&!useInteractiveFallback&&<div className="fv-error" role="alert">{doc.error}</div>}{doc.persistenceError&&<div className="fv-error" role="alert">{doc.persistenceError}<button onClick={downloadDraft}>Download draft</button></div>}
  <RecoveryPanel resource={resource} document={doc}/>
  {renderedRecovery.map(recovery=><div className="fv-conflict" role="status" key={recovery.owner}><span>An unsupported rendered edit is available for recovery{recovery.persisted?'':' in this window only'}. The source draft was not replaced.</span><button type="button" onClick={()=>navigator.clipboard.writeText(recovery.text)}>Copy rendered draft</button><button type="button" onClick={()=>{const url=URL.createObjectURL(new Blob([recovery.text],{type:'text/markdown'}));const a=document.createElement('a');a.href=url;a.download=file.name+'.rendered-recovery.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}}>Download rendered draft</button><button type="button" onClick={()=>{try{const key=renderedRecoveryKey(file.host_id,file.path);discardRenderedRecovery(key,recovery.owner,recoveryStorage());setRenderedRecovery(listRenderedRecoveries(key,recoveryStorage()))}catch{wb.notify('Could not remove the saved recovery draft.')}}}>Discard recovery</button></div>)}
  {doc.conflict&&<div className="fv-conflict" role="alert"><span>The file changed on disk. Your draft is preserved.</span><button onClick={()=>{setCompare(c=>!c);setMode('source');}}>{compare?'Back to draft':'Compare'}</button><button onClick={()=>{if(window.confirm('Replace your draft with the current disk version? Your unsaved draft will be discarded.')){reloadConflict(resource);setCompare(false);}}}>Reload disk version</button><button disabled={!file.editable} onClick={()=>{setCopyOpen(true);setCopyPath((file.relative_path||file.name).replace(/(\.[^./]+)?$/,'.copy$1'));}}>Save a copy…</button></div>}
  {copyOpen&&<form className="fv-copy" onSubmit={e=>{e.preventDefault();copy();}}><label>New path in this folder root<input value={copyPath} onChange={e=>setCopyPath(e.target.value)} required autoFocus aria-label="Save copy path"/></label><button disabled={copyBusy||!copyPath.trim()}>{copyBusy?'Saving…':'Save copy'}</button><button type="button" onClick={()=>setCopyOpen(false)}>Cancel</button>{copyError&&<span role="alert">{copyError}</span>}</form>}
  {showFind&&(kind==='markdown'||kind==='html'&&!interactiveHtml)&&<form className="fv-findbar" onSubmit={e=>{e.preventDefault();if(kind==='html'&&!interactiveHtml){setHtmlFindNext(v=>v+1);return}if(renderedEditorFind(activeMode,editingMarkdown)){const ranges=editableRanges.current;if(ranges.length){matchIndex.current=(matchIndex.current+1)%ranges.length;const range=ranges[matchIndex.current],selection=window.getSelection();selection?.removeAllRanges();selection?.addRange(range);range.startContainer.parentElement?.scrollIntoView({block:'center'});}return}const marks=rendered.current?.querySelectorAll('mark[data-file-find]');if(marks?.length){matchIndex.current=(matchIndex.current+1)%marks.length;marks[matchIndex.current].scrollIntoView({block:'center'});}}}><input type="search" aria-label="Find in document" placeholder="Find in document" autoFocus value={search} onChange={e=>setSearch(e.target.value)}/><span role="status">{matches} matches</span><button>Next</button><button type="button" onClick={()=>setShowFind(false)}>Close</button></form>}
  <div ref={content} className={'fv-content'+(rich&&activeMode==='split'&&!editorUnavailable?' fv-split':'')}>
   {activeMode==='diff'&&canReview?(reviewError?<div className="fv-empty" role="alert">{reviewError}</div>:doc.conflict||review?.previous!=null?<RenderedDocumentDiff previous={doc.conflict?.text??review!.previous!} current={doc.text} markdown={kind==='markdown'}/>:<div className="fv-empty">{review?'The previous version will be kept when you make your first edit.':'Loading previous version…'}</div>):activeMode==='diff'?(diffError?<div className="fv-empty" role="alert">{diffError}</div>:diff?<div className="fv-source">{source}</div>:<div className="fv-empty" role="status">Loading Git comparison…</div>):editorUnavailable?(
    useInteractiveFallback?<div className="fv-preview">{preview}</div>:
    <div className="fv-empty"><p>{kind==='html'&&activeMode!=='source'&&!doc.dirty?'Select Interactive preview to view this HTML file.':'Use Download or Open in editor to inspect this file.'}</p></div>
   ):kind==='pdf'?<Suspense fallback={<div className="fv-empty">Opening PDF viewer…</div>}><PdfViewer url={contentUrl(file)} identity={viewId} initialPage={resource.page} actions={actions} back={small&&resource.meta?.backTo?<button className="fv-back" onClick={()=>wb.open(resource.meta.backTo,'tab')}><ArrowLeft size={15}/>Back to chat</button>:null}/></Suspense>:kind==='image'?<ImageViewer url={contentUrl(file)} name={file.name} svg={file.mime==='image/svg+xml'} actions={actions}/>:kind==='unsupported'?<div className="fv-empty"><p>{file.name} cannot be displayed here.</p><a href={contentUrl(file,true)} download={file.name}>Download file</a><button onClick={()=>wb.open(current,'system')}>Open in system app</button></div>:rich?<>{activeMode!=='render'&&<div className="fv-source" style={activeMode==='split'?{flex:'0 0 '+splitRatio+'%'}:undefined}>{source}</div>}{activeMode==='split'&&<div className="fv-sash" role="separator" tabIndex={0} aria-label="Resize source and preview" aria-valuemin={15} aria-valuemax={85} aria-valuenow={Math.round(splitRatio)} onPointerDown={resizeSplit} onKeyDown={e=>{if(['ArrowRight','ArrowDown'].includes(e.key)){e.preventDefault();setSplitRatio(r=>Math.min(85,r+5));}if(['ArrowLeft','ArrowUp'].includes(e.key)){e.preventDefault();setSplitRatio(r=>Math.max(15,r-5));}}}/ >}{activeMode!=='source'&&<div className="fv-preview">{preview}</div>}</>:<div className="fv-source">{source}</div>}
  </div>
  {canReview&&hoveredParagraph&&frameBounds&&previewBounds&&hoveredParagraph.top<previewBounds.bottom&&
   <div className="document-paragraph-actions" role="group" aria-label="Paragraph actions"
    style={{left:Math.max(0,Math.min(hoveredParagraph.right+6,previewBounds.right-63)-frameBounds.left),top:Math.max(previewBounds.top,hoveredParagraph.top)-frameBounds.top}}
    onPointerEnter={()=>clearTimeout(hoverTimeout.current)} onPointerLeave={hideParagraphActions}>
    <button type="button" title="Comment on paragraph" aria-label="Comment on paragraph" onMouseDown={event=>event.preventDefault()} onClick={()=>startComment(hoveredParagraph,false)}><MessageSquare size={14}/></button>
    <button type="button" title={renderedEditing?'Delete paragraph':'Switch to Edit to delete paragraphs'} aria-label="Delete paragraph"
     disabled={!hoveredParagraph.deletable||!renderedEditing||!renderedWritable||markdownBlocked||htmlBlocked||!!doc.conflict}
     onMouseDown={event=>event.preventDefault()} onClick={()=>deleteParagraph(hoveredParagraph)}><Trash2 size={14}/></button>
   </div>}
  {commentsOpen&&canReview&&<><DocumentComments file={file} review={review} quote={quote} onClearQuote={()=>setQuote('')} onUpdate={value=>{setReview(value);setQuote('')}} onClose={()=>{setCommentsOpen(false);setQuote('')}}/>{reviewError&&<p className="fv-error" role="alert">{reviewError}</p>}</>}
 </div>;
}
export default FileViewer;
