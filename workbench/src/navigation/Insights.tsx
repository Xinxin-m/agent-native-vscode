import {useEffect,useRef} from 'react';
import {useWorkbench} from '../state/context';
let loading:Promise<void>|undefined;
function load(){return loading ||= new Promise<void>((resolve,reject)=>{
 if(customElements.get('library-insights')){resolve();return;}
 const script=document.createElement('script');script.src='/static/settings-insights.js';script.onload=()=>resolve();script.onerror=()=>{loading=undefined;reject(Error('Could not load insights'));};document.head.append(script);
});}
export function Insights({mode}:{mode:'resources'|'analytics'}){
 const ref=useRef<HTMLDivElement>(null);const {open}=useWorkbench();const openRef=useRef(open);openRef.current=open;
 useEffect(()=>{let cancelled=false;const node=ref.current!;let el:HTMLElement|undefined;
 if(!document.querySelector('link[data-insights]')){const link=document.createElement('link');link.rel='stylesheet';link.href='/static/settings-insights.css';link.dataset.insights='true';document.head.append(link);}
 load().then(()=>{if(cancelled)return;el=document.createElement('library-insights');el.setAttribute('mode',mode);el.addEventListener('insights-open-chat',(event:any)=>openRef.current({kind:'chat',key:event.detail.key,title:'Conversation'},'tab'));node.replaceChildren(el);}).catch(e=>{if(!cancelled)node.textContent=e.message;});return()=>{cancelled=true;el?.remove();};
 },[mode]);
 return <div ref={ref}/>;
}
