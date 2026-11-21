export type WorkspaceRoot={id?:string;root_id?:string;path:string;host_id?:string;label?:string;scope_known?:boolean};
const hostId=(value:any)=>typeof value==='string'&&value.trim()?(value==='local'?'mac':value):'';
export function workspacePath(value:any){
 if(typeof value!=='string'||!value.startsWith('/')||value.includes('\0'))return '';
 const out:string[]=[];for(const part of value.split('/')){if(!part||part==='.')continue;if(part==='..')out.pop();else out.push(part);}return '/'+out.join('/');
}
export const workspaceKey=(root:WorkspaceRoot)=>JSON.stringify([hostId(root.host_id),workspacePath(root.path)]);
export function workspaceRoots(rows:WorkspaceRoot[]){const seen=new Set<string>();return rows.filter(root=>{const key=workspaceKey(root);if(root.scope_known===false||!hostId(root.host_id)||!workspacePath(root.path)||seen.has(key))return false;seen.add(key);return true;});}
// 'entire' is All chats, the default since November 20, 2026. A choice saved before then under the
// legacy key keeps a specific workspace, but its old implicit 'added' default becomes All chats once.
export function savedWorkspaceChoice(current:string|null|undefined,legacy:string|null|undefined){
 if(current)return current;
 return !legacy||legacy==='added'?'entire':legacy;
}
export function effectiveWorkspaceChoice(saved:string,roots:WorkspaceRoot[]){
 if(saved==='added'&&roots.length)return 'added';
 return roots.some(root=>workspaceKey(root)===saved)?saved:'entire';
}
// Source-location paths are transcript copies. A multi-node copy list does not associate cwd with either host.
export function chatWorkspace(chat:any){
 const path=workspacePath(chat?.execution_cwd||chat?.cwd);if(!path)return null;
 let host=hostId(chat.execution_host)||hostId(chat.host_id);
 if(!host){const nodes=[...new Set(String(chat.node||'').split('+').map(hostId).filter(Boolean))];if(nodes.length!==1)return null;host=nodes[0];}
 return {host,path};
}
export function inWorkspace(chat:any,roots:WorkspaceRoot[],choice='added'){
 if(choice==='entire'||!roots.length)return true;
 const location=chatWorkspace(chat);if(!location)return false;
 return roots.some(root=>(choice==='added'||workspaceKey(root)===choice)&&hostId(root.host_id)===location.host&&(location.path===workspacePath(root.path)||location.path.startsWith(workspacePath(root.path).replace(/\/$/,'')+'/')));
}
// Root chats hidden by a narrowed scope that are newer than the newest chat it shows.
export function newerOutsideWorkspace(chats:any[],roots:WorkspaceRoot[],choice:string){
 if(choice==='entire'||!roots.length)return 0;
 let newest='';const outside:string[]=[];
 for(const chat of chats){const ts=String(chat?.ts_last||'');if(inWorkspace(chat,roots,choice)){if(ts>newest)newest=ts;}else outside.push(ts);}
 return outside.filter(ts=>ts>newest).length;
}
// Skip unrelated message-search pages, retaining the server cursor when a bounded pass has more to inspect.
export async function scopedWorkspaceSearch(fetchPage:(cursor:string)=>Promise<any>,chats:any[],roots:WorkspaceRoot[],choice:string,cursor=''){
 const metadata=new Map(chats.map(chat=>[chat.key,chat]));let page:any;
 for(let i=0;i<8;i++){page=await fetchPage(cursor);const results=(page.results||[]).filter((row:any)=>inWorkspace(chatWorkspace(metadata.get(row.key))?metadata.get(row.key):row,roots,choice));const next=String(page.next_cursor||'');if(results.length||!next||next===cursor)return {...page,results};cursor=next;page={...page,results};}
 return page;
}
