import React,{useMemo,useState} from "react";
import {isRootChat,providerLabel} from "./chats";
import {relativeDate} from "./Sidebar";
import "./directories.css";
export function Recent({data,open}:any){
 const [query,setQuery]=useState(""),[provider,setProvider]=useState("");
 const chats=useMemo(()=>{const q=query.trim().toLowerCase();return (data?.chats||[]).filter((c:any)=>isRootChat(c)&&!c.automation&&!c.archived&&(!provider||c.source===provider)&&(!q||[c.title,c.description,c.first_prompt].join(" " ).toLowerCase().includes(q))).sort((a:any,b:any)=>String(b.ts_last||"").localeCompare(String(a.ts_last||"")));},[data,query,provider]);
 return <main className="project-directory"><div className="directory-inner"><h1>Recent chats</h1><div className="directory-controls"><input aria-label="Search recent chats" placeholder="Search conversations" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Filter recent chats by agent" value={provider} onChange={e=>setProvider(e.target.value)}><option value="">All agents</option><option value="codex">Codex</option><option value="claude">Claude</option></select></div><div className="directory-results">{chats.map((c:any)=><button className="directory-chat" key={c.key} onClick={()=>open({kind:"chat",key:c.key,title:c.title,meta:c},"tab")}><span>{c.title}</span><small>{providerLabel(c)} · {relativeDate(c.ts_last)}</small></button>)}{!chats.length&&<p className="directory-empty">No matching conversations.</p>}</div></div></main>;
}
