import React,{useMemo,useState} from "react";
import {api} from "../api/client";
import {directoryProjects} from "./directoryScope";
import {WorkspaceSelector,useWorkspaceScope} from "./WorkspaceSelector";
import {relativeDate} from "./Sidebar";
import "./directories.css";
import {ProjectSuggestions} from "./ProjectSuggestions";
export function Projects({data,open,refresh,notify=()=>{}}:any){
 const [q,setQ]=useState(""),[scope,setScope]=useState("all"),[sort,setSort]=useState("date");
 const {roots,choice}=useWorkspaceScope();
 const scoped=useMemo(()=>directoryProjects(data?.projects?.projects||[],data?.chats||[],data?.projects?.assignments||{},{roots,choice}),[data,roots,choice]);
 const needle=q.trim().toLowerCase();
 const rows=scoped.filter(p=>(scope==="all"||(scope==="archived"?p.archived:!p.archived))&&p.name.toLowerCase().includes(needle));
 if(sort==="name")rows.sort((a,b)=>a.name.localeCompare(b.name));
 return <main className="project-directory"><div className="directory-inner"><h1>Projects</h1><div className="directory-controls"><input aria-label="Search projects" placeholder="Search projects" value={q} onChange={e=>setQ(e.target.value)}/><WorkspaceSelector label="Projects workspace" className="directory-workspace"/></div><ProjectSuggestions refresh={refresh} notify={notify}/><div className="directory-tabs">{["all","active","archived"].map(s=><button onClick={()=>setScope(s)} aria-pressed={scope===s} key={s}>{s[0].toUpperCase()+s.slice(1)}</button>)}</div><div className="directory-controls"><select aria-label="Sort projects" value={sort} onChange={e=>setSort(e.target.value)}><option value="date">Recent activity</option><option value="name">Name</option></select></div><div className="directory-results">{rows.map((p:any)=><details className="directory-project" key={p.id}><summary><span className="directory-title">{p.name}</span><span className="directory-meta">{p.chats.length} {p.chats.length===1?"chat":"chats"} · {p.last?relativeDate(p.last):"No activity"}</span></summary><div className="directory-actions"><button onClick={async()=>{try{await api("/api/project_catalog",{action:p.pinned?"unpin":"pin",id:p.id});await refresh();}catch(e){notify(e instanceof Error?e.message:String(e));}}}>{p.pinned?"Unpin project":"Pin project"}</button><button onClick={()=>open({kind:"project",projectId:p.id,title:p.name})}>Open project map</button></div><div className="directory-children">{p.chats.map((c:any)=><button className="directory-chat" key={c.key} onClick={()=>open({kind:"chat",key:c.key,title:c.title,meta:c})}><span>{c.title}</span><small>{relativeDate(c.ts_last)}</small></button>)}</div></details>)}{!rows.length&&<p className="directory-empty">No matching projects.</p>}</div></div></main>;
}
