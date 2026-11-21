// JSON API snapshots are immutable. Equal observations should not invalidate the whole workbench context.
export function sameSnapshot(a:any,b:any):boolean {
 if(Object.is(a,b))return true;
 if(a===null||b===null||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
 if(Array.isArray(a))return a.length===b.length&&a.every((value,index)=>sameSnapshot(value,b[index]));
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.prototype.hasOwnProperty.call(b,key)&&sameSnapshot(a[key],b[key]));
}
export const reuseSnapshot=<T>(previous:T,next:T):T=>sameSnapshot(previous,next)?previous:next;
export function peerRoster(peers:Map<string,any>,now=Date.now()){
 for(const [id,peer] of peers)if(!Number.isFinite(peer.at)||peer.at>now+5000||now-peer.at>=15000)peers.delete(id);
 return [...peers.values()].map(peer=>({id:peer.id,title:peer.title}));
}
