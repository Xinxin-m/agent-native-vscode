import {useEffect,useRef} from 'react';
import {api} from '../api/client';
import {useWorkbench} from '../state/context';
import '../chat/authRecovery.js';
import './notification-center.js';
import './notification-center.css';

export function NotificationCenter({officialRecovery}:{officialRecovery?:(key:string)=>Promise<unknown>}={}){
 const wb=useWorkbench();
 const host=useRef<HTMLDivElement>(null);
 const latest=useRef({wb,officialRecovery});
 latest.current={wb,officialRecovery};
 useEffect(()=>{
  const holder=host.current;
  if(!holder)return;
  return (window as any).LibraryNotifications.mount(holder,{
   request:api,
   accounts:()=>[...latest.current.wb.accounts,...(latest.current.wb.pendingNoticeAccounts||[])],
   onAccounts:()=>void latest.current.wb.refresh(),
   openChat:(row:any)=>latest.current.wb.open({kind:'chat',key:row.chat_key,runId:row.run_id,title:'Failed conversation'}),
   officialRecovery:officialRecovery?(row:any)=>latest.current.officialRecovery?.(row.chat_key):undefined
  });
 },[]);
 return <div ref={host}/>;
}
