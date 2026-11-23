import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';
import {useWorkbench} from '../state/context';
import {useFeed,actMany,act,usePanel,type Item} from './feed';
import {NotificationCard} from './NotificationCard';
import {openItemChat} from './open';
import {chimeForAttention} from '../state/approvalSound';
import './notifications.css';

const read=(key:string)=>{try{return new Set<string>(JSON.parse(sessionStorage.getItem(key)||'[]'))}catch{return new Set<string>()}};
const write=(key:string,ids:Set<string>)=>{try{sessionStorage.setItem(key,JSON.stringify([...ids].slice(-200)))}catch{}};
const narrow=()=>typeof matchMedia==='function'&&matchMedia('(max-width:699px)').matches;

// Mounted once in App. Also owns the tab title count. Hidden while the notifications panel is open (its items are there).
export function NotificationHost({openPanel}:{openPanel:()=>void}){
 const wb=useWorkbench(),feed=useFeed(),panel=usePanel();
 const [hidden,setHidden]=useState(()=>read('library-popup-hidden')),[mine,setMine]=useState(()=>read('library-popup-mine'));
 const tried=useRef(new Set<string>()),[phone,setPhone]=useState(narrow),[lift,setLift]=useState(0);
 const ref=useRef<HTMLDivElement>(null),over=useRef(new Set<string>());
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
 function hide(item:Item){
  setHidden(prev=>{const next=new Set(prev).add(item.id);write('library-popup-hidden',next);return next});
  void act(item,'shown').catch(()=>{});
 }
 // Timed pop-ups (app items only; agent items stay until she decides). The timer pauses while she hovers or types.
 useEffect(()=>{
  const seconds=feed.settings.duration_seconds;if(!seconds)return;
  const timers=shown.filter(i=>i.source==='app'&&!i.needs_answer).map(i=>setInterval(()=>{
   const node=ref.current?.querySelector(`[data-id="${CSS.escape(i.id)}"]`);
   if(node&&(node.matches(':hover')||node.contains(document.activeElement)))return;
   hide(i);
  },seconds*1000));
  return()=>timers.forEach(clearInterval);
 },[shown.map(i=>i.id).join('|'),feed.settings.duration_seconds]);
 // Desktop: the stack sits above any composer or sidebar footer it would cover, so the text box and Send stay free.
 // Phone: a slim bar takes its own row at the top (the app shrinks by its height), so it covers nothing.
 useLayoutEffect(()=>{
  const root=document.documentElement;
  const measure=()=>{
   const box=ref.current;
   root.style.setProperty('--npopup-bar',phone&&box?box.offsetHeight+'px':'0px');
   if(!box||phone){setLift(0);root.style.removeProperty('--npopup-reserve');return}
   const rect=box.getBoundingClientRect(),top=rect.top+lift,bottom=rect.bottom+lift;
   const blocks=[...document.querySelectorAll<HTMLElement>('.chat-composer-area,.sidebar-footer')].filter(b=>b.offsetParent).map(b=>b.getBoundingClientRect()).filter(r=>r.right>rect.left&&r.left<rect.right);
   let need=0;
   for(let pass=0;pass<4;pass++){const next=blocks.reduce((n,r)=>r.bottom>top-n&&r.top<bottom-n?Math.max(n,bottom-r.top+8):n,need);if(next===need)break;need=next}
   // Pages reserve this much scroll room at their end, so a control under the stack can be scrolled clear of it.
   root.style.setProperty('--npopup-reserve',Math.ceil(window.innerHeight-rect.top)+'px');
   setLift(prev=>prev===need?prev:need);
  };
  measure();
  const t=setInterval(measure,500);window.addEventListener('resize',measure);
  return()=>{clearInterval(t);window.removeEventListener('resize',measure)};
 });
 useEffect(()=>()=>{document.documentElement.style.removeProperty('--npopup-bar');document.documentElement.style.removeProperty('--npopup-reserve')},[]);
 if(phone&&shown.length){
  const open=feed.items.filter(i=>i.state==='open'),n=Math.max(feed.counts.open,candidates.length),agents=(open.length?open:candidates).every(i=>i.source==='agent');
  const label=agents?`${n} ${n===1?'agent needs':'agents need'} you`:`${n} ${n===1?'notification needs':'notifications need'} you`;
  return <>
   <div className="npopup-bar" ref={ref} role="region" aria-label="Notifications needing your answer">
    <button type="button" className="npopup-bar-open" onClick={openPanel}><i className="mk wait" aria-hidden="true"/>{label}</button>
    <button type="button" className="npopup-bar-x" aria-label="Dismiss. They stay in Notifications." title="Dismiss. They stay in Notifications." onClick={()=>candidates.forEach(hide)}><X size={15}/></button>
   </div>
  </>;
 }
 if(!shown.length)return null;
 return <div className="npopup-stack" ref={ref} style={lift?{bottom:16+lift,maxHeight:`calc(100vh - ${32+lift}px)`}:undefined} role="region" aria-label="Notifications needing your answer">
  {shown.map(i=><NotificationCard key={i.id} item={i} variant="popup" onOpenChat={x=>openItemChat(wb,x)} onHide={hide}/>)}
  {more>0&&<button type="button" className="npopup-more" onClick={openPanel}>{more} more</button>}
 </div>;
}
