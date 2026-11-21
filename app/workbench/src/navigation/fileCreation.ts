// Resolve creation from the selected row before falling back to the current workspace.
export function creationLocation(roots:any[],rows:any[],selected:string[],single='',focus=''){
 const row=[...selected].reverse().map(key=>rows.find(r=>r.key===key)).find(Boolean);
 const root=roots.find(r=>(r.id||r.root_id)===(row?.rid||single||focus))||roots[0];
 if(!root)return null;
 const path=row?(row.type==='file'?row.path.slice(0,Math.max(0,row.path.lastIndexOf('/'))):row.path):'';
 return {rid:root.id||root.root_id,path,host:root.host_id||'mac',writable:(root.host_id||'mac')==='mac'&&root.access==='write'};
}
export function initialWorkspace(roots:any[],preferred=''){
 return roots.find(r=>(r.id||r.root_id)===preferred)||roots[0];
}
