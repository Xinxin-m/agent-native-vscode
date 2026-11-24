import {Bell} from 'lucide-react';
import {useFeed} from './feed';
import {useWorkbench} from '../state/context';
import './notifications.css';

// Sidebar footer button, left of the profile button; opens the Notifications pane (the secondary sidebar is Cmd+L).
export function NotificationBell(){
 const {counts}=useFeed(),wb=useWorkbench(),n=counts.open;
 return <button type="button" className="notification-bell-button" aria-label={n?`Notifications (${n} open)`:'Notifications'} title="Notifications" onClick={()=>wb.open({kind:'notifications',title:'Notifications'},'tab')}>
  <Bell size={16}/>{n>0&&<span className="notification-bell-badge">{n>99?'99+':n}</span>}
 </button>;
}
