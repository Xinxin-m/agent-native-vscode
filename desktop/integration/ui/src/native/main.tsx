import {PathContextMenu} from '../open/PathContextMenu';
import React,{useState,useEffect,useCallback,useRef} from 'react';
import {createNativeRefresh} from './refresh';
import {chimeForAttention,soundEnabled} from '../state/approvalSound';
import {conversations,conversationIdentity} from '../state/conversations';
import {createRoot} from 'react-dom/client';
import {WorkbenchContext} from '../state/context';
import {api} from '../api/client';
import {Conversation} from '../chat/Conversation';
import {Sidebar} from '../navigation/Sidebar';
import {Projects} from '../navigation/Projects';
import {Recent} from '../navigation/Recent';
import {Settings} from '../navigation/Settings';
import {AccountsPanel} from '../navigation/AccountsPanel';
import {AgentOverview} from '../chat/Overview';
import {SessionsView} from '../navigation/Sessions';
import {FilesTab} from '../navigation/FileTree';
import {installTooltips} from '../ui/tooltips';
import {installRunningIndicator} from '../ui/runningIndicator';
import '../theme.css';
import './native.css';
const boot=(window as any).__LIBRARY_BOOT__,rpc=(window as any).libraryRpc;
// A chat pane asks only for its own chat; a new chat gets its key after the first send.
const chatKeys=()=>boot.kind==='chat'?[boot.resource?.key,conversations.peek(conversationIdentity(boot.resource))?.nativeKey].filter(Boolean):undefined;
function App(){const [data,setData]=useState<any>(null),[accounts,setAccounts]=useState([]),[hosts,setHosts]=useState([]),[runs,setRuns]=useState([]),[attention,setAttention]=useState([]),[liveSessions,setLive]=useState([]),[error,setError]=useState(''),[toast,setToast]=useState(''),[mode,setMode]=useState('chats'),[preferences,setPrefs]=useState(boot.preferences);
const preferenceRevision=useRef(0),attentionReply=useRef<any>(null);
const polling=useRef<ReturnType<typeof createNativeRefresh>|null>(null);
if(!polling.current)polling.current=createNativeRefresh((path,options)=>rpc('api',{path,fresh:options?.fresh===true,since:options?.since,keys:path==='/api/state'?chatKeys():undefined}),{state:setData,accounts:v=>setAccounts(v.accounts||[]),hosts:v=>setHosts(v.hosts||[]),runs:v=>setRuns(v.runs||[]),attention:v=>{attentionReply.current=v;setAttention(v.items||[])},live:v=>setLive(v.sessions||[]),error:setError});
const refresh=useCallback(()=>polling.current!.refresh(true),[]);
useEffect(()=>{const controller=polling.current!;controller.setActive(true);const refreshListener=()=>void refresh(),visible=()=>{if(!document.hidden)void controller.refresh()};window.addEventListener('library-refresh',refreshListener);window.addEventListener('library-state',visible);document.addEventListener('visibilitychange',visible);void controller.refresh();const timer=setInterval(visible,5000);return()=>{controller.setActive(false);clearInterval(timer);window.removeEventListener('library-refresh',refreshListener);window.removeEventListener('library-state',visible);document.removeEventListener('visibilitychange',visible)}},[]);
useEffect(()=>{if(boot.kind==='sidebar'&&data)chimeForAttention(attention,soundEnabled(data.config,attentionReply.current))},[attention,Boolean(data)]);
useEffect(()=>{const change=(e:any)=>setPrefs(e.detail);window.addEventListener('library-preferences',change);return()=>window.removeEventListener('library-preferences',change)},[]);
useEffect(()=>{const p=preferences;const apply=()=>{document.documentElement.dataset.theme=p.theme==='system'?(document.body.classList.contains('vscode-dark')?'dark':'light'):p.theme};apply();const observer=new MutationObserver(apply);observer.observe(document.body,{attributes:true,attributeFilter:['class']});document.documentElement.style.setProperty('--reading-size','14px');document.documentElement.style.setProperty('--reading-line',String(p.lineHeight));document.documentElement.style.setProperty('--reading-width',p.readingWidth+'px');return()=>observer.disconnect()},[preferences]);
const open=(resource:any,destination?:string)=>void rpc('open',{resource,destination}).catch((e:any)=>setToast(e.message));const value:any={doubleClickMs:boot.doubleClickMs,sessionDetails:(id:string)=>rpc('sessionDetails',{id}).catch((e:any)=>setToast(e.message)),renameChat:async(key:string,title:string)=>{const value=await rpc('rename',{key,title});await refresh();return value},getModels:(context:any)=>rpc('models',context),confirm:(message:string,button:string)=>rpc('confirm',{message,button}),data,stateError:boot.kind==='sidebar'?error:'',connect:()=>rpc('endpoint'),accounts,hosts,runs,attention,liveSessions,preferences,open,refresh,notify:setToast,setPreferences:async(p:any)=>{const previous=preferences,next={...preferences,...p},revision=++preferenceRevision.current;setPrefs(next);try{await rpc('preferences',{value:next})}catch(error){if(preferenceRevision.current===revision)setPrefs(previous);setToast(error instanceof Error?error.message:String(error));throw error}},updateTitle:(resource:any,title:string)=>rpc('title',{title})};
return <WorkbenchContext.Provider value={value}><PathContextMenu/>{error&&boot.kind!=='sidebar'&&<div className="native-error" role="alert">{error}<button onClick={()=>void refresh()}>Retry</button><button onClick={()=>void rpc('endpoint')}>Connect Library</button></div>}{boot.kind==='sidebar'?<Sidebar hideFiles mode={mode} setMode={setMode} onContext={()=>{}}/>:boot.kind==='settings'?<Settings onEditorTool={tool=>rpc('advancedEditor',{tool})}/>:boot.kind==='accounts'?<div className="settings-page"><header><h1>Accounts</h1></header><AccountsPanel onGitHubAccounts={connect=>rpc('githubAccounts',{connect})}/></div>:boot.kind==='projects'?<Projects {...value}/>:boot.kind==='recent'?<Recent {...value}/>:boot.kind==='overview'?<AgentOverview/>:boot.kind==='sessions'?<SessionsView/>:boot.kind==='files'?<FilesTab resource={boot.resource} onContext={()=>{}}/>:<Conversation resource={boot.resource}/>} {toast&&<button className="native-toast" onClick={()=>setToast('')}>{toast}</button>}</WorkbenchContext.Provider>}
// The host stops every webview from taking drops once it sees a Library drag. Keeping a Files tab's
// drags inside the webview lets them land in another Files tab; other panes still open the file.
if(boot.kind==='files')for(const type of ['dragstart','drag','dragenter','dragover'])document.addEventListener(type,event=>event.stopPropagation());
installTooltips(document);installRunningIndicator(document);createRoot(document.getElementById('root')!).render(<App/>);
