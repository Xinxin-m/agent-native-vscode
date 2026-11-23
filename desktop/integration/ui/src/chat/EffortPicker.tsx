import {useId} from 'react';
import {ModelUpdates} from './ModelUpdates';

type Effort = string | {id:string; name?:string};
type Choice = {id:string; name:string};

function effortName(id:string){
 return id==='xhigh'?'Extra high':id.charAt(0).toUpperCase()+id.slice(1);
}

export function effortChoices(advertised:Effort[]=[]):Choice[]{
 const choices=new Map<string,Choice>();
 for(const effort of advertised){
  const id=typeof effort==='string'?effort:effort.id;
  if(id&&!choices.has(id))choices.set(id,{id,name:(typeof effort==='string'?'':effort.name)||effortName(id)});
 }
 return [...choices.values()];
}

export function EffortPicker({choices,value,disabled,onChange}:{choices:Choice[];value:string;disabled:boolean;onChange:(value:string)=>void}){
 const id=useId();
 const unavailable=value&&!choices.some(choice=>choice.id===value);
 // The footer sits here so every model picker offers it, also when a model has no effort levels.
 if(!choices.length)return <ModelUpdates compact/>;
 return <><div className="chat-picker-effort">
  <span id={id}>Thinking</span>
  {choices.length===1&&!unavailable
   ? <span className="chat-picker-fixed">{choices[0].name}</span>
   : <div className="chat-effort-options" role="radiogroup" aria-labelledby={id} aria-disabled={disabled||undefined}>
      {[{id:'',name:'Default'},...choices].map(choice=><label key={choice.id}>
       <input type="radio" name={id} value={choice.id} checked={value===choice.id} disabled={disabled} onChange={()=>onChange(choice.id)}/>
       <span>{choice.name}</span>
      </label>)}
      {unavailable&&<small>Saved effort unavailable: {effortName(value)}</small>}
     </div>}
 </div><ModelUpdates compact/></>;
}
