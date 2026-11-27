"use strict";
function targetColumn(columns,active,destination,limit=3){
 const ordered=[...new Set(columns)].sort((a,b)=>a-b);
 if(!['right','below'].includes(destination))return active||ordered[0]||1;
 if(limit>0&&ordered.length>=limit)return ordered[Math.min(limit,ordered.length)-1];
 return (ordered.at(-1)||1)+1;
}
// Explicit placements still belong to their caller. Ordinary utility clicks follow the saved preference.
function utilityDestination(kind,destination,column,preferences){
 if(column||destination&&destination!=='tab'||!['settings','accounts','notifications','recent','projects'].includes(kind))return undefined;
 return preferences.utilityPopups!==false;
}
function resourceKey(r){
 if(r.kind==='chat'){if(!r.key&&!r.draftId)throw Error('Conversation identity required');return 'chat:'+(r.key||r.draftId);}
 if(r.kind==='file')return 'file:'+(r.host_id||'mac')+':'+(r.path||r.file_id);
 if(r.kind==='files')return 'files:'+(r.root_id||'');
 if(r.kind==='notifications')return 'notifications:'+(r.meta?.view==='history'?'history':'');
 return r.kind+':'+(r.url||'');
}
function fileTargetPane(groups,currentId){
 const current=groups.find(g=>g.id===currentId);if(!current)return {split:'right'};const r=current.rect,near=g=>Math.abs(g.rect.top-r.top);
 const right=groups.filter(g=>g.id!==currentId&&g.rect.left>=r.right-3).sort((a,b)=>a.rect.left-b.rect.left||near(a)-near(b))[0];if(right)return {group:right.id};
 if(groups.length===1)return {split:'right'};
 const left=groups.filter(g=>g.id!==currentId&&g.rect.right<=r.left+3).sort((a,b)=>b.rect.right-a.rect.right||near(a)-near(b))[0];
 const stacked=groups.filter(g=>g.id!==currentId).sort((a,b)=>near(a)-near(b))[0];
 return {group:(left||stacked||current).id};
}
// vscode.getEditorLayout (orientation 0 = side by side, nested levels alternate) as rectangles in a 1000 px box; leaves in grid order are view columns 1..n.
function layoutRects(layout){
 const out=[];
 (function place(groups,across,box){
  const total=groups.reduce((sum,g)=>sum+(g.size||1),0);let at=across?box.left:box.top;
  for(const g of groups){const extent=(g.size||1)/total*(across?box.right-box.left:box.bottom-box.top);
   const rect=across?{...box,left:at,right:at+extent}:{...box,top:at,bottom:at+extent};at+=extent;
   if(g.groups?.length)place(g.groups,!across,rect);else out.push({id:out.length+1,rect});}
 })(layout.groups,layout.orientation===0,{left:0,right:1000,top:0,bottom:1000});
 return out;
}
async function besideFileColumn(vscode,uri){
 const native=await vscode.commands.executeCommand('_library.besideFileColumn',uri).catch(error=>{if(!/command .*not found/i.test(error.message))throw error;});
 if(Number.isInteger(native)&&native>0)return native;
 const groups=vscode.window.tabGroups;
 const shown=groups.all.find(group=>group.tabs?.some(tab=>tab.input?.uri?.toString()===uri.toString()));
 if(shown)return shown.viewColumn;
 if(groups.all.length>1){
  const layout=await vscode.commands.executeCommand('vscode.getEditorLayout');
  const choice=fileTargetPane(layoutRects(layout),groups.activeTabGroup.viewColumn);
  if('group' in choice)return choice.group;
 }
 await vscode.commands.executeCommand('workbench.action.newGroupRight');
 return groups.activeTabGroup.viewColumn;
}
module.exports={utilityDestination,besideFileColumn,targetColumn,resourceKey,fileTargetPane,layoutRects};
