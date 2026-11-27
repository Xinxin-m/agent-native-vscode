import React,{useEffect,useState} from 'react';
import {api,query} from '../api/client';
import {terminal,type RunState} from '../state/runs';

const activeLabels:Record<string,string>={starting:'Starting',working:'Running',waiting:'Waiting'};
type Child={id:string;state:string;title?:string;model?:string};
type TreeNode={key:string;is_child?:boolean;agent_id?:string;state?:string;activity?:string;title?:string;model_actual?:string;model_main?:string;models?:Record<string,number>};
type Tree={key:string;active_subagents?:Child[];nodes?:TreeNode[];edges?:{parent_key:string;child_key:string}[]};

export function childModel(node:TreeNode){
 if(node.model_actual!==undefined)return node.model_actual;
 const recorded=Object.keys(node.models||{});
 return node.model_main||(recorded.length===1?recorded[0]:'');
}

function indexedSubagents(tree?:Tree):Child[]{
 if(!tree)return [];
 if(tree.active_subagents)return tree.active_subagents;
 const descendants=new Set([tree.key]);
 let before=0;
 while(before!==descendants.size){
  before=descendants.size;
  for(const edge of tree.edges||[])if(descendants.has(edge.parent_key))descendants.add(edge.child_key);
 }
 return (tree.nodes||[]).flatMap(node=>{
  if(!node.is_child||node.key===tree.key||!descendants.has(node.key))return [];
  const state=node.state||(node.activity==='running'?'working':node.activity)||'unknown';
  return activeLabels[state]?[{id:node.agent_id||node.key,state,title:node.title,model:childModel(node)}]:[];
 });
}

export function activeSubagents(run:RunState,tree?:Tree){
 const children=new Map(indexedSubagents(tree).map(child=>[child.id,child]));
 let snapshot:Child[]|undefined;
 for(let i=run.events.length-1;i>=0;i--){
  const event=run.events[i];
  if(event.seq<=(run.run.last_seq||0))break;
  if(event.event==='state'&&Array.isArray(event.data?.subagents)){snapshot=event.data.subagents;break}
 }
 for(const child of (snapshot||run.run.subagents||[]) as Child[]){
  if(terminal(run.run.status)){
   // Suppress an in-flight tree response from the broker that just stopped.
   if(activeLabels[child.state])children.delete(child.id);
   continue;
  }
  const indexed=children.get(child.id);
  children.set(child.id,{...child,state:child.state==='unknown'?(indexed?.state||child.state):child.state,
   title:indexed?.title||child.title,model:indexed?.model||child.model});
 }
 return [...children.values()].filter(child=>activeLabels[child.state]);
}

export function useSubagentActivity(key:string|undefined,visible:boolean,run:RunState){
 const [tree,setTree]=useState<Tree>();
 useEffect(()=>{
  if(!key||!visible)return;
  let stopped=false,controller:AbortController|undefined;
  async function read(){
   if(document.hidden||controller)return;
   const request=new AbortController();
   controller=request;
   const abort=setTimeout(()=>request.abort(),10000);
   try{
    const result=await api(query('/api/session_tree',{key}),undefined,request.signal);
    if(!stopped)setTree(result);
   }catch{if(!stopped)setTree(undefined)}
   finally{clearTimeout(abort);controller=undefined}
  }
  void read();
  const timer=setInterval(read,12000);
  document.addEventListener('visibilitychange',read);
  return()=>{stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',read);controller?.abort()};
 },[key,visible]);
 return activeSubagents(run,tree?.key===key?tree:undefined);
}

export function SubagentActivity({children:agents}:{children:Child[]}){
 return agents.length>0?<ul aria-label="Active subagents" className="agent-activity-children">{agents.map(agent=>
  <li key={agent.id}><strong>{agent.title||'Subagent'}</strong><span> · {activeLabels[agent.state]}{agent.model&&' · '+agent.model}</span></li>)}</ul>:null;
}

export function CollapseActivity(){
 return <button type="button" className="activity-collapse" onClick={event=>{
  const section=event.currentTarget.closest('details');
  if(!section)return;
  section.open=false;
  section.querySelector('summary')?.focus();
 }}>Collapse agent activity</button>;
}
