import React,{useMemo,useState} from "react";
import {providerLabel} from "./chats";
import {directoryChats} from "./directoryScope";
import {WorkspaceSelector,useWorkspaceScope} from "./WorkspaceSelector";
import {relativeDate} from "./Sidebar";
import "./directories.css";
export function Recent({data,open}:any){
 const [query,setQuery]=useState(""),[provider,setProvider]=useState("");
 const {roots,choice}=useWorkspaceScope();
 const scoped=useMemo(()=>directoryChats(data?.chats||[],{roots,choice}),[data,roots,choice]);
 const q=query.trim().toLowerCase();
 const chats=scoped.filter(c=>(!provider||c.source===provider)&&(!q||[c.title,c.description,c.first_prompt].join(" ").toLowerCase().includes(q)));
 return <main className="project-directory"><div className="directory-inner"><h1>Recent chats</h1><div className="directory-controls"><input aria-label="Search recent chats" placeholder="Search conversations" value={query} onChange={e=>setQuery(e.target.value)}/><WorkspaceSelector label="Recent workspace" className="directory-workspace"/><select aria-label="Filter recent chats by agent" value={provider} onChange={e=>setProvider(e.target.value)}><option value="">All agents</option><option value="codex">Codex</option><option value="claude">Claude</option></select></div><div className="directory-results">{chats.map((c:any)=><button className="directory-chat" key={c.key} onClick={()=>open({kind:"chat",key:c.key,title:c.title,meta:c},"tab")}><span>{c.title}</span><small>{providerLabel(c)} · {relativeDate(c.ts_last)}</small></button>)}{!chats.length&&<p className="directory-empty">No matching conversations.</p>}</div></div></main>;
}
