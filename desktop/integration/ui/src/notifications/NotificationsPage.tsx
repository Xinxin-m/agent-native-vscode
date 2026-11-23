import {useEffect,useRef} from 'react';
import {useWorkbench} from '../state/context';
import {useFeed,actMany,includeArchived,isWaiting,isFailed,type Item} from './feed';
import {NotificationCard} from './NotificationCard';
import {openItemChat} from './open';
import './notifications.css';

// The one notifications surface. The bell (core globalCompositeBar.ts) runs library.notifications, which opens
// this view in an editor group beside the chat; the tab's own close button closes it.
export function NotificationsPanel({visible=true}:{visible?:boolean}){
 const wb=useWorkbench(),feed=useFeed();
 const sent=useRef(new Set<string>());
 // While the panel is showing, what is open counts as seen; the bell counts open items, so seen does not change it.
 useEffect(()=>{
  if(!visible)return;
  const ids=feed.items.filter(i=>i.state==='open'&&!i.seen&&!sent.current.has(i.id)).map(i=>i.id);
  if(!ids.length)return;
  ids.forEach(id=>sent.current.add(id));
  actMany(ids,'seen').catch(()=>ids.forEach(id=>sent.current.delete(id)));
 },[visible,feed.revision]);
 const items=feed.items.filter(i=>i.state==='open');
 const groups:[string,Item[]][]=[['Waiting for your decision',items.filter(isWaiting)],['Failed',items.filter(isFailed)],['Other',items.filter(i=>!isWaiting(i)&&!isFailed(i))]];
 return <aside className="notifications-panel" aria-label="Notifications">
  <div className="npanel-head"><h2>Notifications</h2></div>
  <div className="npanel-list">
   {feed.error&&<p className="notifications-note" role="status">Can't reach the Library server. Showing the last list.</p>}
   {feed.warnings.length>0&&<p className="notifications-note" role="status">Some sources could not be read: {feed.warnings.join('; ')}</p>}
   {groups.filter(g=>g[1].length).map(([label,rows])=><section key={label} className="npanel-group" aria-label={label}><h3>{label}<span className="notifications-count">{rows.length}</span></h3>{rows.map(i=><NotificationCard key={i.id} item={i} onOpenChat={x=>openItemChat(wb,x)}/>)}</section>)}
   {!items.length&&feed.mode!=='loading'&&<p className="notifications-empty">Nothing needs you.</p>}
  </div>
  <div className="npanel-foot"><button type="button" className="npanel-history" onClick={()=>wb.open({kind:'notifications',title:'History',meta:{view:'history'}},'tab')}>History</button></div>
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
   {rows.map(i=><NotificationCard key={i.id} item={i} onOpenChat={x=>openItemChat(wb,x)}/>)}
   {!rows.length&&feed.mode!=='loading'&&<p className="notifications-empty">No answered or archived notifications.</p>}
  </div>
 </div></div>;
}
