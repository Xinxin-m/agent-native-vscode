import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {editHtml,inspectHtml,previewHtml,type HtmlChange,type HtmlModel,acceptHtmlMessage,htmlSnapshotForFile} from './htmlEdit';
import {renderedRecoveryKey} from './markdownEdit';
import {createHtmlAssetLoader} from './htmlAssets';
import {rememberRenderedRecovery,recoveryStorage} from './renderedRecovery';
import {previewLocation,type FileRecord} from './helpers';
import {parseRenderedLink} from '../open/links';
import type {Resource} from '../state/types';

type Props={text:string;file:FileRecord;onChange:(text:string)=>void;onBlocked:(blocked:boolean)=>void;onRecovery:()=>void;onOpen:(resource:Resource,external:boolean)=>void;readOnly?:boolean;findVisible?:boolean;findQuery?:string;findNextToken?:number;onFindToggle?:()=>void;onFindClose?:()=>void;onFindCount?:(count:number,index:number)=>void};
type Snapshot={model:HtmlModel;token:string;last:string;key:string;owner:string};
const randomToken=()=>{const bytes=crypto.getRandomValues(new Uint8Array(24));return btoa(String.fromCharCode(...bytes))};
export function RenderedHtmlEditor({text,file,onChange,onBlocked,onRecovery,onOpen,readOnly=false,findVisible=false,findQuery='',findNextToken=0,onFindToggle,onFindClose,onFindCount}:Props){
 const frame=useRef<HTMLIFrameElement>(null),snapshot=useRef<Snapshot|null>(null),current=useRef(text),seq=useRef(0),callbacks=useRef({onChange,onBlocked,onRecovery,onOpen,onFindToggle,onFindClose,onFindCount});callbacks.current={onChange,onBlocked,onRecovery,onOpen,onFindToggle,onFindClose,onFindCount};
 const identity=renderedRecoveryKey(file.host_id,file.path),activeIdentity=useRef(identity);
 const [srcDoc,setSrcDoc]=useState(''),[error,setError]=useState(''),[generation,setGeneration]=useState(0),[recovery,setRecovery]=useState('');
 const findState=useRef({findVisible,findQuery,findNextToken});findState.current={findVisible,findQuery,findNextToken};const sendFind=()=>{const state=snapshot.current;if(state)frame.current?.contentWindow?.postMessage({type:'library-html-find',token:state.token,visible:findState.current.findVisible,query:findState.current.findQuery,nextToken:findState.current.findNextToken},'*')};
 useEffect(()=>{sendFind()},[findVisible,findQuery,findNextToken]);
 const saveRecovery=(state:Snapshot,draft:string)=>{setRecovery(draft);rememberRenderedRecovery(state.key,state.owner,draft,recoveryStorage());callbacks.current.onRecovery()};
 useEffect(()=>{let active=true;const version=++seq.current,model=inspectHtml(text),token=randomToken(),controller=new AbortController(),key=identity,owner=crypto.randomUUID();current.current=text;snapshot.current=null;setSrcDoc('');setError('');callbacks.current.onBlocked(false);
  const loader=createHtmlAssetLoader({rootId:file.root_id,relativePath:file.relative_path||file.name,signal:controller.signal});
  previewHtml(text,model,token,loader,readOnly).then(html=>{if(!active||version!==seq.current)return;snapshot.current={model,token,last:text,key,owner};setSrcDoc(html)}).catch(e=>{if(active){setError((e as Error).message);callbacks.current.onBlocked(true)}});return()=>{active=false;controller.abort();seq.current++;callbacks.current.onBlocked(false)};
 },[file.host_id,file.path,file.root_id,file.relative_path,generation,readOnly]);
 useLayoutEffect(()=>{if(activeIdentity.current!==identity){activeIdentity.current=identity;seq.current++;snapshot.current=null;current.current=text;setRecovery('');setError('');setSrcDoc('');return}const state=htmlSnapshotForFile(snapshot.current,identity);if(!state){if(text!==current.current){current.current=text;setGeneration(n=>n+1)}return}if(text===state.last)return;current.current=text;if(state.last===state.model.source){setGeneration(n=>n+1);return}saveRecovery(state,state.last);frame.current?.contentWindow?.postMessage({type:'library-html-disable',token:state.token},'*');setError('The source changed while this rendered preview had edits. Your prior draft is available for recovery.');callbacks.current.onBlocked(true)},[text,identity]);
 useEffect(()=>{const receive=(event:MessageEvent)=>{const state=htmlSnapshotForFile(snapshot.current,identity),data=event.data;if(!state||!acceptHtmlMessage(event,frame.current?.contentWindow,state.token))return;
  if(data.type==='library-html-find-toggle'){callbacks.current.onFindToggle?.();return}if(data.type==='library-html-find-close'){callbacks.current.onFindClose?.();return}if(data.type==='library-html-find-count'&&Number.isSafeInteger(data.count)&&Number.isSafeInteger(data.index)&&data.count>=0&&data.count<=5000&&data.index>=0&&data.index<=data.count){callbacks.current.onFindCount?.(data.count,data.index);return}
  if(data.type==='library-html-link'&&typeof data.href==='string'){const resource=parseRenderedLink(data.href,previewLocation(file.path).artifact_directory,file.host_id);if(resource)callbacks.current.onOpen(resource,!!data.external);return}
  if(readOnly||data.type!=='library-html-edit'||!Array.isArray(data.changes))return;
  try{const originals=new Map(state.model.nodes.map(node=>[node.id,node.text]));const changes:HtmlChange[]=data.changes.map((c:any)=>({id:c.id,original:originals.get(c.id),text:c.text}));const next=editHtml(state.model.source,state.model,changes);if(current.current!==state.last){saveRecovery(state,next);return}state.last=next;current.current=next;setError('');callbacks.current.onBlocked(false);callbacks.current.onChange(next)}catch(e){setError((e as Error).message);callbacks.current.onBlocked(true);frame.current?.contentWindow?.postMessage({type:'library-html-disable',token:state.token},'*');saveRecovery(state,JSON.stringify(data.changes,null,2))}
 };window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive)},[file.host_id,file.path,readOnly,identity]);
 return <div className="fv-rendered-edit">{error&&<p role="alert">{error} {recovery&&<button type="button" onClick={()=>navigator.clipboard.writeText(recovery)}>Copy recovery</button>}</p>}<iframe ref={frame} title="Edit rendered HTML" onLoad={sendFind} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={srcDoc} style={{width:'100%',height:'100%',border:0}}/></div>;
}
