import type {Resource} from '../state/types';
export function resourceId(r:Resource){return r.kind==='project'?'project:'+r.projectId:r.kind==='url'?'url:'+r.url:r.kind==='chat'?'chat:'+(r.key||r.draftId||'new'):r.kind==='file'?'file:'+(r.host_id||'mac')+':'+(r.path||r.file_id):r.kind==='files'?'files:'+(r.root_id||''):r.kind;}
export function encodeResource(r:Resource){return '#resource='+encodeURIComponent(JSON.stringify(r));}
export function copyableLink(resource:Resource,origin:string){
 if(resource.kind==='url')return resource.url||'';
 const {meta,...file}=resource;
 return new URL('/workbench',origin).href+encodeResource({...file,meta:{chat_key:meta?.chat_key}});
}
export function decodeResource(hash:string):Resource|null {
 try {
  const raw=new URLSearchParams(hash.replace(/^#/,'' )).get('resource');if(!raw)return null;
  const r:Resource=JSON.parse(raw);
  if(!['chat','file','url','welcome','settings','overview','project','recent','projects','sessions','files','notifications'].includes(r.kind))return null;
  if(r.kind==='project'&&(typeof r.projectId!=='string'||!r.projectId||r.projectId.length>200))return null;
  if(r.kind==='url'&&(!r.url||!/^https?:\/\//i.test(r.url)))return null;
  if(r.kind==='file'&&typeof r.path!=='string'&&!r.file_id)return null;
  if(r.kind==='chat'&&!r.key&&!r.draftId)return null;
  if(r.kind==='file'&&r.host_id&&r.host_id!=='mac'&&r.host_id!=='local'&&!r.root_id&&!r.file_id&&typeof r.meta?.chat_key==='string'){
   return {...r,meta:{...r.meta,workbench_link:true}};
  }
  return r;
 }catch{return null;}
}
export function parseFileLink(value:string,cwd:string,host_id='mac'):Resource|null {
 if(value.startsWith('#'))return null;
 if(/^[a-z][a-z0-9+.-]*:/i.test(value)&&!/^file:\/\//i.test(value)&&!/^.+\.[\w]+:\d+(?::\d+)?$/.test(value))return null;
 let path=value;let page:number|undefined,anchor:string|undefined;const fragment=path.match(/#(?!L\d+$)(.+)$/);if(fragment){path=path.slice(0,fragment.index);const match=fragment[1].match(/^page=(\d+)$/);if(match)page=Math.max(1,Number(match[1]));else try{anchor=decodeURIComponent(fragment[1]);}catch{anchor=fragment[1];}}try{path=decodeURIComponent(path);}catch{}
 if(/^(?:https?:|javascript:|data:|vbscript:|mailto:|\/\/)/i.test(path)||path.startsWith('#'))return null;
 if(/^[a-z][a-z0-9+.-]*:/i.test(path)&&!/^file:\/\//i.test(path)&&!/^.+\.[\w]+:\d+(?::\d+)?$/.test(path))return null;
 path=path.replace(/^file:\/\//i,'');
 let line:number|undefined;
 const loc=path.match(/(?::(\d+)(?::\d+)?|#L(\d+))$/);
 if(loc){line=Number(loc[1]||loc[2]);path=path.slice(0,loc.index);}
 if(path.startsWith('~/')){const home=cwd.match(/^\/(?:Users|home)\/[^/]+/);if(!home)return null;path=home[0]+path.slice(1);}
 if(!path.startsWith('/'))path=(cwd||'')+'/'+path;
 const parts:string[]=[];for(const p of path.split('/')){if(p==='..')parts.pop();else if(p&&p!=='.')parts.push(p);}
 return {kind:'file',host_id,path:'/'+parts.join('/'),line,...(page?{page}:{}),...(anchor?{anchor}:{}),title:parts.at(-1)};
}

export function parseRenderedLink(value:string,cwd:string,host_id='mac',
 {origin=globalThis.location?.origin,chatKey}:{origin?:string;chatKey?:string}={}):Resource|null {
 const web=/^https?:\/\//i.test(value)||value.startsWith('//');
 if(web||/^\/workbench\/?[?#]/.test(value)){
  try{
   const url=new URL(value,origin||(value.startsWith('//')?'https://localhost':undefined));
   if(!['http:','https:'].includes(url.protocol))return null;
   if(url.origin===origin&&['/workbench','/workbench/'].includes(url.pathname)){
    const file=decodeResource(url.hash);
    if(file?.kind==='file'){
     const key=file.meta?.chat_key;
     return {...file,meta:typeof key==='string'?{chat_key:key,...(file.meta.workbench_link===true?{workbench_link:true}:{})}:undefined};
    }
   }
   if(web)return {kind:'url',url:url.href,title:url.hostname};
  }catch{if(web)return null;}
 }
 const file=parseFileLink(value,cwd,host_id);
 return file&&chatKey?{...file,meta:{chat_key:chatKey}}:file;
}

export function isInlineLink(text:string){return /^(?:https?:\/\/\S+|(?:\/|\.\.?\/|~\/|file:\/\/).+)$/.test(text)||(!/[\s{}()[\]<>="'`]/.test(text)&&/^(?:[\w.@-]+\/)*[\w.@-]+\.(?:md|mdx|txt|html?|pdf|json|csv|tsv|tsx?|jsx?|py|ipynb|ya?ml|toml|tex|svg|png|jpe?g|webp|gif|sh|css|scss|sql|rs|go|java|c|cpp|h|rb|r)(?::\d+(?::\d+)?)?(?:#.*)?$/i.test(text));}
