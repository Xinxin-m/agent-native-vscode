import {Bell} from 'lucide-react';
import {useFeed,usePanel,setPanel} from './feed';
import './notifications.css';

// Sidebar footer button, left of the profile button; toggles the notifications panel.
export function NotificationBell(){
 const {counts}=useFeed(),open=usePanel(),n=counts.open;
 return <button type="button" className="notification-bell-button" aria-label={n?`Notifications (${n} open)`:'Notifications'} aria-expanded={open} title={open?'Close notifications':'Notifications'} onClick={()=>setPanel(v=>!v)}>
  <Bell size={16}/>{n>0&&<span className="notification-bell-badge">{n>99?'99+':n}</span>}
 </button>;
}
