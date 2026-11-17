/** Merge paged project searches while old backends still filter one project only. */
type Row={key:string;[key:string]:any};
type Source={project:string;cursor:string;done:boolean;rows:Row[]};
type State={sources:Source[];seen:string[]};
export async function searchProjectPage(projects:string[],fetchPage:(project:string,cursor:string)=>Promise<any>,chats:Row[],cursor='',limit=50){
 if(projects.length<2)return fetchPage(projects[0]||'',cursor);
 const state:State=cursor?JSON.parse(cursor):{sources:projects.map(project=>({project,cursor:'',done:false,rows:[]})),seen:[]};
 const seen=new Set(state.seen),metadata=new Map(chats.map(row=>[row.key,row]));
 const compare=(a:Row,b:Row)=>String(metadata.get(b.key)?.ts_last||'').localeCompare(String(metadata.get(a.key)?.ts_last||''))||a.key.localeCompare(b.key);
 let truncated=false;
 async function fill(source:Source){
  while(!source.rows.length&&!source.done){
   const page=await fetchPage(source.project,source.cursor);
   truncated ||= !!page.truncated;
   source.rows=(page.results||[]).filter((row:Row)=>!seen.has(row.key));
   const next=page.next_cursor;
   source.done=!next||String(next)===source.cursor;
   source.cursor=next?String(next):'';
  }
 }
 const results:Row[]=[];
 while(results.length<limit){
  // Every stream needs a head before choosing the newest global result.
  for(const source of state.sources)await fill(source);
  const ready=state.sources.filter(source=>source.rows.length).sort((a,b)=>compare(a.rows[0],b.rows[0]));
  if(!ready.length)break;
  const row=ready[0].rows.shift()!;
  if(seen.has(row.key))continue;
  seen.add(row.key);results.push(row);
 }
 state.seen=[...seen];
 return {results,next_cursor:state.sources.some(source=>source.rows.length||!source.done)?JSON.stringify(state):null,truncated};
}
