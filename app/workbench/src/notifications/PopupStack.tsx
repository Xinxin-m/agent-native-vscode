import {useEffect,useLayoutEffect,useRef,useState,type ReactNode} from 'react';
import {X} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {useFeed,actMany,usePanel,type Item} from './feed';
import {NotificationCard} from './NotificationCard';
import {openItemChat} from './open';
import {startPopupTimer} from './popupTimer';
import './notifications.css';

const read=(key:string)=>{try{return new Set<string>(JSON.parse(sessionStorage.getItem(key)||'[]'))}catch{return new Set<string>()}};
const write=(key:string,ids:Set<string>)=>{try{sessionStorage.setItem(key,JSON.stringify([...ids].slice(-200)))}catch{}};
const narrow=()=>typeof matchMedia==='function'&&matchMedia('(max-width:699px)').matches;

// Each visible popup owns its timer so arrivals and feed refreshes cannot extend its lifetime.
function TimedPopup({seconds,onExpire,children}:{seconds:number;onExpire:()=>void;children:ReactNode}){
 const expire=useRef(onExpire),element=useRef<HTMLDivElement>(null);
 expire.current=onExpire;
 useEffect(()=>{
  const node=element.current;
  if(!seconds||!node)return;
  return startPopupTimer(node,seconds,()=>expire.current());
 },[seconds]);
 return <div ref={element}>{children}</div>;
}

// Mounted once in App. Also owns the tab title count. Hidden while the secondary sidebar is open (its items are there).
export function NotificationHost({openPane,status,dismissStatus}:{openPane:()=>void;status:{text:string;id:number}|null;dismissStatus:()=>void}){
 const wb=useWorkbench(),feed=useFeed(),panel=usePanel();
 const [hidden,setHidden]=useState(()=>read('library-popup-hidden')),[mine,setMine]=useState(()=>read('library-popup-mine'));
 const tried=useRef(new Set<string>()),[phone,setPhone]=useState(narrow);
 const ref=useRef<HTMLDivElement>(null);
 // The count is a prefix of the page title; the title changes with the active tab, so it is applied again after each change.
 useEffect(()=>{const n=feed.counts.open,strip=(t:string)=>t.replace(/^\(\d+\) /,''),apply=()=>{const want=n?`(${n}) ${strip(document.title)}`:strip(document.title);if(document.title!==want)document.title=want};apply();const el=document.querySelector('title'),watch=new MutationObserver(apply);if(el)watch.observe(el,{childList:true,characterData:true,subtree:true});return()=>{watch.disconnect();document.title=strip(document.title)}},[feed.counts.open]);
 useEffect(()=>{const q=matchMedia('(max-width:699px)'),on=()=>setPhone(q.matches);q.addEventListener('change',on);return()=>q.removeEventListener('change',on)},[]);
 // Claim each new pop-up once; another client holding the claim keeps it, so only one window shows it.
 useEffect(()=>{
  if(feed.mode!=='live')return;
  const fresh=feed.items.filter(i=>i.state==='open'&&(i.needs_answer||(i.source==='app'&&!i.shown))&&!tried.current.has(i.id)&&!hidden.has(i.id)).map(i=>i.id);
  if(!fresh.length)return;
  fresh.forEach(id=>tried.current.add(id));
  actMany(fresh,'shown').then((r:any)=>{
   const got=(r.claimed||[]) as string[];
   if(got.length)setMine(prev=>{const next=new Set(prev);got.forEach(id=>next.add(id));write('library-popup-mine',next);return next});
  }).catch(()=>fresh.forEach(id=>tried.current.delete(id)));
 },[feed.revision,feed.mode]);
 const candidates=panel?[]:feed.items.filter(i=>i.state==='open'&&mine.has(i.id)&&!hidden.has(i.id)).sort((a,b)=>b.at-a.at);
 const shown=candidates.slice(0,phone?1:3),more=candidates.length-shown.length;
 function hide(items:Item[]){
  setHidden(prev=>{const next=new Set(prev);items.forEach(item=>next.add(item.id));write('library-popup-hidden',next);return next});
 }
 function hideStack(){
  // Include claims already requested before the click, even when their replies are in flight.
  hide(feed.items.filter(item=>item.state==='open'&&(mine.has(item.id)||tried.current.has(item.id))));
  dismissStatus();
 }
 useEffect(()=>{
  window.addEventListener('library-dismiss-popups',hideStack);
  return ()=>window.removeEventListener('library-dismiss-popups',hideStack);
 });
 const dismissAll=()=>window.dispatchEvent(new Event('library-dismiss-popups'));
 const seconds=feed.settings.duration_seconds;
 // Desktop: the stack sits above any composer or sidebar footer it would cover, so the text box and Send stay free.
 // Phone: a slim bar takes its own row at the top (the app shrinks by its height), so it covers nothing.
 useLayoutEffect(()=>{
  const root=document.documentElement;
  const measure=()=>{
   const box=ref.current;
   root.style.setProperty('--npopup-bar',phone&&box?box.offsetHeight+'px':'0px');
   if(!box||phone){box?.style.removeProperty('bottom');box?.style.removeProperty('max-height');root.style.removeProperty('--npopup-reserve');return}
   // Measure the natural stack before moving it. Measuring the previously clipped
   // stack made its height and offset drive one another in a synchronous render loop.
   box.style.removeProperty('bottom');
   box.style.removeProperty('max-height');
   const rect=box.getBoundingClientRect(),top=rect.top,bottom=rect.bottom;
   const style=getComputedStyle(box),scale=rect.width/parseFloat(style.width)||1;
   const baseBottom=parseFloat(style.bottom)||0;
   const blocks=[...document.querySelectorAll<HTMLElement>('.chat-composer-area,.sidebar-footer')].filter(b=>b.offsetParent).map(b=>b.getBoundingClientRect()).filter(r=>r.right>rect.left&&r.left<rect.right);
   let need=0;
   for(let pass=0;pass<4;pass++){const next=blocks.reduce((n,r)=>r.bottom>top-n&&r.top<bottom-n?Math.max(n,bottom-r.top+8*scale):n,need);if(next===need)break;need=next}
   // Pages reserve this much scroll room at their end, so a control under the stack can be scrolled clear of it.
   if(need>0){
    box.style.bottom=baseBottom+Math.ceil(need/scale)+'px';
    box.style.maxHeight=Math.max(0,Math.floor((bottom-need)/scale)-16)+'px';
   }
   root.style.setProperty('--npopup-reserve',Math.ceil((window.innerHeight-box.getBoundingClientRect().top)/scale)+'px');
  };
  measure();
  const t=setInterval(measure,500);window.addEventListener('resize',measure);
  return()=>{clearInterval(t);window.removeEventListener('resize',measure)};
 });
 useEffect(()=>()=>{document.documentElement.style.removeProperty('--npopup-bar');document.documentElement.style.removeProperty('--npopup-reserve')},[]);
 if(!shown.length&&!status)return null;
 const dismiss=<button type="button" className="npopup-dismiss" onClick={dismissAll}>Dismiss all notifications</button>;
 if(phone){
  const n=Math.max(feed.counts.open,candidates.length);
  return <div className="npopup-bar" ref={ref} role="region" aria-label="Notifications">
   {status?<TimedPopup key={'status'+status.id} seconds={seconds} onExpire={dismissStatus}><span role="status">{status.text}</span></TimedPopup>:
    <TimedPopup key={shown[0].id} seconds={seconds} onExpire={()=>hide(candidates)}>
     <button type="button" className="npopup-bar-open" onClick={openPane}><i className="mk wait" aria-hidden="true"/>{n} {n===1?'notification needs':'notifications need'} you</button>
    </TimedPopup>}
   {dismiss}
  </div>;
 }
 return <div className="npopup-stack" ref={ref} role="region" aria-label="Notifications">
  {dismiss}
  {status&&<TimedPopup key={'status'+status.id} seconds={seconds} onExpire={dismissStatus}><div role="status" className="npopup-status">{status.text}<button type="button" aria-label="Dismiss notification" onClick={dismissStatus}><X size={14}/></button></div></TimedPopup>}
  {shown.map(i=><TimedPopup key={i.id} seconds={seconds} onExpire={()=>hide([i])}><NotificationCard item={i} variant="popup" onOpenChat={x=>openItemChat(wb,x)} onHide={item=>hide([item])}/></TimedPopup>)}
  {more>0&&<button type="button" className="npopup-more" onClick={openPane}>{more} more</button>}
 </div>;
}
