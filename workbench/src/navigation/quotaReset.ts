/** Provider reset timestamps are absolute instants, never inferred from the window label. */
export function resetTimestamp(value:unknown):number|undefined{
 if(value===null||value===undefined||value==='')return;
 const n=typeof value==='number'?value:typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value)?Number(value):undefined;
 const result=n!==undefined?(n<1e12?n*1000:n):typeof value==='string'?Date.parse(value):NaN;
 return Number.isFinite(result)&&result>0?result:undefined;
}
export function resetCountdown(value:unknown,now=Date.now()){
 const at=resetTimestamp(value);if(at===undefined)return {label:'Reset time unavailable',at:undefined};
 const remaining=at-now;if(remaining<=0)return {label:'Reset due · refresh usage',at};
 const minutes=Math.ceil(remaining/60000),days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),rest=minutes%60;
 const duration=days?`${days}d ${hours}h`:hours?`${hours}h ${rest}m`:`${minutes}m`;
 return {label:`Resets in ${duration}`,at};
}

/** A future reset is independent of when provider usage was last observed. */
export function usageSnapshot(account:any,now=Date.now()){
 const cached=resetTimestamp(account.cached_at),rollout=resetTimestamp(account.rollout_at),fetched=resetTimestamp(account.fetched_at);
 const at=cached??rollout??fetched;
 if(at===undefined||at>now+60000)return {label:'Usage snapshot time unavailable',at:undefined};
 const minutes=Math.max(0,Math.floor((now-at)/60000)),days=Math.floor(minutes/1440),hours=Math.floor(minutes/60);
 const age=days?`${days}d ago`:hours?`${hours}h ago`:minutes?`${minutes}m ago`:'just now';
 return {label:`${cached?'Cached usage':rollout?'Transcript usage snapshot':'Usage checked'} ${age}`,at};
}
