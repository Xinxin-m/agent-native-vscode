// Capture outside clicks before child controls can stop bubbling; native hosts also signal pane changes.
export function dismissPopover(element:()=>HTMLElement|null,close:()=>void,doc:Document=document,win:Window=window){
 const capture={capture:true};
 const outside=(event:Event)=>{if(!element()?.contains(event.target as Node))close();};
 const key=(event:KeyboardEvent)=>{if(event.key==='Escape')close();};
 const hidden=()=>{if(doc.hidden)close();};
 doc.addEventListener('pointerdown',outside,capture);doc.addEventListener('visibilitychange',hidden);
 win.addEventListener('keydown',key);win.addEventListener('blur',close);win.addEventListener('workbench-dismiss-popovers',close);
 return()=>{doc.removeEventListener('pointerdown',outside,capture);doc.removeEventListener('visibilitychange',hidden);win.removeEventListener('keydown',key);win.removeEventListener('blur',close);win.removeEventListener('workbench-dismiss-popovers',close);};
}
