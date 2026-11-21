import {describe,it,expect} from 'vitest';
import {reuseSnapshot,peerRoster} from './pollingSnapshots';
describe('polled snapshots',()=>{
 it('keeps the previous observation across fresh but unchanged nested API JSON',()=>{const previous=[{id:'run',status:'running',details:{steps:[1,2]}}];expect(reuseSnapshot(previous,JSON.parse(JSON.stringify(previous)))).toBe(previous);});
 it('publishes changes to status, order, nested fields and field presence',()=>{for(const next of [[{id:'run',status:'failed'}],[{id:'run',status:'running',details:{steps:[2,1]}}]]){const previous=[{id:'run',status:'running',details:{steps:[1,2]}}];expect(reuseSnapshot(previous,next)).toBe(next);}const absent={};expect(reuseSnapshot(absent,{optional:undefined})).not.toBe(absent);});
 it('expires closed peers and ignores heartbeat-only UI changes while preserving new titles',()=>{const peers=new Map([['a',{id:'a',title:'A',at:10000}],['old',{id:'old',title:'Closed',at:0}]]);const first=peerRoster(peers,16000);expect(peers.has('old')).toBe(false);peers.set('a',{id:'a',title:'A',at:17000});expect(reuseSnapshot(first,peerRoster(peers,18000))).toBe(first);peers.set('a',{id:'a',title:'Renamed',at:19000});expect(reuseSnapshot(first,peerRoster(peers,20000))).not.toBe(first);expect(peerRoster(peers,40000)).toEqual([]);expect(peers.size).toBe(0);});
});
