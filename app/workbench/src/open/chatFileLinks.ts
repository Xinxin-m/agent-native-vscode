import type {Resource} from '../state/types';
import {parseRenderedLink,resourceId} from './links';

export interface FileSequence {
 files:Resource[];
 index:number;
}

export function chatFileLink(element:Element):Resource|null{
 const markdown=element.closest<HTMLElement>('.workbench-markdown');
 if(!markdown)return null;
 const href=element.getAttribute('href')||element.getAttribute('data-local-image');
 if(!href)return null;
 return parseRenderedLink(href,markdown.dataset.cwd||'',markdown.dataset.host||'mac',{chatKey:markdown.dataset.chatKey});
}

export function withChatFileSequence(element:Element,resource:Resource):Resource{
 const reading=element.closest('.chat-reading');
 if(resource.kind!=='file'||!reading)return resource;
 const files:Resource[]=[];
 const seen=new Map<string,number>();
 let index=-1;
 for(const link of reading.querySelectorAll('.workbench-markdown a[href],.workbench-markdown img[data-local-image]')){
  if(link.closest('.chat-reading')!==reading)continue;
  const file=chatFileLink(link);
  if(file?.kind!=='file')continue;
  const identity=JSON.stringify([resourceId(file),file.line,file.page,file.anchor]);
  if(!seen.has(identity)){
   seen.set(identity,files.length);
   files.push(file);
  }
  if(link===element)index=seen.get(identity)!;
 }
 return index>=0&&files.length>1?{...resource,meta:{...resource.meta,fileSequence:{files,index} satisfies FileSequence}}:resource;
}
