
import {PathContextMenu} from '../open/PathContextMenu';
import React,{useState,useEffect,useCallback,useRef} from 'react';
import {createNativeRefresh} from './refresh';
import {chimeForAttention,soundEnabled} from '../state/approvalSound';
import {conversations,conversationIdentity} from '../state/conversations';
import {createRoot} from 'react-dom/client';
import {WorkbenchContext,useWorkbench} from '../state/context';
import {useSeenReporter,useChatActivity} from '../state/activity';
import {rowStatus} from '../navigation/chats';
import {api} from '../api/client';
import {Conversation} from '../chat/Conversation';
import {Sidebar} from '../navigation/Sidebar';
import {WorkspaceScopeProvider} from '../navigation/WorkspaceSelector';
import {Projects} from '../navigation/Projects';
import {Recent} from '../navigation/Recent';
import {Settings} from '../navigation/Settings';
import {AccountsPage} from '../navigation/AccountsPanel';
import {NotificationsPage,NotificationsPanel} from '../notifications/NotificationsPage';
import {startFeed,useFeed} from '../notifications/feed';
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
// The panel polls the feed and marks items seen only while its tab is showing; its tab name carries the open count, like the bell.
// The same kind with meta.view "history" is the History tab reached from the panel's foot.
function NotificationsView(){const history=boot.resource?.meta?.view==='history',[visible,setVisible]=useState(!document.hidden),n=useFeed().counts.open;useEffect(()=>{if(!history)void rpc('title',{title:n?`(${n}) Notifications`:'Notifications'}).catch(()=>{});},[n,history]);useEffect(()=>{const on=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',on);return()=>document.removeEventListener('visibilitychange',on)},[]);return history?<NotificationsPage/>:<NotificationsPanel visible={visible}/>;}
// A chat tab follows the server: it tells the server the chat is in front of her (visible, focused window only) and keeps its tab name equal to the chat's current title (the host cuts it to 24 characters).
function ChatTabSync(){const wb=useWorkbench() as any;useSeenReporter(()=>chatKeys()||[],[]);const key=chatKeys()?.[0],title=(wb.data?.chats||[]).find((c:any)=>c.key===key)?.title;useEffect(()=>{if(title)wb.updateTitle?.(boot.resource,title);},[title]);const activity=useChatActivity(boot.resource),status=rowStatus(activity.live,activity.unread,{...activity,failed:activity.failed||activity.recovery});useEffect(()=>{if(key)void rpc('title',{status}).catch(()=>{});},[key,status]);return null;}
function App(){const [data,setData]=useState<any>(null),[accounts,setAccounts]=useState([]),[hosts,setHosts]=useState([]),[runs,setRuns]=useState([]),[attention,setAttention]=useState([]),[liveSessions,setLive]=useState([]),[error,setError]=useState(''),[toast,setToast]=useState(''),[mode,setMode]=useState('chats'),[preferences,setPrefs]=useState(boot.preferences);
const [pendingNoticeAccounts,setPendingNoticeAccounts]=useState([]);
useEffect(()=>boot.kind==='notifications'?startFeed():undefined,[]);
const [fileResource,setFileResource]=useState(boot.resource);
useEffect(()=>{const change=(e:any)=>setFileResource(e.detail);window.addEventListener('library-files-context',change);return()=>window.removeEventListener('library-files-context',change)},[]);
const preferenceRevision=useRef(0),attentionReply=useRef<any>(null);
const polling=useRef<ReturnType<typeof createNativeRefresh>|null>(null);
if(!polling.current)polling.current=createNativeRefresh((path,options)=>rpc('api',{path,fresh:options?.fresh===true,since:options?.since,keys:path==='/api/state'?chatKeys():undefined}),{state:setData,accounts:v=>{(window as any).LibraryNotifications?.observe(v);setAccounts(v.accounts||[]);setPendingNoticeAccounts(v.pending_accounts||[])},hosts:v=>setHosts(v.hosts||[]),runs:v=>{(window as any).LibraryNotifications?.observe(v);setRuns(v.runs||[])},attention:v=>{attentionReply.current=v;setAttention(v.items||[])},live:v=>setLive(v.sessions||[]),error:setError});
const refresh=useCallback(()=>polling.current!.refresh(true),[]);
useEffect(()=>{const controller=polling.current!;controller.setActive(true);const refreshListener=()=>void refresh(),visible=()=>{if(!document.hidden)void controller.refresh()};window.addEventListener('library-refresh',refreshListener);window.addEventListener('library-state',visible);document.addEventListener('visibilitychange',visible);void controller.refresh();const timer=setInterval(visible,5000);return()=>{controller.setActive(false);clearInterval(timer);window.removeEventListener('library-refresh',refreshListener);window.removeEventListener('library-state',visible);document.removeEventListener('visibilitychange',visible)}},[]);
useEffect(()=>{if(boot.kind==='sidebar'&&data)chimeForAttention(attention,soundEnabled(data.config,attentionReply.current))},[attention,Boolean(data)]);
useEffect(()=>{const change=(e:any)=>setPrefs(e.detail);window.addEventListener('library-preferences',change);return()=>window.removeEventListener('library-preferences',change)},[]);
useEffect(()=>{const p=preferences;const apply=()=>{document.documentElement.dataset.theme=p.theme==='system'?(document.body.classList.contains('vscode-dark')?'dark':'light'):p.theme};apply();const observer=new MutationObserver(apply);observer.observe(document.body,{attributes:true,attributeFilter:['class']});document.documentElement.style.setProperty('--reading-size','14px');document.documentElement.style.setProperty('--reading-line',String(p.lineHeight));document.documentElement.style.setProperty('--reading-width',p.readingWidth+'px');return()=>observer.disconnect()},[preferences]);
const open=(resource:any,destination?:string)=>void rpc('open',{resource,destination}).catch((e:any)=>setToast(e.message));const value:any={doubleClickMs:boot.doubleClickMs,sessionDetails:(id:string)=>rpc('sessionDetails',{id}).catch((e:any)=>setToast(e.message)),renameChat:async(key:string,title:string)=>{const value=await rpc('rename',{key,title});await refresh();return value},getModels:(context:any)=>rpc('models',context),confirm:(message:string,button:string)=>rpc('confirm',{message,button}),data,stateError:boot.kind==='sidebar'?error:'',connect:()=>rpc('endpoint'),accounts,pendingNoticeAccounts,hosts,runs,attention,liveSessions,preferences,open,refresh,notify:setToast,setPreferences:async(p:any)=>{const previous=preferences,next={...preferences,...p},revision=++preferenceRevision.current;setPrefs(next);try{await rpc('preferences',{value:next})}catch(error){if(preferenceRevision.current===revision)setPrefs(previous);setToast(error instanceof Error?error.message:String(error));throw error}},updateTitle:(resource:any,title:string)=>rpc('title',{title}),replace:(_panel:any,resource:any)=>void rpc('replace',{resource}).catch((e:any)=>setToast(e.message))};
return <WorkbenchContext.Provider value={value}><PathContextMenu/>{boot.kind==='chat'&&<ChatTabSync/>}{error&&boot.kind!=='sidebar'&&<div className="native-error" role="alert">{error}<button onClick={()=>void refresh()}>Retry</button><button onClick={()=>void rpc('endpoint')}>Connect Library</button></div>}{boot.kind==='sidebar'?<Sidebar hideFiles mode={mode} setMode={setMode} onContext={()=>{}}/>:boot.kind==='settings'?<Settings onEditorTool={tool=>rpc('advancedEditor',{tool})}/>:boot.kind==='accounts'?<AccountsPage onGitHubAccounts={connect=>rpc('githubAccounts',{connect})}/>:boot.kind==='notifications'?<NotificationsView/>:boot.kind==='projects'?<Projects {...value}/>:boot.kind==='recent'?<Recent {...value}/>:boot.kind==='overview'?<AgentOverview/>:boot.kind==='sessions'?<SessionsView/>:boot.kind==='files'?<FilesTab resource={fileResource}/>:<Conversation resource={boot.resource}/>} {toast&&<button className="native-toast" onClick={()=>setToast('')}>{toast}</button>}</WorkbenchContext.Provider>}
// The host stops every webview from taking drops once it sees a Library drag. Keeping a Files tab's
// drags inside the webview lets them land in another Files tab; other panes still open the file.
if(boot.kind==='files')for(const type of ['dragstart','drag','dragenter','dragover'])document.addEventListener(type,event=>event.stopPropagation());
installTooltips(document);installRunningIndicator(document);createRoot(document.getElementById('root')!).render(<WorkspaceScopeProvider enabled={['sidebar','projects','recent'].includes(boot.kind)}><App/></WorkspaceScopeProvider>);
