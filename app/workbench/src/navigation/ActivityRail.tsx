import {Activity,Files,MessageSquare,Settings,UserRound} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {NotificationBell} from '../notifications/NotificationBell';
import './activity-rail.css';

const views=[{id:'chats',label:'Chats',Icon:MessageSquare},{id:'files',label:'Explorer',Icon:Files},{id:'live',label:'Live',Icon:Activity}];

export function ActivityRail({mode,sidebar,onSelect}:{mode:string;sidebar:boolean;onSelect:(id:string)=>void}){
 const {open,preferences}=useWorkbench();
 const visibility=preferences.iconBar||'visible';
 if(visibility==='hidden')return null;
 return <nav aria-label="Activity bar" className={'activity-rail '+(visibility==='autoHide'?'auto-hide':'')}>
  {views.map(({id,label,Icon})=><button key={id} aria-label={label} title={label} aria-pressed={sidebar&&(mode===id||id==='chats'&&mode==='search')} onClick={()=>onSelect(id)}><Icon size={18}/></button>)}
  <span className="rail-spacer"/>
  <NotificationBell/>
  <button aria-label="Profile and connected accounts" title="Profile and connected accounts" onClick={()=>open({kind:'settings',title:'Accounts',meta:{settingsTab:'Accounts'}},'tab')}><UserRound size={18}/></button>
  <button aria-label="Settings" title="Settings" onClick={()=>open({kind:'settings',title:'Settings',meta:{settingsTab:'Appearance'}},'tab')}><Settings size={18}/></button>
 </nav>;
}
