import {useEffect,useState} from 'react';
import {parseFileLink} from './links';
import {useWorkbench} from '../state/context';

/** Fallback for displayed paths outside the richer file-link and tab menus. */
export function PathContextMenu(){
 const {notify}=useWorkbench();const [menu,setMenu]=useState<{path:string;x:number;y:number}|null>(null);
 useEffect(()=>{
  const close=()=>setMenu(null);
  const context=(event:MouseEvent)=>{
   if(event.defaultPrevented)return;const target=event.target as HTMLElement;
   if(target.closest('input,textarea,select,[contenteditable="true"]'))return;
   const explicit=target.closest<HTMLElement>('[data-copy-path]')?.dataset.copyPath;
   const text=explicit||target.textContent?.trim()||'';
   if(!/^(?:\/|~\/|file:\/\/)/.test(text)||text.includes('\n')||text.length>4096||/^\/\//.test(text))return;
   const path=parseFileLink(text,'')?.path;if(!path)return;
   event.preventDefault();setMenu({path,x:Math.max(8,Math.min(event.clientX,innerWidth-200)),y:Math.max(8,Math.min(event.clientY,innerHeight-60))});
  };
  const key=(event:KeyboardEvent)=>{if(event.key==='Escape')close()};
  document.addEventListener('contextmenu',context);document.addEventListener('pointerdown',close);window.addEventListener('keydown',key);window.addEventListener('workbench-dismiss-popovers',close);
  return()=>{document.removeEventListener('contextmenu',context);document.removeEventListener('pointerdown',close);window.removeEventListener('keydown',key);window.removeEventListener('workbench-dismiss-popovers',close)};
 },[]);
 return menu&&<div className="chat-link-menu" role="menu" style={{left:menu.x,top:menu.y}} onPointerDown={event=>event.stopPropagation()}><button role="menuitem" onClick={()=>{void navigator.clipboard.writeText(menu.path).then(()=>notify('Path copied')).catch(()=>notify('Clipboard is unavailable'));setMenu(null)}}>Copy Path</button></div>;
}
