// Pure rules for dragging files between workspace roots in the Files tree.
export type DragItem={rid:string,path:string,name:string,type:string,host:string};
export type Target={rid:string,path:string,host:string,label:string};

export const parentPath=(path:string)=>path.split('/').slice(0,-1).join('/');
export const place=(label:string,path:string)=>path?label+'/'+path:label;

// Returns why the drop cannot happen, or '' when the server should be asked.
export function refusal(items:DragItem[],target:Target,hostLabel:(id:string)=>string){
 const hosts=new Set([...items.map(i=>i.host),target.host]),sources=new Set(items.map(i=>i.host));
 if(hosts.size>1){if(hosts.size!==2||!hosts.has('mac')||sources.size!==1)return 'Transfer one direction at a time between this Mac and one remote host.';return '';}
 const other=[...hosts].find(h=>h!=='mac');if(other)return `Files on ${hostLabel(other)} are read-only within the remote workspace.`;
 const inside=items.find(i=>i.rid===target.rid&&(target.path===i.path||target.path.startsWith(i.path+'/')));
 return inside?`"${inside.name}" cannot be moved into itself.`:'';
}

// Items that actually change location; moving into the current parent is a no-op.
export const moving=(items:DragItem[],target:Target,copy:boolean)=>copy?items:items.filter(i=>!(i.rid===target.rid&&parentPath(i.path)===target.path));

// confirm: 'some' asks for folders or more than 5 items, 'always' for every move, 'never' not at all.
export function confirmation(items:DragItem[],target:Target,rootLabel:(rid:string)=>string,copy:boolean,confirm:'some'|'always'|'never'='some'){
 if(copy||confirm==='never'||confirm==='some'&&!(items.some(i=>i.type==='directory')||items.length>5))return '';
 const sources=[...new Set(items.map(i=>place(rootLabel(i.rid),parentPath(i.path))))];
 const what=items.length===1?`the ${items[0].type==='directory'?'folder':'file'} "${items[0].name}"`:`${items.length} items`;
 return `Move ${what} from ${sources.length===1?sources[0]:sources.length+' folders'} to ${place(target.label,target.path)}?`;
}

// A finished move in absolute paths; open tabs and every Files tree follow it.
export type Moved={from:string,to:string,root_id:string,from_host?:string,to_host?:string};
export function followMove(r:{kind:string,host_id?:string,path?:string},moves:Moved[]){
 if(r.kind!=='file'||!r.path)return;
 for(const m of moves)if((r.host_id||'mac')===(m.from_host||'mac')&&(r.path===m.from||r.path.startsWith(m.from+'/')))return {path:m.to+r.path.slice(m.from.length),root_id:m.root_id,...(m.to_host?{host_id:m.to_host}:{})};
}
