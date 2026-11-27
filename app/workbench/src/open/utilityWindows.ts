import {useEffect,useRef,useState} from 'react';
import type {Destination,Resource} from '../state/types';
import {encodeResource} from './links';

type GroupChoice={id:string;label:string;saved:boolean;select:()=>void};
type Open=(resource:Resource,destination?:Destination,groupId?:string)=>void;
const query=new URLSearchParams(location.search);
export const utilityWindow=query.get('utility')==='1';
const utilityKinds=new Set(['settings','accounts','notifications','recent','projects']);

export function useUtilityWindows(open:Open,choices:GroupChoice[],enabled:boolean,owner:string,notify:(text:string)=>void){
 const child=useRef<Window|null>(null);
 const latest=useRef({open,choices});
 latest.current={open,choices};
 const [remoteGroups,setRemoteGroups]=useState<Omit<GroupChoice,'select'>[]>([]);
 const hasOwner=()=>utilityWindow&&query.has('utilityHost')&&window.opener&&!window.opener.closed;
 const groupSignature=JSON.stringify(choices.map(({id,label,saved})=>({id,label,saved})));
 useEffect(()=>{
  const receive=(event:MessageEvent)=>{
   if(event.origin!==location.origin)return;
   const msg=event.data;
   if(!msg||typeof msg.type!=='string')return;
   if(child.current&&event.source===child.current){
    if(msg.type==='library-utility-ready')child.current.postMessage({type:'library-utility-groups',groups:JSON.parse(groupSignature)},location.origin);
    if(msg.type==='library-utility-open')latest.current.open(msg.resource,msg.destination);
    if(msg.type==='library-utility-group')latest.current.choices.find(group=>group.id===msg.id)?.select();
   } else if(hasOwner()&&event.source===window.opener&&msg.type==='library-utility-groups')setRemoteGroups(msg.groups);
  };
  window.addEventListener('message',receive);
  if(child.current&&!child.current.closed)child.current.postMessage({type:'library-utility-groups',groups:JSON.parse(groupSignature)},location.origin);
  if(hasOwner())window.opener.postMessage({type:'library-utility-ready'},location.origin);
  return ()=>window.removeEventListener('message',receive);
 },[groupSignature]);
 const openPage:Open=(resource,destination,groupId)=>{
  if(hasOwner()&&!utilityKinds.has(resource.kind)&&(!destination||destination==='tab')){
   window.opener.postMessage({type:'library-utility-open',resource,destination},location.origin);
   window.close();return;
  }
  if(!utilityWindow&&enabled&&utilityKinds.has(resource.kind)&&(!destination||destination==='tab')&&!groupId){
   const url=new URL(location.pathname,location.origin);
   url.searchParams.set('utility','1');url.searchParams.set('window',crypto.randomUUID());url.searchParams.set('utilityHost',owner);
   url.hash=encodeResource(resource);
   child.current=window.open(url,'library-utilities-'+owner,'popup,width=980,height=760,resizable=yes,scrollbars=yes');
   if(child.current){child.current.focus();return;}
   notify('Your browser blocked the pop-up. Opened in this pane.');
  }
  open(resource,destination,groupId);
 };
 const tabGroups=hasOwner()?remoteGroups.map(group=>({...group,select:()=>{
  window.opener.postMessage({type:'library-utility-group',id:group.id},location.origin);window.close();
 }})):choices;
 return {openPage,tabGroups};
}
