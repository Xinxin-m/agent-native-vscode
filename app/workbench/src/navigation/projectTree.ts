/** Parent project views include descendant conversations without duplicating assignments. */
export function projectMembers(projects:any[],id:string){
 const ids=new Set([id]);let changed=true;
 while(changed){changed=false;for(const p of projects)if(ids.has(p.parent_id)&&!ids.has(p.id)){ids.add(p.id);changed=true;}}
 return ids;
}
export function projectTree(projects:any[],all:any[],assignments:Record<string,string>,eligibility:any[]=all){
 const rows=projects.map(p=>{const ids=projectMembers(projects,p.id);const chats=all.filter(c=>!c.archived&&ids.has(assignments[c.key]));return {...p,chats,last:chats[0]?.ts_last||'',active:!p.archived&&(!!p.pinned||eligibility.filter(c=>!c.archived&&ids.has(assignments[c.key])).length>=5&&chats.length>0&&Date.now()-Date.parse(chats[0]?.ts_last||'')<7*86400000)};}).sort((a,b)=>String(b.last).localeCompare(String(a.last)));
 const output:any[]=[];const seen=new Set();
 function append(p:any,depth:number){if(seen.has(p.id))return;seen.add(p.id);output.push({...p,depth});for(const child of rows.filter(c=>c.parent_id===p.id))append(child,depth+1);}
 for(const p of rows.filter(p=>!rows.some(parent=>parent.id===p.parent_id)))append(p,0);
 for(const p of rows)append(p,0);
 return output;
}
