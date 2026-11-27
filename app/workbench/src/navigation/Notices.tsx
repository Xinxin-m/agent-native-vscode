import {useEffect,useState} from 'react';
import {poll} from '../notifications/feed';
import {api} from '../api/client';

// Notification preferences are shared by the Library service.
export function useNotices(){
 const [value,setValue]=useState<any>(null),[error,setError]=useState(''),[status,setStatus]=useState('');
 useEffect(()=>{api('/api/notices').then(setValue,()=>setError('Notification settings are unavailable.'))},[]);
 async function set(name:string,on:boolean|number){setStatus('Saving…');try{setValue(await api('/api/notices',{action:'settings',[name]:on}));void poll(true);setStatus('Saved')}catch(e:any){setStatus('Not saved: '+e.message)}}
 return {value,error,status,set};
}
type Notices=ReturnType<typeof useNotices>;
export const NoticeToggle=({notices,name}:{notices:Notices;name:'usage'|'signin'})=><span className="setting-control"><small role="status">{notices.status}</small><input type="checkbox" disabled={!notices.value} checked={notices.value?.settings?.[name]!==false} onChange={e=>void notices.set(name,e.target.checked)}/></span>;
export function NoticeDuration({notices}:{notices:Notices}){
 const value=notices.value?.settings?.duration_seconds??4;
 const choices=[3,4,5,10,30,60,0];
 if(!choices.includes(value))choices.unshift(value);
 return <span className="setting-control"><small role="status">{notices.status}</small><select aria-label="Notification popup duration" disabled={!notices.value} value={value} onChange={e=>void notices.set('duration_seconds',Number(e.target.value))}>{choices.map(n=><option key={n} value={n}>{n===0?'Keep open':n+' seconds'}</option>)}</select></span>;
}
