import React,{useEffect,useState} from 'react';
import {api} from '../api/client';
export const RESCAN_NOTE='Checks the whole library and refreshes labels for new or changed chats; manual edits stay.';
type Suggestion={key:string;from:string;to:string;to_name:string;from_name?:string;title?:string};
export function ProjectSuggestions({refresh,notify=()=>{}}:{refresh:()=>unknown;notify?:(text:string)=>void}){
 const [rows,setRows]=useState<Suggestion[]>([]),[busy,setBusy]=useState('loading'),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const read=(value:any)=>{if(!Array.isArray(value.suggestions))throw Error('Project suggestions returned an invalid response.');setRows(value.suggestions);};
 const message=(e:any)=>e?.status===404?'Project suggestions need the updated Library server.':e instanceof Error?e.message:String(e);
 useEffect(()=>{let active=true;api('/api/project_suggestions').then(value=>{if(active)read(value)}).catch(e=>{if(active)setError(message(e))}).finally(()=>{if(active)setBusy('')});return()=>{active=false}},[]);
 async function act(action:string,row?:Suggestion){
  if(busy)return;setBusy(row?.key||action);setError('');setNotice('');
  try{
   if(action==='refresh')read(await api('/api/project_suggestions'));
   else if(action==='rescan'){await api('/api/rescan',{});setNotice('Rescan requested. Labels update as the scan finishes.');}
   else {read(await api('/api/project_suggestions',{action,key:row!.key,to:row!.to}));setNotice(action==='accept'?'Project placement accepted.':'Suggestion rejected; this destination will not be suggested again.');if(action==='accept')await refresh();}
  }catch(e){setError(message(e));}finally{setBusy('')}
 }
 return <section className={'project-suggestions'+(rows.length?'':' is-empty')} aria-label="Project suggestions"><div className="directory-controls"><h2>Project suggestions</h2><button disabled={!!busy} onClick={()=>void act('refresh')}>Refresh suggestions</button><button disabled={!!busy} title={RESCAN_NOTE} onClick={()=>void act('rescan')}>Rescan library</button></div>{rows.length>0&&<p className="project-review-note">{RESCAN_NOTE}</p>}{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy==='loading'&&<p>Loading suggestions…</p>}{!busy&&!error&&!rows.length&&<p className="project-review-note project-review-empty">Nothing to review.</p>}{rows.map(row=><article className="project-suggestion" key={row.key}><div><strong>{row.title||row.key}</strong><p>{row.from_name||row.from} → {row.to_name||row.to}</p></div><div className="directory-actions"><button disabled={!!busy} aria-label={'Accept '+(row.title||row.key)} onClick={()=>void act('accept',row)}>Accept</button><button disabled={!!busy} aria-label={'Reject '+(row.title||row.key)} onClick={()=>void act('reject',row)}>Reject</button></div></article>)}</section>;
}
