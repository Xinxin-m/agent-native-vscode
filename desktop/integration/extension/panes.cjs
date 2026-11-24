"use strict";
function targetColumn(columns,active,destination,limit=3){
 const ordered=[...new Set(columns)].sort((a,b)=>a-b);
 if(!['right','below'].includes(destination))return active||ordered[0]||1;
 if(limit>0&&ordered.length>=limit)return ordered[Math.min(limit,ordered.length)-1];
 return (ordered.at(-1)||1)+1;
}
function resourceKey(r){
 if(r.kind==='chat'){if(!r.key&&!r.draftId)throw Error('Conversation identity required');return 'chat:'+(r.key||r.draftId);}
 if(r.kind==='file')return 'file:'+(r.host_id||'mac')+':'+(r.path||r.file_id);
 if(r.kind==='files')return 'files:'+(r.root_id||'');
 if(r.kind==='notifications')return 'notifications:'+(r.meta?.view==='history'?'history':'');
 return r.kind+':'+(r.url||'');
}
// A PDF opens beside the current pane: the nearest pane to its right, else a new right split, else (at the pane limit) the nearest pane to its left.
// Same rule as chat-dashboard/workbench/src/state/workspace.ts pdfTargetPane.
function pdfTargetPane(groups,currentId,limit=4){
 const current=groups.find(g=>g.id===currentId);if(!current)return {split:'right'};const r=current.rect,near=g=>Math.abs(g.rect.top-r.top);
 const right=groups.filter(g=>g.id!==currentId&&g.rect.left>=r.right-3).sort((a,b)=>a.rect.left-b.rect.left||near(a)-near(b))[0];if(right)return {group:right.id};
 if(groups.length<limit)return {split:'right'};
 const left=groups.filter(g=>g.id!==currentId&&g.rect.right<=r.left+3).sort((a,b)=>b.rect.right-a.rect.right||near(a)-near(b))[0];
 return {group:(left||current).id};
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
module.exports={targetColumn,resourceKey,pdfTargetPane,layoutRects};
