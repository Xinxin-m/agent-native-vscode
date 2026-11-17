// One soft chime when a run waits for her answer. Only attention items the broker flags
// human_required count, so auto-approved requests stay quiet. Each request chimes once,
// across windows that share this storage.
const storageKey='library-approval-chimes',heardHere=new Set<string>();
function heard():string[]{try{return JSON.parse(globalThis.localStorage?.getItem(storageKey)||'[]')}catch{return []}}
export function playChime(){
 const Context=(globalThis as any).AudioContext||(globalThis as any).webkitAudioContext;if(!Context)return;
 try{const ctx=new Context(),tone=ctx.createOscillator(),gain=ctx.createGain(),t=ctx.currentTime;
  tone.type='sine';tone.frequency.setValueAtTime(660,t);
  gain.gain.setValueAtTime(0.0001,t);gain.gain.exponentialRampToValueAtTime(0.05,t+0.03);gain.gain.exponentialRampToValueAtTime(0.0001,t+0.6);
  tone.connect(gain);gain.connect(ctx.destination);tone.start(t);tone.stop(t+0.6);tone.onended=()=>void ctx.close?.();void ctx.resume?.()?.catch?.(()=>{});
 }catch{}
}
// The launchd watcher (attention_watch.py) chimes without any window open; while it runs the clients stay silent.
export const soundEnabled=(config:any,attention:any)=>config?.attention_sound!==false&&attention?.watcher_sound!==true;
export function chimeForAttention(items:any[],enabled:boolean,play=playChime){
 const known=new Set([...heard(),...heardHere]);
 const fresh=(items||[]).filter(i=>i?.human_required||i?.payload?.human_required).map(i=>i.run_id+':'+i.request_id).filter(id=>!known.has(id));
 if(!fresh.length)return false;
 fresh.forEach(id=>heardHere.add(id));
 try{globalThis.localStorage?.setItem(storageKey,JSON.stringify([...heard(),...fresh].slice(-100)))}catch{}
 if(enabled)play();
 return enabled;
}
