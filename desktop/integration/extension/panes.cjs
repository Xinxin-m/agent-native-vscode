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
module.exports={targetColumn,resourceKey};
