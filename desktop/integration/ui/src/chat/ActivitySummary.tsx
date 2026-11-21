import {useEffect,useState} from 'react';
import type {RunEvent} from '../state/runs';
import {summarizeActivity} from './activityPhase';
import './ActivitySummary.css';

export function ActivitySummary({run,events,awaiting,connectionError}:{run:any;events:RunEvent[];awaiting:boolean;connectionError?:string}){
 const [now,setNow]=useState(Date.now());
 const summary=summarizeActivity(run,events,awaiting,now),phase=connectionError?'Connection interrupted':summary.phase;
 const active=!connectionError&&summary.active;
 useEffect(()=>{if(!active)return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[run?.id,active]);
 return <div className="activity-summary" data-phase={phase}>
  <div className="activity-summary-status" role="status" aria-live="polite"><span className={'activity-summary-dot'+(active?' is-active':'')} aria-hidden="true"/><span>{phase}</span></div>
  {summary.elapsed&&<span className="activity-summary-meta" aria-label={'Elapsed '+summary.elapsed}>{summary.elapsed}</span>}
 </div>;
}
