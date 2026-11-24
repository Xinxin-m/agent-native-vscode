/** Preserve server ordering and the first useful snippet across overlapping pages. */
export function uniqueChats<T extends {key:string}>(chats:T[]):T[]{const seen=new Set<string>();return chats.filter(chat=>{if(!chat.key||seen.has(chat.key))return false;seen.add(chat.key);return true;});}
export function providerLabel(chat:{source?:string;provider?:string;tool?:string;key?:string}){const provider=chat.source||chat.provider||chat.tool||chat.key?.split('-')[0];return provider==='claude'?'Claude':provider==='codex'?'Codex':'Unknown provider';}
export function searchParts(value:any,query:string):{text:string;match:boolean}[]{const text=String(value||''),needle=query.trim();if(!needle)return [{text,match:false}];const expression=new RegExp(needle.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'giu'),parts:{text:string;match:boolean}[]=[];let end=0;for(const match of text.matchAll(expression)){if(match.index!>end)parts.push({text:text.slice(end,match.index),match:false});parts.push({text:match[0],match:true});end=match.index!+match[0].length;}if(end<text.length)parts.push({text:text.slice(end),match:false});return parts.length?parts:[{text,match:false}];}

export function isRootChat(chat:any){return !chat.is_child&&!chat.parent_key&&!chat.parent_agent_id&&!chat.parent_session_id&&(!chat.root_key||chat.root_key===chat.key);}

/** A query of at least 6 hex or dash characters, with or without the claude- / codex- prefix, matches a chat whose session ID equals it or starts with it. */
export function sessionIdQuery(query:string){const q=String(query||'').trim().toLowerCase().replace(/^(claude|codex)-/,'');return /^[0-9a-f-]{6,}$/.test(q)?q:'';}
export function matchesSessionId(chat:{key?:string;id?:string},query:string){const q=sessionIdQuery(query);if(!q)return false;const key=String(chat.key||'').toLowerCase().replace(/^(claude|codex)-/,''),id=String(chat.id||'').toLowerCase();return key.startsWith(q)||(!!id&&id.startsWith(q));}
export function historyChats(chats:any[],provider:string,query:string){const needle=query.trim().toLowerCase(),byId=(c:any)=>matchesSessionId(c,query);return uniqueChats(chats).filter(c=>(byId(c)||isRootChat(c)&&!c.automation&&(!needle||[c.title,c.description,c.first_prompt].join(' ').toLowerCase().includes(needle)))&&(!provider||(c.source||c.key?.split('-')[0])===provider)).sort((a,b)=>Number(byId(b))-Number(byId(a))||String(b.ts_last).localeCompare(String(a.ts_last))).slice(0,80);}

export type RowStatus='waiting'|'working'|'failed'|'new'|null;
/** One mark per row: running (blue turning circle), waiting for her (blue dot), failed (red dot), a finished result she has not seen (green dot). Live idle has no mark. */
/** One mark per chat (user request 2026-11-23; server: sidebar.resolve): failed > waiting > working > new. A failed chat is never also running. */
export function rowStatus(live:string|null|undefined,unseen:boolean|undefined,local:{awaiting?:boolean;running?:boolean;failed?:boolean}={}):RowStatus{
 if(local.failed)return 'failed';
 if(live==='waiting'||local.awaiting)return 'waiting';
 if(live==='working'||local.running)return 'working';
 if(unseen)return 'new';
 return null;
}
export const statusWords:Record<string,string>={waiting:'Waiting for your decision',working:'Running',failed:'Failed',new:'Finished, not seen yet'};
/** "12 min ago" or "3 h ago" within 24 hours of the last activity, otherwise the date. */
export function relativeTime(value:any,now=Date.now()){
 const time=typeof value==='number'?value*1000:Date.parse(value);if(!Number.isFinite(time))return '';
 const seconds=Math.max(0,(now-time)/1000);
 if(seconds<60)return 'just now';
 if(seconds<3600)return Math.floor(seconds/60)+' min ago';
 if(seconds<86400)return Math.floor(seconds/3600)+' h ago';
 return new Date(time).toLocaleDateString(undefined,{month:'short',day:'numeric'});
}
export const TAB_TITLE_MAX=24;
export function cutTitle(title:string,max=TAB_TITLE_MAX){const text=String(title||'').replace(/\s+/g,' ').trim();return text.length>max?text.slice(0,max).trimEnd()+'…':text;}
export type SidebarList='recent'|'starred'|'archive'|'unclassified';
export const sidebarLists:[SidebarList,string][]=[['recent','Recent'],['starred','Starred'],['archive','Archive'],['unclassified','Unclassified']];
export function inSidebarList(chat:any,list:string){
 if(list==='starred')return !!chat.starred&&!chat.archived;
 if(list==='archive')return !!chat.archived;
 if(list==='unclassified')return !chat.project_id&&!chat.archived&&!chat.is_child;
 return !chat.archived;
}
