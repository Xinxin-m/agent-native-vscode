export interface DraftContent {text:string;baseText:string;version?:string}
export interface FileDraft extends DraftContent {writer:string;updatedAt:number;storageKey:string;legacy?:boolean}
type DraftStorage=Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
export const draftPrefix='library.workbench.file-draft.';
const legacyPrefixes=['library.workbench.buffer.','library.workbench.recovery.'];
const valid=(value:any):value is DraftContent=>value&&typeof value.text==='string'&&typeof value.baseText==='string';
export function draftEventIdentity(key:string){if(key.startsWith(draftPrefix)){try{return JSON.parse(key.slice(draftPrefix.length))[0] as string;}catch{return null;}}for(const prefix of legacyPrefixes)if(key.startsWith(prefix))return key.slice(prefix.length);return null;}
export function createDraftRepository(storage:DraftStorage,writer:string){
 const ownKey=(id:string)=>draftPrefix+JSON.stringify([id,writer]);
 function list(id:string):FileDraft[]{const found:FileDraft[]=[];const keys:string[]=[];for(let i=0;i<storage.length;i++){const key=storage.key(i);if(key)keys.push(key);}for(const prefix of legacyPrefixes)if(storage.getItem(prefix+id)!==null)keys.push(prefix+id);
  for(const key of new Set(keys)){let owner='',legacy=false;if(key.startsWith(draftPrefix)){try{const [resource,author]=JSON.parse(key.slice(draftPrefix.length));if(resource!==id||typeof author!=='string')continue;owner=author;}catch{continue;}}else if(legacyPrefixes.some(p=>key===p+id)){owner=key.startsWith(legacyPrefixes[0])?'legacy buffer':'legacy recovery';legacy=true;}else continue;
   try{const value=JSON.parse(storage.getItem(key)||'null');if(valid(value))found.push({...value,writer:owner,updatedAt:Number((value as any).updatedAt)||0,storageKey:key,legacy});}catch{}
  }return found.sort((a,b)=>b.updatedAt-a.updatedAt||a.writer.localeCompare(b.writer));
 }
 function write(key:string,value:DraftContent,author=writer){storage.setItem(key,JSON.stringify({...value,writer:author,updatedAt:Date.now()}));}
 return {writer,list,save(id:string,value:DraftContent){write(ownKey(id),value);},clearOwn(id:string){storage.removeItem(ownKey(id));},archive(id:string,value:DraftContent){const author=writer+':recovery:'+crypto.randomUUID();write(draftPrefix+JSON.stringify([id,author]),value,author);},discard(id:string,key:string){if(list(id).some(d=>d.storageKey===key))storage.removeItem(key);}};
}
