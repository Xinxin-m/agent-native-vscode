import {useEffect,useRef} from 'react';
import {X} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {useFeed,actMany,includeArchived,usePanel,setPanel,isWaiting,isFailed,type Item} from './feed';
import {NotificationCard} from './NotificationCard';
import {openItemChat} from './open';
import './notifications.css';

// The one notifications surface: a side panel beside the chat (a full-width sheet below 700 px).
export function NotificationsPanel(){
 const wb=useWorkbench(),feed=useFeed(),open=usePanel();
 const sent=useRef(new Set<string>());
 // While the panel is open, what is open counts as seen.
 useEffect(()=>{
  if(!open)return;
  const ids=feed.items.filter(i=>i.state==='open'&&!i.seen&&!sent.current.has(i.id)).map(i=>i.id);
  if(!ids.length)return;
  ids.forEach(id=>sent.current.add(id));
  actMany(ids,'seen').catch(()=>ids.forEach(id=>sent.current.delete(id)));
 },[open,feed.revision]);
 if(!open)return null;
 const items=feed.items.filter(i=>i.state==='open');
 const groups:[string,Item[]][]=[['Waiting for your decision',items.filter(isWaiting)],['Failed',items.filter(isFailed)],['Other',items.filter(i=>!isWaiting(i)&&!isFailed(i))]];
 const openChat=(i:Item)=>{if(matchMedia('(max-width:699px)').matches)setPanel(false);openItemChat(wb,i)};
 return <aside className="notifications-panel" aria-label="Notifications" onKeyDown={e=>{if(e.key==='Escape'&&!(e.target as HTMLElement).closest('input,textarea')){e.stopPropagation();setPanel(false)}}}>
  <div className="npanel-head"><h2>Notifications</h2><button type="button" className="npanel-x" aria-label="Close notifications" title="Close" onClick={()=>setPanel(false)}><X size={16}/></button></div>
  <div className="npanel-list">
   {feed.error&&<p className="notifications-note" role="status">Can't reach the Library server. Showing the last list.</p>}
   {feed.warnings.length>0&&<p className="notifications-note" role="status">Some sources could not be read: {feed.warnings.join('; ')}</p>}
   {groups.filter(g=>g[1].length).map(([label,rows])=><section key={label} className="npanel-group" aria-label={label}><h3>{label}<span className="notifications-count">{rows.length}</span></h3>{rows.map(i=><NotificationCard key={i.id} item={i} variant="page" onOpenChat={openChat}/>)}</section>)}
   {!items.length&&feed.mode!=='loading'&&<p className="notifications-empty">Nothing needs you.</p>}
  </div>
  <div className="npanel-foot"><button type="button" className="npanel-history" onClick={()=>wb.open({kind:'notifications',title:'History'},'tab')}>History</button></div>
 </aside>;
}

// History: answered and archived items, opened from the foot of the panel.
export function NotificationsPage(){
 const wb=useWorkbench(),feed=useFeed();
 useEffect(()=>{includeArchived(true);return()=>includeArchived(false)},[]);
 const rows=feed.items.filter(i=>i.state!=='open');
 return <div className="notifications-page"><div className="notifications-inner">
  <h1>History</h1>
  {feed.error&&<p className="notifications-note" role="status">Can't reach the Library server. Showing the last list.</p>}
  <div className="notifications-list">
   {rows.map(i=><NotificationCard key={i.id} item={i} variant="page" onOpenChat={x=>openItemChat(wb,x)}/>)}
   {!rows.length&&feed.mode!=='loading'&&<p className="notifications-empty">No answered or archived notifications.</p>}
  </div>
 </div></div>;
}
