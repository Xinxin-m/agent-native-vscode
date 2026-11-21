export function resourceState(resource:any,storage:Record<string,string>){
 if(resource?.kind==='files'){try{const chosen=JSON.parse(storage['library.files.root']||'null');if(chosen)return {...resource,root_id:chosen.root_id,title:chosen.title};}catch{}return resource;}
 if(resource?.kind!=='chat')return resource;
 for(const [key,value] of Object.entries(storage)){
  if(!key.startsWith('library.workbench.conversation.'))continue;
  try{const record=JSON.parse(value)?.record;
   const matches=resource.draftId?record?.id==='draft:'+resource.draftId:record?.nativeKey===resource.key||record?.id==='chat:'+resource.key;
   if(matches&&['claude','codex'].includes(record?.provider))return {...resource,meta:{...resource.meta,source:record.provider,nativeKey:record.nativeKey}};
  }catch{}
 }
 return resource;
}
