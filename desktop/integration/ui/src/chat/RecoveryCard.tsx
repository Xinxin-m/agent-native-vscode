import {useEffect,useRef} from 'react';
import {api} from '../api/client';
import './authRecovery.js';
const recovery=()=>(window as any).LibraryRecovery;
export function RecoveryCard({run,accounts,onRun,onAccounts}:any){const host=useRef<HTMLDivElement>(null);useEffect(()=>{if(host.current)recovery().mount(host.current,{run,accounts,request:api,onRun,onAccounts})},[run,accounts,onRun,onAccounts]);return <div ref={host}/>}
export function AccountWarning({account,accounts,onAccounts}:any){const host=useRef<HTMLDivElement>(null);useEffect(()=>{if(!host.current||!account)return;const options={account,accounts,request:api,onAccounts};recovery().mountWarning(host.current,options)},[account,accounts,onAccounts]);return <div ref={host}/>}
