import {renderedRecoveryKey} from './markdownEdit';
export type RenderedRecovery={owner:string;text:string;persisted:boolean};
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
const unavailable:StorageLike={get length(){return 0},key:()=>null,getItem:()=>null,setItem:()=>{throw Error('Draft storage unavailable')},removeItem:()=>{throw Error('Draft storage unavailable')}};
export function recoveryStorage(get:()=>StorageLike=()=>globalThis.localStorage):StorageLike{try{return get()}catch{return unavailable}}
const memory=new Map<string,Map<string,RenderedRecovery>>();
function memoryFor(key:string){let entries=memory.get(key);if(!entries){entries=new Map();memory.set(key,entries)}return entries;}
const ownerKey=(key:string,owner:string)=>key+':'+JSON.stringify(owner);
function read(storage:StorageLike,key:string):RenderedRecovery|null{try{const raw=storage.getItem(key);if(!raw)return null;try{const value=JSON.parse(raw);if(typeof value?.owner==='string'&&typeof value?.text==='string')return {owner:value.owner,text:value.text,persisted:true}}catch{}return {owner:'unidentified',text:raw,persisted:true}}catch{return null}}
export function listRenderedRecoveries(key:string,storage:StorageLike){const entries=new Map(memory.get(key)||[]);try{const legacy=read(storage,key);if(legacy)entries.set('legacy:'+legacy.owner,{...legacy,owner:'legacy:'+legacy.owner});const prefix=key+':';for(let i=0;i<storage.length;i++){const name=storage.key(i);if(!name?.startsWith(prefix))continue;let owner:string;try{owner=JSON.parse(name.slice(prefix.length))}catch{continue}if(typeof owner!=='string')continue;const item=read(storage,name);if(item&&item.owner===owner&&!entries.has(owner))entries.set(owner,item)}}catch{}return [...entries.values()];}
export function rememberRenderedRecovery(key:string,owner:string,text:string,storage:StorageLike){const entry={owner,text,persisted:false};memoryFor(key).set(owner,entry);try{storage.setItem(ownerKey(key,owner),JSON.stringify({owner,text}));entry.persisted=true}catch{}return entry;}
export function releaseRenderedMemory(key:string,owner:string){memory.get(key)?.delete(owner);if(!memory.get(key)?.size)memory.delete(key);}
export function discardRenderedRecovery(key:string,owner:string,storage:StorageLike){storage.removeItem(owner.startsWith('legacy:')?key:ownerKey(key,owner));releaseRenderedMemory(key,owner);}
export function hasUnpersistedRenderedRecovery(){return [...memory.values()].some(entries=>[...entries.values()].some(item=>!item.persisted));}
if(typeof window!=='undefined')window.addEventListener('beforeunload',event=>{if(hasUnpersistedRenderedRecovery()){event.preventDefault();event.returnValue=''}});
export {renderedRecoveryKey};
