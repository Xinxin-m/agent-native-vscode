export const PROJECT_MAP_ORIGIN='http://127.0.0.1:53962';
export function projectMapUrl(projectId:string,theme:string){const url=new URL('/',PROJECT_MAP_ORIGIN);url.searchParams.set('project',projectId);url.searchParams.set('embedded','1');url.searchParams.set('host','browser');url.searchParams.set('theme',['dark','light','beige','sage'].includes(theme)?theme:'light');return url.href;}
export function projectMapMessage(event:Pick<MessageEvent,'origin'|'source'|'data'>,source:MessageEventSource|null){
 if(!source||event.source!==source||event.origin!==PROJECT_MAP_ORIGIN)return null;
 const data=event.data;if(!data||typeof data!=='object')return null;
 if(data.type==='project-map:ready')return {type:'ready' as const};
 if(data.type==='project-map:open-session'&&typeof data.key==='string'&&/^(codex|claude)-[\w-]{1,240}$/.test(data.key))return {type:'session' as const,key:data.key};
 return null;
}
