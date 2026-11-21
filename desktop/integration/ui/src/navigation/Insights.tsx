import {useEffect,useRef} from 'react';
import {useWorkbench} from '../state/context';
// Bundle the shared component for native webviews; its requests use the native RPC bridge.
// @ts-ignore Shared browser component is plain JavaScript.
import '../../../backend/web/settings-insights.js';
import '../../../backend/web/settings-insights.css';
export function Insights({mode}:{mode:'resources'|'analytics'}){
 const ref=useRef<HTMLDivElement>(null);const {open}=useWorkbench();const openRef=useRef(open);openRef.current=open;
 useEffect(()=>{let cancelled=false;const node=ref.current!;let el:HTMLElement|undefined;
 Promise.resolve().then(()=>{if(cancelled)return;el=document.createElement('library-insights');el.setAttribute('mode',mode);el.addEventListener('insights-open-chat',(event:any)=>openRef.current({kind:'chat',key:event.detail.key,title:'Conversation'},'tab'));el.addEventListener('insights-open-resource',(event:any)=>openRef.current({kind:'file',host_id:'mac',path:event.detail.path,title:event.detail.name},'tab'));node.replaceChildren(el);}).catch(e=>{if(!cancelled)node.textContent=e.message;});return()=>{cancelled=true;el?.remove();};
 },[mode]);
 return <div ref={ref}/>;
}
