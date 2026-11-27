import type {Resource} from '../state/types';

const fileLinkType='application/library-chat-file-link';
let source:Element|null=null;

export function startChatFileDrag(event:DragEvent,link:Element,resource:Resource){
 const data=event.dataTransfer;
 if(!data)return;
 // An anchor otherwise also carries a URL that the browser can open on drop.
 data.clearData();
 data.setData('application/library-resource',JSON.stringify(resource));
 data.setData(fileLinkType,'1');
 data.effectAllowed='copy';
 source=link;
 const document=link.ownerDocument;
 const handlers=new AbortController();
 let target:EventTarget|null=null;
 document.addEventListener('dragenter',event=>{target=event.target;},{capture:true,signal:handlers.signal});
 document.addEventListener('dragend',event=>{
  handlers.abort();
  source=null;
  // On Escape the OS can end the source drag without sending dragleave to the target.
  if(event.dataTransfer?.dropEffect==='none'){
   target?.dispatchEvent(new DragEvent('dragleave',{bubbles:true}));
  }
 },{capture:true,once:true,signal:handlers.signal});
}

export function ignoresChatFileDrop(event:DragEvent){
 if(!event.dataTransfer?.types.includes(fileLinkType))return false;
 if(event.type==='dragend')return true;
 const link=source;
 return Boolean(link?.isConnected&&link.ownerDocument.elementsFromPoint(event.clientX,event.clientY).some(element=>link.contains(element)));
}
