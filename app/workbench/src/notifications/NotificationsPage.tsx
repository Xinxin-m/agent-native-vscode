import {useEffect,useRef,useState} from 'react';
import {X,Bug} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {useFeed,actMany,act,includeArchived,usePanel,setPanel,isWaiting,isFailed,debugActive,historyRows,byDay,type Item,type HistoryTab} from './feed';
import {NotificationCard} from './NotificationCard';
import {openItemChat} from './open';
import './notifications.css';

// While a surface shows the open items, they count as seen.
function useMarkSeen(visible:boolean){
 const feed=useFeed(),sent=useRef(new Set<string>());
 useEffect(()=>{
  if(!visible)return;
  const ids=feed.items.filter(i=>i.state==='open'&&!i.seen&&!sent.current.has(i.id)).map(i=>i.id);
  if(!ids.length)return;
  ids.forEach(id=>sent.current.add(id));
  actMany(ids,'seen').catch(()=>ids.forEach(id=>sent.current.delete(id)));
 },[visible,feed.revision]);
}

// One click sends a debug agent to every debuggable item of the group that has none running.
function DebugAll({rows,label}:{rows:Item[];label:string}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const targets=rows.filter(i=>i.debuggable),idle=targets.filter(i=>!debugActive(i));
 if(!targets.length)return null;
 async function run(){
  setBusy(true);setError('');
  const failed:string[]=[];
  for(const i of idle){try{await act(i,'debug')}catch(e:any){if(e.status!==409)failed.push(e.message||'Debug did not start')}}
  if(failed.length)setError(failed.length===1?failed[0]:`${failed.length} debugs did not start: ${failed[0]}`);
  setBusy(false);
 }
 const name='Debug all: '+label.toLowerCase();
 return <><button type="button" className="ngroup-debug" aria-label={name} title={idle.length?`Debug all (${idle.length})`:'A debug is running for every item'} disabled={busy||!idle.length} onClick={()=>void run()}><Bug size={14}/></button>
  {error&&<span className="ngroup-error" role="alert">{error}</span>}</>;
}

// Open items in groups: the Notifications pane and the secondary sidebar show this same list.
function OpenGroups({onOpenChat}:{onOpenChat:(i:Item)=>void}){
 const feed=useFeed(),items=feed.items.filter(i=>i.state==='open');
 const groups:[string,Item[],boolean][]=[['Waiting for your decision',items.filter(isWaiting),true],['Failed',items.filter(isFailed),true],['Other',items.filter(i=>!isWaiting(i)&&!isFailed(i)),false]];
 return <>
  {feed.error&&<p className="notifications-note" role="status">Can't reach the Library server. Showing the last list.</p>}
  {feed.warnings.length>0&&<p className="notifications-note" role="status">Some sources could not be read: {feed.warnings.join('; ')}</p>}
  {groups.filter(g=>g[1].length).map(([label,rows,debug])=><section key={label} className="ngroup" aria-label={label}>
   <div className="ngroup-head"><h3>{label}</h3><span className="notifications-count">{rows.length}</span>{debug&&<DebugAll rows={rows} label={label}/>}</div>
   {rows.map(i=><NotificationCard key={i.id} item={i} variant="page" onOpenChat={onOpenChat}/>)}
  </section>)}
  {!items.length&&feed.mode!=='loading'&&<p className="notifications-empty">Nothing needs you.</p>}
 </>;
}

// The secondary sidebar: toggled with Cmd+L (Ctrl+L) or its button at the top right; a full-width sheet below 700 px.
export function NotificationsPanel(){
 const wb=useWorkbench(),open=usePanel(),{counts}=useFeed();
 useMarkSeen(open);
 if(!open)return null;
 const openChat=(i:Item)=>{if(matchMedia('(max-width:699px)').matches)setPanel(false);openItemChat(wb,i)};
 return <aside className="notifications-panel" aria-label="Secondary sidebar: notifications" onKeyDown={e=>{if(e.key==='Escape'&&!(e.target as HTMLElement).closest('input,textarea')){e.stopPropagation();setPanel(false)}}}>
  <div className="npanel-head"><h2>Notifications</h2>{counts.open>0&&<span className="notifications-count">{counts.open} open</span>}<button type="button" className="npanel-x" aria-label="Close secondary sidebar" title="Close (⌘L)" onClick={()=>setPanel(false)}><X size={16}/></button></div>
  <div className="npanel-list"><OpenGroups onOpenChat={openChat}/></div>
  <div className="npanel-foot"><button type="button" className="npanel-history" onClick={()=>wb.open({kind:'notifications',mode:'history',title:'History'},'tab')}>History</button></div>
 </aside>;
}

// The Notifications pane (an editor tab), opened by the bell, "N more" and the phone bar.
export function NotificationsPage({visible=true}:{visible?:boolean}){
 const wb=useWorkbench();
 useMarkSeen(visible);
 return <div className="notifications-page"><div className="notifications-inner">
  <div className="notifications-title"><h1>Notifications</h1><button type="button" className="npanel-history" onClick={()=>wb.open({kind:'notifications',mode:'history',title:'History'},'tab')}>History</button></div>
  <div className="notifications-list"><OpenGroups onOpenChat={i=>openItemChat(wb,i)}/></div>
 </div></div>;
}

const TABS:[HistoryTab,string][]=[['agents','Agents'],['app','App'],['failed','Failed']];
// History: answered and archived items, one tab per message type, grouped by day, newest first.
export function NotificationsHistory(){
 const wb=useWorkbench(),feed=useFeed();
 const [tab,setTab]=useState<HistoryTab>(()=>{const v=localStorage.getItem('workbench-history-tab');return v==='app'||v==='failed'?v:'agents'});
 useEffect(()=>{includeArchived(true);return()=>includeArchived(false)},[]);
 useEffect(()=>{localStorage.setItem('workbench-history-tab',tab)},[tab]);
 const tabs=TABS.filter(([id])=>id!=='failed'||historyRows(feed.items,'failed').length>0),current=tabs.some(t=>t[0]===tab)?tab:'agents';
 const days=byDay(historyRows(feed.items,current));
 return <div className="notifications-page"><div className="notifications-inner">
  <h1>History</h1>
  <div className="notifications-tabs" role="tablist" aria-label="Message type">{tabs.map(([id,label])=><button key={id} type="button" role="tab" aria-selected={current===id} onClick={()=>setTab(id)}>{label}<span className="notifications-count">{historyRows(feed.items,id).length}</span></button>)}</div>
  {feed.error&&<p className="notifications-note" role="status">Can't reach the Library server. Showing the last list.</p>}
  <div className="notifications-list" role="tabpanel">
   {days.map(([day,rows])=><section key={day} className="nday" aria-label={day}><h3>{day}</h3>{rows.map(i=><NotificationCard key={i.id} item={i} variant="page" onOpenChat={x=>openItemChat(wb,x)}/>)}</section>)}
   {!days.length&&feed.mode!=='loading'&&<p className="notifications-empty">Nothing here yet.</p>}
  </div>
 </div></div>;
}
