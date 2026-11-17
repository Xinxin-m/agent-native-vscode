// Reconcile a flattened stream only against the latest indexed assistant reply.
export function recordedOutputContains(turns:any[],output:string){
 const normalize=(text:string)=>text.replace(/\s+/g,' ').trim();
 const expected=normalize(output);if(!expected)return false;
 let start=0;for(let i=turns.length-1;i>=0;i--)if(turns[i].role==='user'){start=i+1;break}
 const parts=turns.slice(start).filter(turn=>turn.role==='assistant').map(turn=>String(turn.text||''));
 return parts.some(text=>normalize(text)===expected)||normalize(parts.join(' '))===expected||normalize(parts.join(''))===expected;
}
