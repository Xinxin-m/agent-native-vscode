import {useEffect,useRef} from 'react';
import type {SlashCommand} from './slashCommands';
export function SlashPicker({items,index,onChoose,id}:{items:SlashCommand[];index:number;onChoose:(item:SlashCommand)=>void;id:string}){
 const root=useRef<HTMLDivElement>(null);
 useEffect(()=>{root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'})},[index]);
 return <div className="chat-slash-picker" id={id} role="listbox" aria-label="Agent commands" ref={root}>{items.length?items.map((c,i)=><button type="button" role="option" id={id+'-'+i} aria-selected={i===index} aria-disabled={!!c.disabled} key={c.name} onMouseDown={e=>e.preventDefault()} onClick={()=>onChoose(c)}><span><strong>/{c.name}</strong>{c.argumentHint&&<small>{c.argumentHint}</small>}</span><span>{c.disabled||c.description}</span></button>):<p>No matching commands</p>}</div>;
}
