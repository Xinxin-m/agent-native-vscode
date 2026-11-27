import {MenuFlyout,OrderedMenu,type MenuItem} from './ui/OrderedMenu';
import React,{useState,useRef,useEffect,useCallback,createContext,useContext} from 'react';
import {ignoresChatFileDrop} from './open/chatFileDrag';
import {DockviewReact,type IDockviewPanelProps,type IDockviewPanelHeaderProps,type IDockviewHeaderActionsProps} from 'dockview-react';
import {PanelLeft,PanelRight,Plus,History,Columns2,Rows2,Maximize2,Minimize2,MoreHorizontal,ChevronDown,X,MessageSquare,FileText,Settings2,ArrowUpRight,Check,Search,Monitor,Copy,Folder} from 'lucide-react';
import {api,ApiError,getState,pollWhileVisible} from './api/client';
import {chimeForAttention,soundEnabled} from './state/approvalSound';
import {WorkbenchContext} from './state/context';
import type {Resource,Destination,Preferences} from './state/types';
import {resourceId,encodeResource,decodeResource} from './open/links';
import {providerLabel,isRootChat,historyChats,rowStatus,cutTitle} from './navigation/chats';
import {StatusMark} from './ui/StatusMark';
import {NotificationHost} from './notifications/PopupStack';
import {NotificationsPage,NotificationsPanel,NotificationsHistory} from './notifications/NotificationsPage';
import {startFeed,setPanel,usePanel} from './notifications/feed';
import {Sidebar,IconButton,relativeDate} from './navigation/Sidebar';
import {Settings} from './navigation/Settings';
import {Conversation} from './chat/Conversation';
import {ProjectMap} from './projectMap/ProjectMap';
import {ActivityRail} from './navigation/ActivityRail';
import {Projects} from './navigation/Projects';
import {Recent} from './navigation/Recent';
import {FileViewer} from './viewers/FileViewer';
import {viewerKind} from './viewers/helpers';
import {FilesTab} from './navigation/FileTree';
import {followMove,deletedBy} from './navigation/fileMoves';
import {reuseSnapshot,peerRoster} from './state/pollingSnapshots';
import {documentKey,snapshot,resolveFile,acceptTransferredDraft,useFileDocument,moveDocument} from './viewers/documents';
import {windowIdentity,viewIdentity,filePreviewGroup,fileTargetPane,createPaneNavigation,createWindowTransfers,createNarrowPaneController,dropWorkspaceResource,findOpenChat,conversationTransferSnapshot,acceptTransferredConversation} from './state/workspace';
import {conversationIdentity,conversations} from './state/conversations';
import {utilityWindow,useUtilityWindows} from './open/utilityWindows';
import {isPlainNewChat,unusedNewChat} from './open/newChat';
import {arrangeByRequest,paneBudget} from './state/customArrangement';
import {arrangeWorkspaceTabs} from './state/tabGrouping';
import {useChatActivity,markChatViewed,useSeenReporter} from './state/activity';
import {terminal} from './state/runs';
import {AgentOverview} from './chat/Overview';
import {SessionsView} from './navigation/Sessions';
import 'dockview-react/dist/styles/dockview.css';
import {PathContextMenu} from './open/PathContextMenu';
import './theme.css';
import './docking.css';

const groupColors = [
 ['blue','#467bb1'], ['green','#40815d'], ['purple','#8264a8'], ['orange','#a96630'],
 ['pink','#a25983'], ['cyan','#357e88'], ['red','#ac5656'], ['yellow','#917222'], ['grey','#686d79'],
].map(([id,value])=>({id,value,label:id[0].toUpperCase()+id.slice(1)}));

const ShellContext=createContext<any>(null);
const defaultPreferences:Preferences={theme:'system',fontSize:14,lineHeight:1.65,readingWidth:780,openDefault:'right',maxAutoPanes:3,filesTabWorkspace:'ask',defaultProvider:'ask',defaultModels:{},thinking:'collapsed',toolOutput:'folded',composerMax:null,foldComposer:true,sendKey:'enter',whileWorking:'queue',openAtLatest:true,tabGroups:'manual',groupFiles:false,confirmMoves:'some',showHidden:false,liveRefresh:15};
const load=(key:string,fallback:any)=>{try{return JSON.parse(localStorage.getItem(key)||'null')??fallback;}catch{return fallback;}};
function Panel({params,api:panelApi}:IDockviewPanelProps){const [visible,setVisible]=useState(panelApi.isVisible);useEffect(()=>{setVisible(panelApi.isVisible);const listener=panelApi.onDidVisibilityChange(e=>setVisible(e.isVisible));return()=>listener.dispose();},[panelApi]);const {data,open,refresh,notify}=useContext(WorkbenchContext);const {menu,groupChoices}=useContext(ShellContext);const resource={...params.resource,meta:{...params.resource.meta,viewId:panelApi.id,panelVisible:visible}} as Resource;if(resource.kind==='file')return <ErrorBoundary key={panelApi.id}><FileViewer resource={resource} onNavigate={target=>{
 panelApi.updateParameters({...params,resource:target});
 panelApi.setTitle(target.title||target.path?.split('/').at(-1)||'File');
 }}/></ErrorBoundary>;return <ErrorBoundary key={resourceId(resource)}>{resource.kind==='chat'?<Conversation resource={resource}/>:resource.kind==='settings'?<Settings initialTab={resource.meta?.settingsTab}/>:resource.kind==='notifications'?(resource.mode==='history'||!resource.mode&&resource.title==='History'?<NotificationsHistory/>:<NotificationsPage visible={visible}/>):resource.kind==='recent'?<Recent data={data} open={open} tabGroups={groupChoices}/>:resource.kind==='projects'?<Projects data={data} open={open} refresh={refresh} notify={notify}/>:resource.kind==='project'?<ProjectMap resource={resource} panelApi={panelApi}/>:resource.kind==='overview'?<AgentOverview/>:resource.kind==='sessions'?<SessionsView/>:resource.kind==='url'?<WebPage resource={resource}/>:resource.kind==='files'?<FilesTab params={params} panelApi={panelApi} onContext={menu}/>:<Welcome/>}</ErrorBoundary>;}
function WebPage({resource}:{resource:Resource}){return <div className="workspace-web-page"><div className="workspace-web-address"><span title="Some sites block embedded views. Open externally if the page does not load.">{resource.url}</span><small>Page blank? This site may require an external browser.</small><button onClick={()=>window.open(resource.url,'_blank','noopener,noreferrer')}>Open externally</button></div><iframe title={resource.title||resource.url} src={resource.url} sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads" referrerPolicy="no-referrer"/></div>;}
// Reports the chats in front of her (the active tab of each pane) as seen, only while this window is visible and focused.
function SeenReporter({dock,active,groups}:{dock:any;active:any;groups:number}){useSeenReporter(()=>(dock.current?.groups||[]).map((g:any)=>g.activePanel?.params?.resource).filter((r:any)=>r?.kind==='chat').map((r:any)=>r.key||conversations.peek(conversationIdentity(r))?.nativeKey).filter(Boolean),[active,groups]);return null;}
function Tab(props:IDockviewPanelHeaderProps){const wb=useContext(WorkbenchContext);const {menu}=useContext(ShellContext);const r=props.params.resource as Resource;const documentState=useFileDocument(r),activity=useChatActivity(r);useEffect(()=>{if(r.kind==='file'&&documentState.dirty&&props.params.preview)props.api.updateParameters({...props.params,preview:false});},[documentState.dirty,props.params.preview]);const [title,setTitle]=useState(props.api.title);useEffect(()=>props.api.onDidTitleChange(e=>setTitle(e.title)).dispose,[props.api]);
 const nativeKey=r.kind==='chat'?r.key||conversations.peek(conversationIdentity(r))?.nativeKey:'',listed=nativeKey?(wb.data?.chats||[]).find((c:any)=>c.key===nativeKey):null,fullTitle=listed?.title||title||(r.kind==='chat'?'New chat':''),shownTitle=r.kind==='chat'?cutTitle(fullTitle):title,status=rowStatus(activity.live,activity.unread,{...activity,failed:activity.failed||activity.recovery});
 // The chat's title from the state (the field the sidebar row shows) is the panel's title too, so pane lists follow a rename.
 useEffect(()=>{if(listed?.title&&listed.title!==props.api.title)props.api.setTitle(listed.title)},[listed?.title]);
 return <div className={'workbench-tab '+(props.params.preview?'preview ':'')+(status==='working'?'is-running ':'')} onContextMenu={e=>menu(e,r,props.api.id)} onDoubleClick={()=>props.api.updateParameters({...props.params,preview:false})} title={r.path||fullTitle}>{r.kind==='chat'?<StatusMark status={status} compact/>:<span className="tab-symbol">{r.kind==='settings'?<Settings2 size={14}/>:r.kind==='files'?<Folder size={14}/>:<FileText size={14}/>}</span>}<span className="tab-title">{shownTitle}</span>{r.kind==='file'&&documentState.dirty&&<span title="Unsaved changes" aria-label="Unsaved changes" style={{width:5,height:5,background:'currentColor',borderRadius:'var(--radius)',flexShrink:0}}/>}<button title="Close tab" aria-label={'Close '+fullTitle} onClick={e=>{e.preventDefault();e.stopPropagation();const row=e.currentTarget.closest('.dv-tabs-overflow-container .dv-tab');props.api.close();row?.remove();}}><X size={13}/></button></div>;}
// Tab titles and pane controls settle after Dockview restores the layout.
function useVisibleTabLabel(group:IDockviewHeaderActionsProps['group'],panelId?:string){
 useEffect(()=>{
  const strip=group.element.querySelector<HTMLElement>('.dv-tabs-container');
  if(!strip||!panelId)return;
  let frame=0;
  const reveal=()=>{
   cancelAnimationFrame(frame);
   frame=requestAnimationFrame(()=>{
    const tab=strip.querySelector<HTMLElement>(':scope > .dv-active-tab');
    if(!tab?.offsetWidth||!strip.clientWidth)return;
    const bounds=tab.getBoundingClientRect(),viewport=strip.getBoundingClientRect();
    if(bounds.left<viewport.left)strip.scrollLeft+=bounds.left-viewport.left;
    else if(bounds.right>viewport.right)strip.scrollLeft+=bounds.right-viewport.right;
   });
  };
  const sizes=new ResizeObserver(reveal);
  sizes.observe(strip);
  for(const tab of strip.children)sizes.observe(tab);
  return()=>{sizes.disconnect();cancelAnimationFrame(frame)};
 },[group,panelId]);
}
function GroupActions({containerApi,group,activePanel}:IDockviewHeaderActionsProps){const shell=useContext(ShellContext),corner=useCorner(group,containerApi,'right',[]);useVisibleTabLabel(group,activePanel?.id);return <div className="group-actions"><IconButton label="New chat" onClick={e=>{const r=e.currentTarget.getBoundingClientRect();shell.newChat(group.id,'tab',{x:r.left,y:r.bottom+4})}}><Plus size={16}/></IconButton><IconButton className="icon-button group-caret" label="Open in this pane" onClick={e=>shell.addMenu(e,group.id)}><ChevronDown size={13}/></IconButton><IconButton label="Split pane right" onClick={e=>{const r=e.currentTarget.getBoundingClientRect();shell.newChat(group.id,'right',{x:r.left,y:r.bottom+4})}}><Columns2 size={15}/></IconButton><IconButton label="Pane options" onClick={e=>shell.menu(e,activePanel?.params?.resource,activePanel?.id,undefined,true)}><MoreHorizontal size={16}/></IconButton>{corner&&<SecondaryToggle/>}</div>;}
// True while this pane's tab bar touches the workspace's top-left (or top-right) corner.
function useCorner(group:any,containerApi:any,side:'left'|'right',deps:any[]){const [corner,setCorner]=useState(false);
 useEffect(()=>{const check=()=>{const canvas=group.element.closest('.workbench-canvas');if(!canvas)return;const a=group.element.getBoundingClientRect(),b=canvas.getBoundingClientRect();setCorner(a.width>0&&Math.abs(a[side]-b[side])<2&&Math.abs(a.top-b.top)<2);};check();const d=containerApi.onDidLayoutChange(check);addEventListener('resize',check);return()=>{d.dispose();removeEventListener('resize',check)};},[group,containerApi,...deps]);
 return corner;}
// The show-sidebar button sits at the top left of the tab bar of the pane in the workspace's top-left corner.
function LeftActions({containerApi,group}:IDockviewHeaderActionsProps){const shell=useContext(ShellContext),corner=useCorner(group,containerApi,'left',[shell.sidebar]);
 return shell.sidebar||!corner?null:<IconButton className="icon-button sidebar-reveal" label="Show sidebar (⌘B)" onClick={()=>shell.toggleSidebar()}><PanelLeft size={16}/></IconButton>;}
const shortcut=/Mac|iPhone|iPad/.test(navigator.platform)?'⌘L':'Ctrl+L';
// The secondary sidebar toggle, at the top right like VS Code's.
function SecondaryToggle({float}:{float?:boolean}){const open=usePanel();return <IconButton className={'icon-button secondary-toggle'+(float?' secondary-toggle-float':'')} label={(open?'Hide':'Show')+' secondary sidebar ('+shortcut+')'} aria-pressed={open} onClick={()=>setPanel(v=>!v)}><PanelRight size={16}/></IconButton>;}
function Welcome(){const {newChat,history,showGroups}=useContext(ShellContext);return <div className="welcome"><div className="welcome-symbol"><MessageSquare size={28} strokeWidth={1.4}/></div><h1>A place for your work.</h1><div className="welcome-actions"><button onClick={()=>newChat()}><Plus size={17}/>New chat</button><button onClick={()=>history()}><History size={17}/>Open a conversation</button><button onClick={showGroups}>Tab groups</button></div></div>;}
class ErrorBoundary extends React.Component<any,{error:string}>{state={error:''};static getDerivedStateFromError(e:any){return {error:e.message};}render(){return this.state.error?<div className="viewer-error"><h2>This view could not load</h2><p>{this.state.error}</p><button onClick={()=>this.setState({error:''})}>Retry</button></div>:this.props.children;}}

export default function App(){
 const [data,setData]=useState<any>(null),[accounts,setAccounts]=useState<any[]>([]),[hosts,setHosts]=useState<any[]>([]),[runs,setRuns]=useState<any[]>([]),[attention,setAttention]=useState<any[]>([]),[liveSessions,setLiveSessions]=useState<any[]>([]),[error,setError]=useState('');
 const [preferences,setPrefs]=useState<Preferences>(()=>(()=>{const p={...defaultPreferences,...load('workbench-preferences',{})};if(!p.interfaceScaleVersion){p.fontSize=14;p.interfaceScaleVersion=1;}else if(![12,13,14,16,18].includes(p.fontSize))p.fontSize=14;if(['preview','browser-tab','browser-window','browser-current','default-browser'].includes(p.openDefault))p.openDefault='tab';return p;})());
 useEffect(()=>{
  const update=(event:StorageEvent)=>{if(event.key==='workbench-preferences'&&event.newValue){
   try{const incoming=JSON.parse(event.newValue);setPrefs(current=>JSON.stringify(current)===event.newValue?current:{...defaultPreferences,...incoming});}catch{}
  }};
  window.addEventListener('storage',update);return()=>window.removeEventListener('storage',update);
 },[]);
 const [pendingNoticeAccounts,setPendingNoticeAccounts]=useState<any[]>([]);
 const accountsAt=useRef(0);
 const readAccounts=useCallback((value:any)=>{
  accountsAt.current=Date.now();
  setAccounts(previous=>reuseSnapshot(previous,value.accounts||[]));setPendingNoticeAccounts(previous=>reuseSnapshot(previous,value.pending_accounts||[]));
 },[]);
 const [sidebar,setSidebar]=useState(()=>load('workbench-sidebar-visible',true)),[mode,setMode]=useState(()=>load('workbench-sidebar-mode','chats')),[width,setWidth]=useState(()=>{const saved=Number(load('workbench-sidebar-width',0));return saved&&![270,272].includes(saved)?saved:320;});
 const [toast,setToast]=useState<{text:string;id:number}|null>(null),[menuState,setMenu]=useState<any>(null),[historyState,setHistory]=useState<any>(null),[historyQuery,setHistoryQuery]=useState(''),[active,setActive]=useState<any>(null),[groupCount,setGroupCount]=useState(1),[windows,setWindows]=useState<any[]>([]),[narrow,setNarrow]=useState(()=>matchMedia('(max-width:800px)').matches),[paneOptions,setPaneOptions]=useState<any[]>([]),[navRevision,setNavRevision]=useState(0);
 const [historyDragging,setHistoryDragging]=useState(false);
 const [historyProvider,setHistoryProvider]=useState(()=>load('workbench-history-provider',''));
 useEffect(()=>{localStorage.setItem('workbench-history-provider',JSON.stringify(historyProvider));},[historyProvider]);
 const dock=useRef<any>(null),closed=useRef<Resource[]>([]),channel=useRef<BroadcastChannel>(null),openRef=useRef<any>(null),dropReplaceRef=useRef<((r:Resource,e:any)=>boolean)|null>(null),latestData=useRef(data),attentionReply=useRef<any>(null),latestPrefs=useRef(preferences);latestPrefs.current=preferences;
 const [identity]=useState(()=>{
  const url=new URL(location.href),navigation=(performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming)?.type||'navigate';
  const requested=url.searchParams.get('window')||history.state?.libraryWindow;
  const value=windowIdentity(navigation,requested,()=>crypto.randomUUID());
  if(!requested&&!utilityWindow&&!url.hash)value.restoreId=load('workbench-last-window','main');
  url.searchParams.delete('window');
  history.replaceState({...history.state,libraryWindow:value.id},'',url);
  return value;
 });
 const windowId=useRef(identity.id),pendingWindows=useRef(new Map<string,{resource:Resource;panelId:string}>());
 const layoutKey=useRef('workbench-layout-'+identity.id);
 const nav=useRef(createPaneNavigation(load(layoutKey.current+'-history',{}))),walking=useRef(new Set<string>()),previewTargets=useRef<Record<string,string>>({}),transfers=useRef<ReturnType<typeof createWindowTransfers>>(null),narrowController=useRef<ReturnType<typeof createNarrowPaneController>>(null);
 const saveHistory=()=>{if(utilityWindow)return;try{localStorage.setItem(layoutKey.current+'-history',JSON.stringify(nav.current.serialize()))}catch{}setNavRevision(v=>v+1)};
 const syncPaneOptions=()=>setPaneOptions((dock.current?.groups||[]).map((g:any)=>({id:g.id,title:g.activePanel?.api.title||'Empty pane'})));
 useEffect(()=>{const media=matchMedia('(max-width:800px)'),update=()=>{setNarrow(media.matches);narrowController.current?.sync(media.matches)};update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[]);
 useEffect(()=>{const media=matchMedia('(max-width:640px)'),close=()=>{if(media.matches)setSidebar(!decodeResource(location.hash));};close();media.addEventListener('change',close);return()=>media.removeEventListener('change',close)},[]);
 latestData.current=data;
 useEffect(()=>{let stopped=false;const refreshLive=()=>api('/api/live').then(value=>{if(!stopped)setLiveSessions(previous=>reuseSnapshot(previous,value.sessions||[]))}).catch(()=>{});const stop=pollWhileVisible(refreshLive,15000);return()=>{stopped=true;stop()};},[]);
 const notify=useCallback((s:string)=>{setToast(prev=>({text:s,id:(prev?.id??0)+1}));},[]);
 useEffect(()=>{const media=matchMedia('(prefers-color-scheme: dark)'),changed=()=>setPrefs(p=>p.theme==='system'?{...p}:p);media.addEventListener('change',changed);return()=>media.removeEventListener('change',changed);},[]);
 const runsRevision=useRef<any>(undefined),runsLoadedAt=useRef(0);
 const refresh=useCallback(async()=>{
   const values=await Promise.allSettled([getState(),api('/api/accounts'),api('/api/hosts'),api('/api/runs'),api('/api/attention')]);
   if(values[0].status==='fulfilled'){setData(values[0].value);setError('');}else setError(values[0].reason.message);
   if(values[1].status==='fulfilled')readAccounts(values[1].value);
   if(values[2].status==='fulfilled'){const next=values[2].value.hosts||[];setHosts(previous=>reuseSnapshot(previous,next));}
   if(values[3].status==='fulfilled'){const next=values[3].value.runs||[];setRuns(previous=>reuseSnapshot(previous,next));runsLoadedAt.current=Date.now();}
   if(values[4].status==='fulfilled'){attentionReply.current=values[4].value;const next=values[4].value.items||[];setAttention(previous=>reuseSnapshot(previous,next));}
 },[]);
 useEffect(()=>{let stopped=false,busy=false,timer:ReturnType<typeof setTimeout>,stateAt=0;void refresh();const check=async()=>{if(stopped||busy)return;clearTimeout(timer);busy=true;try{const r=await api('/api/revision');if(!stopped&&Date.now()-accountsAt.current>=60000){accountsAt.current=Date.now();void api('/api/accounts').then(value=>{if(!stopped)readAccounts(value)},()=>{});}if(!stopped)setError(r.error?'Library could not refresh: '+r.error:'');if(!stopped&&!r.error&&r.revision!==latestData.current?.revision&&Date.now()-stateAt>=5000){stateAt=Date.now();const value=await getState();if(!stopped){latestData.current=value;setData(value)}}if((r.runs!==undefined&&r.runs!==runsRevision.current)||(r.runs===undefined&&Date.now()-runsLoadedAt.current>=15000)){const value=await api('/api/runs');if(!stopped){setRuns(previous=>reuseSnapshot(previous,value.runs||[]));runsRevision.current=r.runs;runsLoadedAt.current=Date.now()}}}catch(e){if(!stopped)setError((e as Error).message)}finally{busy=false;if(!stopped&&!document.hidden)timer=setTimeout(check,2000)}};const wake=()=>{if(!document.hidden)void check()};timer=setTimeout(check,2000);document.addEventListener('visibilitychange',wake);window.addEventListener('focus',wake);return()=>{stopped=true;clearTimeout(timer);document.removeEventListener('visibilitychange',wake);window.removeEventListener('focus',wake)}},[refresh]);
 useEffect(()=>{if(latestData.current)chimeForAttention(attention,soundEnabled(latestData.current.config,attentionReply.current))},[attention,Boolean(data)]);
 useEffect(()=>startFeed(),[]);
 useEffect(()=>{let canceled=false;const check=()=>api('/api/attention').then(d=>{if(!canceled){attentionReply.current=d;setAttention(previous=>reuseSnapshot(previous,d.items||[]));}}).catch(()=>{});const stop=pollWhileVisible(check,4000);return()=>{canceled=true;stop();};},[]);
 useEffect(()=>{const theme=preferences.theme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):preferences.theme;document.documentElement.dataset.theme=theme;document.documentElement.style.setProperty('--reading-size','14px');document.body.style.zoom=String(preferences.fontSize/14);document.documentElement.style.setProperty('--reading-line',String(preferences.lineHeight));document.documentElement.style.setProperty('--reading-width',preferences.readingWidth+'px');localStorage.setItem('workbench-preferences',JSON.stringify(preferences));},[preferences]);
 useEffect(()=>{if(utilityWindow)return;localStorage.setItem('workbench-sidebar-visible',JSON.stringify(sidebar));localStorage.setItem('workbench-sidebar-width',JSON.stringify(width));localStorage.setItem('workbench-sidebar-mode',JSON.stringify(mode));},[sidebar,width,mode]);
 useEffect(()=>{
  if(utilityWindow)return;
  const bc=new BroadcastChannel('library-workbench-windows');channel.current=bc;const peers=new Map(),movingChats=new Map<string,string>(),movingDocuments=new Map<string,{text:string;version?:string}>();
  const coordinator=createWindowTransfers(windowId.current,m=>{if(m.type==='open'&&m.move&&m.resource?.kind==='chat'){const draft=conversations.get(conversationIdentity(m.resource));movingChats.set(m.request_id,conversationTransferSnapshot(draft));setTimeout(()=>movingChats.delete(m.request_id),7000);m={...m,conversationDraft:structuredClone(draft)};}if(m.type==='open'&&m.move&&m.resource?.kind==='file'){const draft=snapshot(documentKey(m.resource));movingDocuments.set(m.request_id,{text:draft.text,version:draft.baseVersion});setTimeout(()=>movingDocuments.delete(m.request_id),7000);if(draft.dirty)m={...m,documentDraft:{text:draft.text,baseText:draft.baseText,version:draft.baseVersion}};}bc.postMessage(m)},transfer=>{
   if(!transfer.move)return;const chatSent=movingChats.get(transfer.request_id);movingChats.delete(transfer.request_id);if(chatSent&&conversationTransferSnapshot(conversations.get(conversationIdentity(transfer.resource)))!==chatSent){notify('The other window opened the tab. Newer conversation edits remain in this window.');return;}const sent=movingDocuments.get(transfer.request_id);movingDocuments.delete(transfer.request_id);if(sent){const current=snapshot(documentKey(transfer.resource));if(current.text!==sent.text||current.baseVersion!==sent.version){notify('The other window opened the tab. Newer edits remain in this window.');return;}}const source=transfer.panelId&&dock.current?.getPanel(transfer.panelId);
   if(source&&viewIdentity(source.params.resource)===viewIdentity(transfer.resource)){source.api.close();notify('Tab moved to the other window.')}else notify('The other window opened the tab. The source changed and was retained.');
  },notify);transfers.current=coordinator;
  const syncPeers=()=>setWindows(previous=>reuseSnapshot(previous,peerRoster(peers)));
  const announce=()=>{syncPeers();bc.postMessage({type:'hello',id:windowId.current,title:document.title,restoreId:identity.restoreId,at:Date.now()});};
  bc.onmessage=async e=>{const m=e.data;if(!m||m.id===windowId.current)return;if(m.type==='hello'){const pending=pendingWindows.current.get(m.restoreId);if(pending){pendingWindows.current.delete(m.restoreId);coordinator.offer(m.id,pending.resource,pending.panelId,true);}peers.set(m.id,{id:m.id,title:m.title,at:m.at});syncPeers();}
   if(m.type==='ack'){coordinator.ack(m);return;}
   if(m.type==='drop-request'&&m.target===windowId.current){const panel=dock.current?.getPanel(m.panelId);if(panel)coordinator.offer(m.id,panel.params.resource,panel.id,true,m.placement);return;}
   if(m.type==='open'&&m.target===windowId.current){try{const opened=openRef.current?.(m.resource,'tab');if(!opened)throw Error('The destination workspace is not ready.');if(m.conversationDraft)acceptTransferredConversation(m.resource,m.conversationDraft);if(m.documentDraft)await acceptTransferredDraft(m.resource,m.documentDraft);if(m.placement){const group=dock.current?.groups.find(g=>g.id===m.placement.groupId);opened.api.moveTo({group,position:m.placement.position,index:m.placement.index});}window.focus();bc.postMessage({type:'ack',id:windowId.current,target:m.id,request_id:m.request_id,ok:true});}catch(e){bc.postMessage({type:'ack',id:windowId.current,target:m.id,request_id:m.request_id,ok:false,error:(e as Error).message});}}
  };announce();const timer=setInterval(announce,5000);return()=>{clearInterval(timer);coordinator.dispose();bc.close();};
 },[notify]);
 const open=useCallback((resource:Resource,destination?:Destination,groupId?:string)=>{if(matchMedia('(max-width:640px)').matches)setSidebar(false);
   if(matchMedia('(max-width:640px)').matches)setSidebar(false);
   const r={...resource};if(r.kind==='url'&&!/^https?:\/\//i.test(r.url||'')){notify('Only HTTP and HTTPS web pages can be opened.');return;}if(r.kind==='chat'&&!r.key&&!r.draftId)r.draftId=r.runId?'run-'+r.runId:crypto.randomUUID();
   if(r.kind==='files'&&r.root_id===undefined){const current=dock.current?.activePanel?.params?.resource;r.root_id=preferences.filesTabWorkspace==='all'?'all':current?.root_id||localStorage.getItem('library.files.workspace')||'';if(current?.kind==='file')r.meta={...r.meta,filesPath:current.path,filesHost:current.host_id||'mac'};}
   const target=destination||(['file','url'].includes(r.kind)?preferences.openDefault:'tab');
   const requestedWindow=crypto.randomUUID();const url=r.kind==='url'?r.url:location.origin+location.pathname+'?window='+requestedWindow+encodeResource(r);
   if(['browser-tab','browser-window','browser-current'].includes(target)){
     if(target==='browser-current'){location.href=url;return;}
     const win=window.open(url,'_blank',target==='browser-window'?'popup,width=1280,height=900':'');if(!win)notify('Your browser blocked the window. Allow pop-ups for Library, then try again.');else{win.opener=null;const source=findOpenChat(dock.current?.panels||[],r)||(dock.current?.panels||[]).find(p=>viewIdentity(p.params.resource)===viewIdentity(r));if(target==='browser-window'&&r.kind!=='url'&&source){pendingWindows.current.set(requestedWindow,{resource:r,panelId:source.id});setTimeout(()=>{if(pendingWindows.current.delete(requestedWindow))notify('The new window did not confirm receipt. The source tab is retained.');},10000);}}return;
   }
   if(['default-browser','default-browser-window','system','editor'].includes(target)){
     void (async()=>{
      let payload:any={url};
      if(r.kind==='file'){
       const file=await resolveFile(r);
       // A remote file stays on its host. A separate window reads it through Workbench.
       if(target!=='default-browser-window'||!file.host_id||['mac','local'].includes(file.host_id))
        payload={host_id:file.host_id,root_id:file.root_id,file_id:file.file_id,path:file.relative_path||'',page:r.page,anchor:r.anchor};
      }
      try{await api('/api/open_destination',{resource:payload,destination:target.replaceAll('-','_')});}
      catch(error){
       if(target!=='default-browser-window'||!(error instanceof ApiError)||error.status!==400||error.code!=='invalid_destination')throw error;
       const popup=window.open(url,'_blank','popup,width=1280,height=900');
       if(popup)popup.opener=null;
       else notify('Your browser blocked the window. Allow pop-ups for Library, then try again.');
      }
     })().catch(e=>notify(e.message));return;
   }
   const apiDock=dock.current;if(!apiDock)return;
   const id=viewIdentity(r),group=apiDock.groups.find(g=>g.id===groupId)||apiDock.activeGroup;
   const unused=target==='tab'&&isPlainNewChat(r)&&group?unusedNewChat(group.panels,p=>conversations.get(conversationIdentity(p))):undefined;if(unused){unused.api.setActive();return unused;}
   const isPdf=r.kind==='file'&&viewerKind(r.path||r.title||'')==='pdf';
   const defaultPreview=!destination&&r.kind==='file'&&['right','below','preview'].includes(target);
   let destinationGroup=group,position:any=group?{referenceGroup:group.id,direction:'within'}:undefined;
   if(defaultPreview&&target==='right'&&group){
    const choice=fileTargetPane(apiDock.groups.map(g=>({id:g.id,rect:g.element.getBoundingClientRect()})),group.id);
    destinationGroup='group' in choice?apiDock.groups.find(g=>g.id===choice.group):undefined;
    position=destinationGroup?{referenceGroup:destinationGroup.id,direction:'within'}:{referenceGroup:group.id,direction:'right'};
    const from=group.activePanel?.params?.resource;if(from?.kind==='chat')r.meta={...r.meta,backTo:{kind:'chat',key:from.key,draftId:from.draftId,title:group.activePanel.api.title}};
   }else if(defaultPreview&&group&&['right','below'].includes(target)){
    const positions=apiDock.groups.map(g=>({id:g.id,rect:g.element.getBoundingClientRect()}));
    const reusable=preferences.maxAutoPanes>0&&positions.length>=preferences.maxAutoPanes?filePreviewGroup(positions,group.id,target as 'right'|'below',undefined,preferences.maxAutoPanes):group.activePanel?.params?.resource?.kind==='file'?group.id:filePreviewGroup(positions,group.id,target as 'right'|'below',previewTargets.current[group.id],preferences.maxAutoPanes);
    destinationGroup=reusable?apiDock.groups.find(g=>g.id===reusable):undefined;
    position=destinationGroup?{referenceGroup:destinationGroup.id,direction:'within'}:{referenceGroup:group.id,direction:target};
   }else if(target==='right'||target==='below'){destinationGroup=undefined;position=group?{referenceGroup:group.id,direction:target}:undefined;}
   const preview=target==='preview'||defaultPreview;
   const found=findOpenChat(apiDock.panels,r)||(isPdf||['project','settings','sessions','files','notifications'].includes(r.kind)?apiDock.panels.find(p=>viewIdentity(p.params.resource)===id):null)||destinationGroup?.panels.find(p=>viewIdentity(p.params.resource)===id);
   if(found){if(r.kind==='chat'&&(groupId||target==='right'||target==='below')){found.api.moveTo({group:destinationGroup||group,position:target==='right'?'right':target==='below'?'bottom':'center'});}found.api.setActive();if(r.kind==='settings'&&r.title)found.api.setTitle(r.title);if(r.line||r.page||r.anchor||r.meta?.searchQuery!==undefined||r.meta?.focusComposer!==undefined||r.meta?.settingsTab!==undefined||r.meta?.filesPath!==undefined||r.meta?.fileSequence!==undefined)found.api.updateParameters({...found.params,resource:{...found.params.resource,...r,meta:{...found.params.resource.meta,...r.meta}}});if(!preview)found.api.updateParameters({...found.params,preview:false});return found;}
   const replaced:any[]=[];
   if(preview&&destinationGroup){for(const previous of [...destinationGroup.panels].filter(p=>p.params.preview&&p.params.resource.kind==='file')){if(snapshot(documentKey(previous.params.resource)).dirty)previous.api.updateParameters({...previous.params,preview:false});else replaced.push(previous);}}
   const title=r.title||r.url||r.path?.split('/').at(-1)||latestData.current?.chats?.find(c=>c.key===r.key)?.title||(r.kind==='chat'?'New chat':r.kind==='settings'?'Settings':r.kind==='notifications'?(r.mode==='history'?'History':'Notifications'):r.kind==='overview'?'Agent overview':r.kind==='files'?'Files':'Workspace');
   const added=apiDock.addPanel({id:id+':'+crypto.randomUUID().slice(0,8),component:'resource',title:r.mode==='diff'?title+' · Changes':title,params:{resource:r,preview},position});
   for(const previous of replaced)previous.api.close();
   const welcome=r.kind!=='welcome'&&apiDock.getPanel('welcome');if(welcome&&welcome.group===added.group)welcome.api.close();
   if(defaultPreview&&group)previewTargets.current[group.id]=added.group.id;
   return added;
 },[preferences.openDefault,preferences.maxAutoPanes,preferences.filesTabWorkspace,notify]);openRef.current=open;
 // New chat: the active chat's provider, else the default provider setting, else a two-item picker.
 const newChat=(groupId?:string,destination:Destination='tab',anchor?:{x:number,y:number},provider?:string)=>{
  const group=dock.current?.groups.find((g:any)=>g.id===groupId)||dock.current?.activeGroup,r=group?.activePanel?.params?.resource,choice=latestPrefs.current.defaultProvider;
  provider||=r?.kind==='chat'?conversations.get(conversationIdentity(r)).provider:choice!=='ask'?choice:undefined;
  if(!provider)return setAdd({x:Math.max(12,Math.min(anchor?.x??innerWidth/2-120,innerWidth-252)),y:anchor?.y??80,groupId:group?.id,destination,pick:true});
  open({kind:'chat',draftId:crypto.randomUUID(),title:'New chat',meta:{source:provider}},destination,group?.id);
 };
 const [addState,setAdd]=useState<{x:number,y:number,groupId:string,destination?:Destination,pick?:boolean}|null>(null);
 const addMenu=(e:any,groupId:string)=>{const rect=e.currentTarget.getBoundingClientRect();setAdd({x:Math.max(12,Math.min(rect.left,innerWidth-252)),y:rect.bottom+4,groupId});};
 // An open file follows a move or rename; unsaved text moves with it.
 useEffect(()=>{const follow=(e:any)=>{for(const p of dock.current?.panels||[]){const r=p.params.resource,next=followMove(r,e.detail.moves);if(!next)continue;const title=next.path.split('/').at(-1)||next.path,moved={...r,...next,file_id:undefined,title};moveDocument(r,moved);p.api.updateParameters({...p.params,resource:moved});p.api.setTitle(r.mode==='diff'?title+' · Changes':title);}
  // Tabs of deleted files close; one with unsaved edits stays open and shows that its file is gone.
  for(const p of [...(dock.current?.panels||[])])if(deletedBy(p.params.resource,e.detail.deleted||[])&&!snapshot(documentKey(p.params.resource)).dirty)p.api.close();};window.addEventListener('library-files-moved',follow);return()=>window.removeEventListener('library-files-moved',follow);},[]);
 const showMenu=(e:any,resource:Resource,panelId?:string,actions?:{label:string,disabled?:boolean,run:()=>void}[],pane=false)=>{e.preventDefault?.();e.stopPropagation?.();const rect=e.currentTarget?.getBoundingClientRect();setMenu({x:Math.min(e.clientX||rect?.left||100,innerWidth-255),y:Math.min(e.clientY||rect?.bottom||80,innerHeight-470),resource,panelId,actions,pane});};
 const showHistory=(groupId?:string,panelId?:string)=>{setHistory({groupId,panelId});setHistoryQuery('');};
 // Load the picked chat into the tab that asked; keep that tab if it holds a draft or a running turn.
 const pickHistory=(c:any)=>{const r:Resource={kind:'chat',key:c.key,title:c.title,meta:c},panel=historyState?.panelId&&dock.current?.getPanel(historyState.panelId);setHistory(null);
  const found=findOpenChat(dock.current?.panels||[],r);if(!panel||found)return void open(r,'tab',historyState?.groupId);
  const current=panel.params.resource,draft=current.kind==='chat'?conversations.get(conversationIdentity(current)):null;
  if(draft&&(draft.text?.trim()||draft.attachments?.length||runs.some(run=>run.id===draft.runId&&!terminal(run.status))))return void open(r,'tab',panel.group.id);
  panel.api.updateParameters({...panel.params,resource:r,preview:false});panel.api.setTitle(c.title||'Conversation');panel.api.setActive();nav.current.visit(panel.group.id,r);saveHistory();};
 // A chat dropped on the middle of a pane or on its current tab replaces that tab's chat; edges and the tab bar keep Dockview's split and new tab. A chat open elsewhere is focused (one tab per chat).
 dropReplaceRef.current=(r,e)=>{
  const group=e.group,active=group?.activePanel;if(r.kind!=='chat'||e.position!=='center'||!active||active.params.resource.kind!=='chat')return false;
  const target=e.nativeEvent?.target as Element|null,tab=target?.closest?.('.dv-tab'),bar=target?.closest?.('.dv-tabs-and-actions-container');
  if(bar&&!(tab&&e.index===group.panels.indexOf(active)))return false;
  const found=findOpenChat(dock.current?.panels||[],r);if(found){found.api.setActive();return true;}
  active.api.updateParameters({...active.params,resource:r,preview:false});active.api.setTitle(r.title||'Conversation');active.api.setActive();nav.current.visit(group.id,r);saveHistory();return true;
 };
 // Agent tree "Open in current tab" and its back arrow: swap this tab's chat; an already open chat is focused instead.
 const replaceChat=(panelId:string|undefined,r:Resource)=>{const panel=panelId?dock.current?.getPanel(panelId):undefined,found=findOpenChat(dock.current?.panels||[],r);if(!panel||(found&&found!==panel))return void open(r,'tab',panel?.group.id);
  panel.api.updateParameters({...panel.params,resource:r,preview:false});panel.api.setTitle(r.title||'Conversation');panel.api.setActive();nav.current.visit(panel.group.id,r);saveHistory();};
 const groupTabs=async(mode:'provider'|'project',includeFiles=false)=>{
  const apiDock=dock.current;if(!apiDock)return;
  let projects:any[]=[];if(mode==='project'){try{projects=(await api('/api/project_catalog')).projects||[]}catch{notify('Project catalog is unavailable.');return;}}
  arrangeWorkspaceTabs(apiDock,mode,includeFiles,latestData.current||{},projects);
 };
 const groupPaneTabs=(pane:IDockviewPanelHeaderProps['api']['group'],members=[...pane.panels])=>{
  const apiDock=dock.current;
  if(!apiDock)return;

  const otherGroups=apiDock.groups.flatMap(p=>apiDock.getTabGroups({groupId:p.id}));
  const color=groupColors.find(color=>otherGroups.every(group=>group.color!==color.id))||groupColors[0];
  let index=1;
  while([...otherGroups,...archives()].some(group=>group.label==='Group '+index))index++;
  const label=window.prompt('Group name','Group '+index)?.trim();
  if(!label)return;
  const group=apiDock.createTabGroup({groupId:pane.id,label,color:color.id});
  for(const member of members)apiDock.addPanelToTabGroup({groupId:pane.id,tabGroupId:group.id,panelId:member.id});
 };
 const [custom,setCustom]=useState<string|null>(null),[arranging,setArranging]=useState(false);
 const [catalog,setCatalog]=useState(false),[,refreshGroups]=useState(0),[arrangementError,setArrangementError]=useState('');
 const [workspace,setWorkspace]=useState('');
 const historyRows=(data?.chats||[]).filter(c=>!workspace||c.cwd===workspace);
 const filteredHistory=historyChats(historyRows,historyProvider,historyQuery);
 type SavedGroup={id:string,label:string,color?:string,tabs:{title:string,resource:Resource}[]};
 const archiveKey=layoutKey.current+'-closed-groups';
 const archives=():SavedGroup[]=>load(archiveKey,[]);
 const liveGroups=()=>dock.current?.groups.flatMap(pane=>dock.current!.getTabGroups({groupId:pane.id}).map(group=>({pane,group})))||[];
 function saveArchives(groups:SavedGroup[]){
  try{localStorage.setItem(archiveKey,JSON.stringify(groups));refreshGroups(v=>v+1);return true}
  catch{notify('Could not save the tab group. Browser storage is full.');return false}
 }
 function closeGroup(paneId:string,id:string){
  const entry=liveGroups().find(e=>e.pane.id===paneId&&e.group.id===id);
  if(!entry)return;
  const tabs=entry.group.panelIds.map(id=>dock.current!.getPanel(id)!);
  const saved={id:crypto.randomUUID(),label:entry.group.label,color:entry.group.color,tabs:tabs.map(p=>{
   const r=p.params.resource,key=r.kind==='chat'&&conversations.peek(conversationIdentity(r))?.nativeKey;
   return {title:p.api.title,resource:key?{...r,key}:r};
  })};
  if(saveArchives([...archives(),saved]))for(const tab of tabs)tab.api.close();
 }
 function reopenGroup(saved:SavedGroup){
  const api=dock.current!;
  let pane=api.activeGroup;
  const members=saved.tabs.map(({resource,title})=>{
   const existing=resource.kind==='chat'&&findOpenChat(api.panels,resource);
   const panel=existing||api.addPanel({id:crypto.randomUUID(),component:'resource',title,params:{resource,preview:false},position:pane?{referenceGroup:pane.id,direction:'within'}:undefined});
   pane||=panel.group;
   if(panel.group!==pane)panel.api.moveTo({group:pane});
   return panel;
  });
  const group=api.createTabGroup({groupId:pane.id,label:saved.label,color:saved.color});
  for(const panel of members)api.addPanelToTabGroup({groupId:pane.id,tabGroupId:group.id,panelId:panel.id});
  saveArchives(archives().filter(g=>g.id!==saved.id));
  members[0]?.api.setActive();
  return {pane,group};
 }
 const closeMenu=()=>setMenu(null);
 useEffect(()=>{const dismiss=(e:KeyboardEvent|PointerEvent)=>{if(e instanceof KeyboardEvent&&e.key!=='Escape')return;if(e instanceof PointerEvent&&(e.target as Element)?.closest('.context-menu,.history-dialog'))return;setMenu(null);setHistory(null);setAdd(null);};window.addEventListener('keydown',dismiss,true);window.addEventListener('pointerdown',dismiss,true);return()=>{window.removeEventListener('keydown',dismiss,true);window.removeEventListener('pointerdown',dismiss,true);};},[]);
 const updateTitle=useCallback((r:Resource,title:string)=>{for(const p of dock.current?.panels||[])if(viewIdentity(p.params.resource)===viewIdentity(r))p.api.setTitle(r.mode==='diff'?title+' · Changes':title);},[]);
 function ready({api:apiDock}:any){
   dock.current=apiDock;
   apiDock.onWillDragPanel(e=>{if('dataTransfer' in e.nativeEvent&&e.nativeEvent.dataTransfer){e.nativeEvent.dataTransfer.setData('application/library-resource',JSON.stringify(e.panel.params.resource));e.nativeEvent.dataTransfer.setData('application/library-transfer',JSON.stringify({source:windowId.current,panelId:e.panel.id}));e.nativeEvent.dataTransfer.effectAllowed='move';}});
   apiDock.onUnhandledDragOver(e=>{const n=e.nativeEvent as DragEvent;if(n.dataTransfer?.types.includes('application/library-resource')&&!ignoresChatFileDrop(n)&&!document.elementsFromPoint(n.clientX,n.clientY).some(el=>el.closest('.files-tab')))e.accept();});
   apiDock.onDidDrop(e=>{if(ignoresChatFileDrop(e.nativeEvent as DragEvent))return;const raw='dataTransfer' in e.nativeEvent&&e.nativeEvent.dataTransfer?.getData('application/library-resource');if(!raw)return;try{const resource=JSON.parse(raw);if(!['chat','file','url','project'].includes(resource.kind))return;e.nativeEvent.preventDefault();const transferRaw='dataTransfer' in e.nativeEvent&&e.nativeEvent.dataTransfer?.getData('application/library-transfer');if(transferRaw){const transfer=JSON.parse(transferRaw);if(transfer.source!==windowId.current){channel.current?.postMessage({type:'drop-request',id:windowId.current,target:transfer.source,panelId:transfer.panelId,placement:{groupId:e.group?.id,position:e.position,index:e.index}});return;}}if(dropReplaceRef.current?.(resource,e)){setHistory(null);setHistoryDragging(false);return;}dropWorkspaceResource(apiDock,openRef.current,resource,e);setHistory(null);setHistoryDragging(false);}catch{notify('The dragged item could not be opened.');}});
   const saved=load(layoutKey.current,utilityWindow?null:load('workbench-layout-'+identity.restoreId,null));
   let restored=false;
   if(saved)try{
    apiDock.fromJSON(saved);
    const seen=new Set<string>();
    for(const panel of [...apiDock.panels]){
     if(panel.params.resource?.kind!=='chat')continue;
     const key=conversationIdentity(panel.params.resource);
     if(seen.has(key))panel.api.close();
     else seen.add(key);
    }
    const focused=apiDock.groups.find(group=>group.id===saved.activeGroup);
    focused?.activePanel?.api.setActive();
    restored=apiDock.panels.length>0;
   }catch{notify('The saved arrangement could not be restored. Your drafts are still available.');}
   const link=decodeResource(location.hash);
   const reload=(performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming)?.type==='reload';
   if(link&&(!restored||!reload)){openRef.current(link,'tab');if(utilityWindow)document.title=(link.title||'Workspace')+' · Library';}
   else if(!restored)apiDock.addPanel({id:'welcome',component:'resource',title:'Workspace',params:{resource:{kind:'welcome'}}});
   for(const event of [apiDock.onDidCreateTabGroup,apiDock.onDidDestroyTabGroup,apiDock.onDidTabGroupChange])event(()=>refreshGroups(v=>v+1));
   setActive(apiDock.activePanel);setGroupCount(apiDock.groups.length);syncPaneOptions();
   for(const g of apiDock.groups)if(g.activePanel)nav.current.visit(g.id,g.activePanel.params.resource);saveHistory();
   narrowController.current=createNarrowPaneController(apiDock);narrowController.current.sync(matchMedia('(max-width:800px)').matches);
   const saveLayout=()=>{
    if(utilityWindow)return;
    try{localStorage.setItem(layoutKey.current,JSON.stringify(apiDock.toJSON()));if(!document.hidden)localStorage.setItem('workbench-last-window',JSON.stringify(windowId.current));}
    catch{notify('Layout storage is full. Close a few documents or free browser storage.');}
   };
   saveLayout();
   apiDock.onDidLayoutChange(()=>{saveLayout();setGroupCount(apiDock.groups.length);syncPaneOptions();});
   apiDock.onDidRemovePanel(p=>{queueMicrotask(()=>{if(!apiDock.getPanel(p.id)&&p.params.resource.kind!=='welcome')closed.current=[p.params.resource,...closed.current].slice(0,30);});});
   apiDock.onDidRemoveGroup(g=>{nav.current.remove(g.id);saveHistory();});
   apiDock.onDidActiveGroupChange(saveLayout);
   apiDock.onDidActivePanelChange(e=>{saveLayout();const p=e.panel;setActive(p);if(p){document.title=(p.api.title||'Workspace')+' · Library';if(!walking.current.has(p.group.id)){nav.current.visit(p.group.id,p.params.resource);saveHistory();}narrowController.current?.activate(p);syncPaneOptions();}});
 }
 function navigate(dir:number,groupId?:string){const group=dock.current?.groups.find(g=>g.id===groupId)||dock.current?.activeGroup;if(!group)return;const resource=nav.current.step(group.id,dir);if(!resource)return;walking.current.add(group.id);try{open(resource,'tab',group.id);}finally{walking.current.delete(group.id);saveHistory();}}
 const canNavigate=(groupId:string,dir:number)=>nav.current.can(groupId,dir);
 useEffect(()=>{const listener=(e:KeyboardEvent)=>{if(e.defaultPrevented||!(e.metaKey||e.ctrlKey)||e.isComposing||(e.target as Element)?.closest?.('.fv-rendered-edit [contenteditable]'))return;if(e.key.toLowerCase()==='b'){e.preventDefault();setSidebar(v=>!v);}if(e.key.toLowerCase()==='l'&&!e.shiftKey&&!e.altKey&&(shortcut==='⌘L'?e.metaKey&&!e.ctrlKey:e.ctrlKey&&!e.metaKey)){e.preventDefault();setPanel(v=>!v);}if(e.shiftKey&&e.key.toLowerCase()==='t'){e.preventDefault();const r=closed.current.shift();if(r)openRef.current(r,'tab');}if(e.shiftKey&&e.key.toLowerCase()==='e'){e.preventDefault();openRef.current({kind:'files',title:'Files'},'tab');}if(e.key==='\\'){e.preventDefault();newChat(undefined,e.shiftKey?'below':'right');}if(e.altKey&&e.key.toLowerCase()==='g'){e.preventDefault();void groupTabs('project',e.shiftKey);}};window.addEventListener('keydown',listener);return()=>window.removeEventListener('keydown',listener);},[]);
 function resize(e:React.PointerEvent){e.preventDefault();const start=e.clientX,original=width;const move=(event:PointerEvent)=>setWidth(Math.min(430,Math.max(210,original+event.clientX-start)));const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);}
 const menuAction=(fn:()=>void)=>()=>{closeMenu();fn();};
 const panel=menuState?.panelId&&dock.current?.getPanel(menuState.panelId);
 const groupChoices=[
  ...liveGroups().map(({pane,group})=>({id:'open:'+pane.id+':'+group.id,label:group.label,saved:false,select:()=>{
   group.expand();
   const activeMember=group.panelIds.includes(pane.activePanel?.id)?pane.activePanel?.id:group.panelIds[0];
   if(activeMember)dock.current!.getPanel(activeMember)?.api.setActive();
  }})),
  ...archives().map(saved=>({id:'saved:'+saved.id,label:saved.label,saved:true,select:()=>reopenGroup(saved)}))
 ];
 const {openPage,tabGroups:utilityGroups}=useUtilityWindows(open,groupChoices,preferences.utilityPopups!==false,identity.id,notify);
 function command(id:string,label:string,action:()=>void,{disabled=false,separator=false,icon}:{
  disabled?:boolean,separator?:boolean,icon?:React.ReactNode
 }={}):MenuItem{
  return {id,separator,content:<button role="menuitem" disabled={disabled} onClick={action}>{icon}{label}</button>};
 }
 const directions=[['right','Open beside'],['below','Open below']] as const;
 const addItems:MenuItem[]=addState?.pick?[
  command('claude','Claude Code',()=>{setAdd(null);newChat(addState.groupId,addState.destination,undefined,'claude')},{icon:<MessageSquare size={14}/>}),
  command('codex','Codex',()=>{setAdd(null);newChat(addState.groupId,addState.destination,undefined,'codex')},{icon:<MessageSquare size={14}/>})
 ]:addState?[
  ...directions.map(([dest,label])=>command(dest,label,()=>{setAdd(null);newChat(addState.groupId,dest,{x:addState.x,y:addState.y})})),
  command('folder','Open Folder…',()=>{setAdd(null);open({kind:'files',title:'Open workspace',root_id:''},'tab',addState.groupId)},{icon:<Folder size={14}/>}),
  command('history','Conversation history',()=>{setAdd(null);showHistory(addState.groupId)}),
  {id:'groups',content:<MenuFlyout label="Tab Groups">{groupChoices.length?groupChoices.map(group=><button role="menuitem" key={group.id} onClick={()=>{setAdd(null);group.select()}}>{group.label}{group.saved&&<small>Closed</small>}</button>):<span className="empty-note">No tab groups yet.</span>}</MenuFlyout>}
 ]:[];
 const paneItems:MenuItem[]=menuState?.pane&&panel?[
  command('close-pane','Close pane',menuAction(()=>[...panel.group.panels].forEach(p=>p.api.close()))),
  command('tab-groups','Tab groups',menuAction(()=>setCatalog(true))),
  command('maximize',dock.current!.hasMaximizedGroup()?'Restore pane':'Maximize pane',
   menuAction(()=>dock.current!.hasMaximizedGroup()?dock.current!.exitMaximizedGroup():dock.current!.maximizeGroup(panel)),
   {icon:dock.current!.hasMaximizedGroup()?<Minimize2 size={14}/>:<Maximize2 size={14}/>}),
  {id:'arrange',content:<MenuFlyout label="Arrange automatically"><OrderedMenu name="arrange" items={[
   command('provider','By agent and files',menuAction(()=>{void groupTabs('provider')})),
   command('project','Chats by project',menuAction(()=>{void groupTabs('project')})),
   command('project-files','Chats and files by project',menuAction(()=>{void groupTabs('project',true)})),
   command('custom','Custom…',menuAction(()=>{setArrangementError('');setCustom('')}),{disabled:arranging})
  ]}/></MenuFlyout>}
 ]:[];
 const tabItems:MenuItem[]=[];
 if(menuState&&!menuState.pane&&menuState.resource){
  const resource=menuState.resource as Resource;
  const groups=liveGroups();
  const savedGroups=archives();
  if(panel){
   tabItems.push(
    command('group-tab','New group with this tab',menuAction(()=>groupPaneTabs(panel.group,[panel]))),
    command('group-pane','New tab group from pane',menuAction(()=>groupPaneTabs(panel.group)))
   );
   if(groups.length||savedGroups.length)tabItems.push({id:'add-group',content:<MenuFlyout label="Add tab to group">
    {[...groups.map(entry=>({id:entry.pane.id+':'+entry.group.id,label:entry.group.label,get:()=>entry})),
      ...savedGroups.map(saved=>({id:saved.id,label:saved.label,get:()=>reopenGroup(saved)}))].map(item=>
      <button role="menuitem" key={item.id} onClick={menuAction(()=>{
       const {pane,group}=item.get();
       if(panel.group!==pane)panel.api.moveTo({group:pane});
       dock.current!.addPanelToTabGroup({groupId:pane.id,tabGroupId:group.id,panelId:panel.id});
      })}>{item.label}</button>)}
   </MenuFlyout>});
   if(dock.current!.getTabGroupForPanel({groupId:panel.group.id,panelId:panel.id}))
    tabItems.push(command('ungroup','Remove tab from group',menuAction(()=>dock.current!.removePanelFromTabGroup({groupId:panel.group.id,panelId:panel.id}))));
  }
  const otherPanes=dock.current?.groups.filter(g=>g.id!==panel?.group.id)||[];
  if(otherPanes.length>Number(!panel))tabItems.push({id:'move-pane',content:<MenuFlyout label="Move to pane">
   {otherPanes.map(g=><button role="menuitem" key={g.id} onClick={menuAction(()=>panel?panel.api.moveTo({group:g}):open(resource,'tab',g.id))}>{g.activePanel?.api.title||'Empty pane'}</button>)}
  </MenuFlyout>});
  if(resource.kind==='file'||resource.kind==='chat'&&resource.key)tabItems.unshift(command('copy-path','Copy Path',menuAction(()=>{
   void (async()=>{
    const path=resource.kind==='file'?(resource.path||(await resolveFile(resource)).path):(await api('/api/session/reference?key='+encodeURIComponent(resource.key!))).transcript_path;
    if(!path)throw Error('The selected tab has no file path.');
    await navigator.clipboard.writeText(path);notify('Path copied');
   })().catch(error=>notify(error.message||'Clipboard is unavailable'));
  })));
  for(const item of menuState.actions||[])tabItems.push(command('action:'+item.label,item.label,menuAction(item.run),{disabled:item.disabled}));
  for(const [dest,label] of directions)tabItems.push(command(dest,label,menuAction(()=>open(resource,dest,panel?.group.id)),{separator:dest==='right'&&!!tabItems.length}));
  if(resource.kind==='url'||resource.kind==='file')tabItems.push(command('external','Open in Default Browser',menuAction(()=>open(resource,'default-browser-window',panel?.group.id))));
  if(resource.kind!=='url')tabItems.push(command('new-window','Open in new window',menuAction(()=>open(resource,'browser-window',panel?.group.id)),{separator:true}));
  if(windows.length)for(const move of panel?[false,true]:[false]){
   const label=move?'Move to existing window':'Open in existing window';
   tabItems.push({id:move?'move-window':'existing-window',content:<MenuFlyout label={label}>
    {windows.map(w=><button role="menuitem" key={w.id} onClick={menuAction(()=>transfers.current?.offer(w.id,resource,menuState.panelId,move||resource.kind==='chat'&&!!panel))}>{w.title}</button>)}
   </MenuFlyout>});
  }
 }
 return <WorkbenchContext.Provider value={{data,stateError:error,accounts,pendingNoticeAccounts,hosts,runs,attention,liveSessions,preferences,open:openPage,notify,refresh,setPreferences:patch=>setPrefs(p=>({...p,...patch})),updateTitle,history:(panelId:string)=>showHistory(undefined,panelId),replace:replaceChat,renameChat:async(key:string,title:string)=>{const value=await api('/api/chat_state',{action:'rename',key,title});await refresh();return value.title}}}><ShellContext.Provider value={{showGroups:()=>setCatalog(true),groupChoices:utilityGroups,menu:showMenu,addMenu,newChat,history:showHistory,sidebar,toggleSidebar:()=>setSidebar(v=>!v),navigate,canNavigate,navRevision}}>
 <PathContextMenu/><SeenReporter dock={dock} active={active} groups={groupCount}/><div className={'app '+(utilityWindow?'utility-window sidebar-hidden':!sidebar?'sidebar-hidden':'')} style={{'--sidebar-width':width+'px'} as any}>
  {!utilityWindow&&<ActivityRail mode={mode} sidebar={sidebar} onSelect={next=>{setMode(next);setSidebar(true)}}/>}
  {sidebar&&!utilityWindow&&<><Sidebar mode={mode} setMode={setMode} onContext={showMenu} onDismiss={()=>setSidebar(false)}/><button className="sidebar-scrim" aria-label="Dismiss navigation overlay" onClick={()=>setSidebar(false)}/></>}
  {sidebar&&!utilityWindow&&<div role="separator" aria-label="Resize navigation" aria-orientation="vertical" className="sidebar-resizer" onPointerDown={resize} tabIndex={0} onKeyDown={e=>{if(e.key==='ArrowLeft')setWidth(w=>Math.max(210,w-20));if(e.key==='ArrowRight')setWidth(w=>Math.min(430,w+20));}}/>}
  <main className="workspace">
   {error&&!sidebar&&<div className="connection-error">{error}<button onClick={refresh}>Retry connection</button></div>}
   {narrow&&paneOptions.length>1&&<div className="narrow-pane-selector"><label>Pane <select aria-label="Active pane" value={active?.group?.id||''} onChange={e=>{const group=dock.current?.groups.find(g=>g.id===e.target.value);if(group?.activePanel){group.activePanel.api.setActive();narrowController.current?.activate(group.activePanel);}}}>{paneOptions.map((g,i)=><option value={g.id} key={g.id}>{i+1} · {g.title}</option>)}</select></label></div>}
   <div className="workbench-canvas">
    {!groupCount&&<SecondaryToggle float/>}{!sidebar&&!groupCount&&<IconButton className="icon-button sidebar-reveal sidebar-reveal-float" label="Show sidebar (⌘B)" onClick={()=>setSidebar(true)}><PanelLeft size={16}/></IconButton>}
    <DockviewReact tabGroupColors={groupColors} className="dockview-theme-workbench" components={{resource:Panel}} defaultTabComponent={Tab} rightHeaderActionsComponent={GroupActions} prefixHeaderActionsComponent={LeftActions} onReady={ready} getTabGroupChipContextMenuItems={({tabGroup,group,api})=>['rename','colorPicker','collapse',{label:'Close group',action:()=>closeGroup(group.id,tabGroup.id)},{label:'Dissolve group',action:()=>api.dissolveTabGroup({groupId:group.id,tabGroupId:tabGroup.id})}]} watermarkComponent={Welcome} disableFloatingGroups={false}/>
   </div>
  </main>
  <NotificationsPanel/>
 </div>
 <NotificationHost status={toast} dismissStatus={()=>setToast(null)} openPane={()=>openPage({kind:'notifications',title:'Notifications'},'tab')}/>
 {addState&&<><div className="menu-backdrop" onPointerDown={()=>setAdd(null)}/><div className="context-menu reorderable-menu" role="menu" aria-label={addState.pick?'New chat':'Open in this pane'} style={{left:addState.x,top:addState.y}}><OrderedMenu key={String(!!addState.pick)} name={addState.pick?'provider':'plus'} items={addItems}/></div></>}
 {menuState&&<><div className="menu-backdrop" onPointerDown={closeMenu}/><div className="context-menu reorderable-menu" role="menu" aria-label={menuState.pane?'Pane options':'Tab actions'} style={{left:menuState.x,top:Math.max(12,menuState.y)}}>
  <OrderedMenu key={String(menuState.pane)} name={menuState.pane?'pane':panel?'tab':'resource'} first={menuState.pane?undefined:'copy-path'} items={menuState.pane?paneItems:tabItems}/>
 </div></>}
 {custom!==null&&<div className="dialog-backdrop arrangement-backdrop" onPointerDown={()=>{if(!arranging)setCustom(null)}}><form className="arrangement-dialog" aria-labelledby="arrangement-title" aria-modal="true" role="dialog" onPointerDown={e=>e.stopPropagation()} onClick={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Escape'&&!arranging)setCustom(null)}} onSubmit={async e=>{
  e.preventDefault();if(arranging||!custom.trim())return;setArrangementError('');setArranging(true);
  try{await arrangeByRequest(dock.current!,custom,latestData.current,groupColors.map(c=>c.id));setCustom(null)}
  catch(error){setArrangementError((error as Error).message)}finally{setArranging(false)}
 }}>
  <h2 id="arrangement-title">Arrange open tabs</h2>
  <label htmlFor="arrangement-request">How should the tabs be grouped?</label>
  <textarea id="arrangement-request" autoFocus required value={custom} disabled={arranging} onChange={e=>setCustom(e.target.value)} placeholder="Keep each project task in one pane."/>
  <small>{paneBudget(dock.current!)===1?'Sonnet will use one pane.':`Sonnet will use up to ${paneBudget(dock.current!)} panes at this window size.`}</small>
  {arrangementError&&<p className="arrangement-error" role="alert">{arrangementError}</p>}
  <footer><button type="button" disabled={arranging} onClick={()=>setCustom(null)}>Cancel</button><button type="submit" className="primary" disabled={arranging||!custom.trim()}>{arranging?'Arranging…':'Arrange tabs'}</button></footer>
 </form></div>}
 {catalog&&<div className="dialog-backdrop" onPointerDown={()=>setCatalog(false)}><div className="history-dialog" role="dialog" aria-label="Tab groups" onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Escape')setCatalog(false)}}>
  <div className="history-search"><strong>Tab groups</strong><button autoFocus onClick={()=>setCatalog(false)}>Done</button></div>
  <div className="group-directory">{liveGroups().map(({pane,group})=><div key={group.id}><strong>{group.label}</strong><small>{group.size} open tabs</small><button onClick={()=>{group.expand();dock.current!.getPanel(group.panelIds[0])?.api.setActive();setCatalog(false)}}>Show</button><button onClick={()=>{closeGroup(pane.id,group.id);setNavRevision(v=>v+1)}}>Close group</button></div>)}
  {archives().map(saved=><div key={saved.id}><strong>{saved.label}</strong><small>{saved.tabs.length} saved tabs</small><button onClick={()=>{reopenGroup(saved);setCatalog(false)}}>Reopen</button><button onClick={()=>{saveArchives(archives().filter(g=>g.id!==saved.id));setNavRevision(v=>v+1)}}>Delete group</button></div>)}
  {!liveGroups().length&&!archives().length&&<p>No tab groups yet.</p>}</div>
 </div></div>}

 {historyState&&<div className="dialog-backdrop" style={historyDragging?{opacity:0,pointerEvents:'none'}:undefined} onPointerDown={()=>setHistory(null)}><div className="history-dialog" role="dialog" aria-modal="true" aria-label="Conversation history" onPointerDown={e=>e.stopPropagation()}><div className="history-search"><Search size={18}/><input autoFocus placeholder="Find a conversation…" aria-label="Find a conversation" value={historyQuery} onChange={e=>setHistoryQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Escape')setHistory(null);}}/><IconButton label="Close history" onClick={()=>setHistory(null)}><X size={17}/></IconButton></div><div className="history-providers" role="group" aria-label="Filter history by agent">{[['','All agents'],['codex','Codex'],['claude','Claude']].map(([value,label])=><button key={value} aria-pressed={historyProvider===value} onClick={()=>setHistoryProvider(value)}>{label}</button>) }<select aria-label="Filter by workspace" value={workspace} onChange={e=>setWorkspace(e.target.value)}><option value="">All workspaces</option>{Array.from(new Set<string>((data?.chats||[]).map(c=>c.cwd).filter(Boolean))).sort().map(path=><option key={path} value={path}>{path}</option>)}</select></div><div className="history-list">{filteredHistory.length===0&&<p className="empty-note" role="status">No matching conversations.</p>}{filteredHistory.map(c=><button key={c.key} draggable onDragStart={e=>{e.dataTransfer.setData('application/library-resource',JSON.stringify({kind:'chat',key:c.key,title:c.title,meta:c}));e.dataTransfer.effectAllowed='copyMove';setHistoryDragging(true);}} onDragEnd={()=>setHistoryDragging(false)} onClick={()=>pickHistory(c)}><MessageSquare size={16}/><span>{c.title}<small>{providerLabel(c)}{c.cwd?' · '+c.cwd:''}</small></span><time>{relativeDate(c.ts_last)}</time></button>)}</div></div></div>}
 </ShellContext.Provider></WorkbenchContext.Provider>;
}
