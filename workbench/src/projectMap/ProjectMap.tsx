import React,{useEffect,useRef,useState} from 'react';
import {useWorkbench} from '../state/context';
import type {Resource} from '../state/types';
import {PROJECT_MAP_ORIGIN,projectMapMessage,projectMapUrl} from './bridge';
import './project-map.css';

export function ProjectMap({resource,panelApi}:{resource:Resource;panelApi:any}){
 const {data,open,preferences}=useWorkbench();const frame=useRef<HTMLIFrameElement>(null);const [ready,setReady]=useState(false),[slow,setSlow]=useState(false),[attempt,setAttempt]=useState(0);
 const theme=preferences.theme==='dark'||(preferences.theme==='system'&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';
 // Theme changes travel over the bridge, retaining the map's camera and panels.
 const [visible,setVisible]=useState(panelApi.isVisible);
 const [initialTheme]=useState(theme);const src=projectMapUrl(resource.projectId!,initialTheme);
 const sendTheme=()=>{if(!frame.current?.dataset.ready)return;frame.current?.contentWindow?.postMessage({type:'project-map:theme',theme},PROJECT_MAP_ORIGIN);frame.current?.contentWindow?.postMessage({type:'project-map:visibility',visible:visible&&!document.hidden},PROJECT_MAP_ORIGIN);};
 useEffect(()=>{const subscription=panelApi.onDidVisibilityChange(e=>setVisible(e.isVisible));return()=>subscription.dispose();},[panelApi]);
 useEffect(()=>{const changed=()=>sendTheme();document.addEventListener('visibilitychange',changed);return()=>document.removeEventListener('visibilitychange',changed);},[visible,theme]);
 useEffect(()=>{const receive=(event:MessageEvent)=>{const message=projectMapMessage(event,frame.current?.contentWindow||null);if(!message)return;if(message.type==='ready'){if(frame.current)frame.current.dataset.ready='1';setReady(true);setSlow(false);sendTheme();}else{const chat=data?.chats?.find(c=>c.key===message.key);open({kind:'chat',key:message.key,title:chat?.title,meta:chat},'right');}};window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);},[data,open,theme,visible]);
 useEffect(()=>{sendTheme();},[theme,visible]);
 useEffect(()=>{setReady(false);setSlow(false);const timer=setTimeout(()=>setSlow(true),10000);return()=>clearTimeout(timer);},[src,attempt]);
 return <div className="project-map-panel"><iframe key={attempt} ref={frame} title={(resource.title||'Project')+' agent tree'} src={src} allow="clipboard-write" sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads" onLoad={sendTheme}/>{!ready&&<div className="project-map-status" role="status">{slow?<><span>The project map service is unavailable.</span><button onClick={()=>setAttempt(n=>n+1)}>Retry</button><a href={src} target="_blank" rel="noreferrer">Open map separately</a></>:<span>Loading project map…</span>}</div>}</div>;
}
