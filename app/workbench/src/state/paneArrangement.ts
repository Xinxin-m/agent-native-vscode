/** Assign related tabs to existing panes, retaining the strongest existing placement. */
export function planPaneArrangement<T extends string | number>(panes: readonly T[], entries: readonly {pane:T;bucket:string}[]): Map<string,T> {
 const buckets=new Map<string,Map<T,number>>();
 for(const entry of entries){const counts=buckets.get(entry.bucket)||new Map<T,number>();counts.set(entry.pane,(counts.get(entry.pane)||0)+1);buckets.set(entry.bucket,counts);}
 const used=new Map<T,number>(),result=new Map<string,T>();
 // Resolve strong existing homes first; a stable key makes reruns deterministic.
 const ordered=[...buckets].sort((a,b)=>Math.max(...b[1].values())-Math.max(...a[1].values())||[...b[1].values()].reduce((x,y)=>x+y,0)-[...a[1].values()].reduce((x,y)=>x+y,0)||a[0].localeCompare(b[0]));
 for(const [key,counts] of ordered){
  const available=panes.filter(pane=>!used.has(pane)),candidates=available.length?available:panes;
  const target=[...candidates].sort((a,b)=>(counts.get(b)||0)-(counts.get(a)||0)||(used.get(a)||0)-(used.get(b)||0)||panes.indexOf(a)-panes.indexOf(b))[0];
  if(target!==undefined){result.set(key,target);used.set(target,(used.get(target)||0)+[...counts.values()].reduce((a,b)=>a+b,0));}
 }
 return result;
}
