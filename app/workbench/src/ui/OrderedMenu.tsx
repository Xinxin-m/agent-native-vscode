import {useLayoutEffect,useRef,useState, type ReactNode} from 'react';
import {ChevronRight, GripVertical} from 'lucide-react';

export type MenuItem = {id:string, content:ReactNode, separator?:boolean};

const dragType = 'application/library-menu-item';

function savedOrder(key:string):string[]{
 try {
  const order:unknown = JSON.parse(localStorage.getItem(key) || 'null');
  return Array.isArray(order) ? order.filter(id=>typeof id==='string') : [];
 }catch{return []}
}

export function moveMenuItem(ids:string[],from:string,to:string,after:boolean){
 if(from===to||!ids.includes(from)||!ids.includes(to))return ids;
 const next=ids.filter(id=>id!==from);
 next.splice(next.indexOf(to)+Number(after),0,from);
 return next;
}

export function OrderedMenu({name,items,first}:{name:string,items:MenuItem[],first?:string}){
 const key = 'workbench-menu-order:'+name;
 const [order,setOrder] = useState(()=>savedOrder(key));
 const [drag,setDrag] = useState('');
 const [target,setTarget] = useState<{id:string,after:boolean}>();
 const ids = [...new Set([...(first?[first]:[]),...order,...items.map(item=>item.id)])];
 const byId = new Map(items.map(item=>[item.id,item]));
 function move(from:string,to:string,after:boolean){
  if(from===first||to===first||!byId.has(from)||!byId.has(to))return;
  const next=moveMenuItem(ids,from,to,after);
  if(next===ids)return;
  setOrder(next);
  try{localStorage.setItem(key,JSON.stringify(next))}catch{}
 }
 const visible = ids.flatMap(id=>byId.has(id)?[byId.get(id)!]:[]);
 return <>{visible.map(({id,content,separator},index)=><div key={id} className="ordered-menu-row" draggable={id!==first}
  data-insert={target?.id===id?(target.after?'after':'before'):undefined}
  onDragStart={e=>{
   if((e.target as Element).closest('.ordered-menu-row')!==e.currentTarget)return;
   e.stopPropagation();
   e.dataTransfer.setData(dragType,name);
   e.dataTransfer.effectAllowed='move';
   setDrag(id);
  }}
  onDragOver={e=>{
   if(!drag||!e.dataTransfer.types.includes(dragType)||(e.target as Element).closest('.ordered-menu-row')!==e.currentTarget)return;
   e.preventDefault();e.stopPropagation();
   const rect=e.currentTarget.getBoundingClientRect();
   setTarget({id,after:e.clientY>rect.top+rect.height/2});
  }}
  onDragLeave={e=>{
   if(e.relatedTarget instanceof Node&&e.currentTarget.contains(e.relatedTarget))return;
   setTarget(previous=>previous?.id===id?undefined:previous);
  }}
  onDrop={e=>{
   if(!drag||e.dataTransfer.getData(dragType)!==name||(e.target as Element).closest('.ordered-menu-row')!==e.currentTarget)return;
   e.preventDefault();e.stopPropagation();
   const rect=e.currentTarget.getBoundingClientRect();
   move(drag,id,e.clientY>rect.top+rect.height/2);
   setDrag('');setTarget(undefined);
  }}
  onDragEnd={()=>{setDrag('');setTarget(undefined)}}
  onKeyDown={e=>{
   if(!e.altKey||!['ArrowUp','ArrowDown'].includes(e.key)||(e.target as Element).closest('.ordered-menu-row')!==e.currentTarget)return;
   const after=e.key==='ArrowDown',next=visible[index+(after?1:-1)];
   if(next){e.preventDefault();move(id,next.id,after)}
  }}>
  {separator&&index>0&&<div className="menu-separator"/>}
  {content}
  {id!==first&&<span className="menu-drag-handle" title="Drag to reorder. Option and ↑ or ↓ also works." aria-hidden="true"><GripVertical size={13}/></span>}
 </div>)}</>;
}

export function MenuFlyout({label,children}:{label:string,children:ReactNode}){
 const [open,setOpen]=useState(false);
 const trigger=useRef<HTMLButtonElement>(null);
 const submenu=useRef<HTMLDivElement>(null);
 useLayoutEffect(()=>{
  if(!open||!trigger.current||!submenu.current)return;
  const row=trigger.current.getBoundingClientRect(),menu=submenu.current;
  const {width,height}=menu.getBoundingClientRect();
  const right=innerWidth-row.right-8,left=row.left-8;
  let x:number,y:number;
  if(right>=width||left>=width){
   x=right>=width?row.width:-width;
   y=Math.min(0,innerHeight-row.top-height-8);
  }else{
   x=Math.max(8-row.left,Math.min(0,innerWidth-row.left-width-8));
   y=row.bottom+height+8<=innerHeight?row.height:-height;
  }
  menu.style.left=x+'px';
  menu.style.top=y+'px';
 },[open,children]);
 return <div className="menu-flyout"
  onPointerEnter={e=>{if(e.pointerType==='mouse')setOpen(true)}}
  onPointerLeave={()=>setOpen(false)}
  onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}}
  onKeyDown={e=>{
   if(e.altKey||e.target!==trigger.current||!['ArrowRight','ArrowDown'].includes(e.key))return;
   e.preventDefault();
   setOpen(true);
   requestAnimationFrame(()=>submenu.current?.querySelector<HTMLButtonElement>('button')?.focus());
  }}>
  <button ref={trigger} role="menuitem" aria-haspopup="menu" aria-expanded={open} onClick={()=>setOpen(true)}>{label}<ChevronRight size={13}/></button>
  {open&&<div ref={submenu} className="menu-flyout-items" role="menu" aria-label={label}>{children}</div>}
 </div>;
}
