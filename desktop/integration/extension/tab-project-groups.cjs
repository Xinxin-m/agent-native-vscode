'use strict';
function projectGrouping(vscode, state, catalog, roots, includeFiles) {
 const projects=(catalog.projects||[]).map(project=>({id:project.id,name:project.display_name||project.name,prefixes:[]}));
 for(const project of catalog.projects||[]){
  const entry=projects.find(item=>item.id===project.id);
  for(const mapping of project.paths||project.workspace_mappings||[]){
   if(['mac','local'].includes(mapping.host)) entry.prefixes.push(vscode.Uri.file(mapping.path).toString());
   else for(const root of roots||[]){
    if(root.host_id!==mapping.host)continue;
    const base=root.path.replace(/\/$/,'');
    if(base.startsWith(mapping.path.replace(/\/$/,'')+'/'))entry.prefixes.push(vscode.Uri.from({scheme:'library-file',authority:root.id,path:'/'}).toString());
    else if(mapping.path===base||mapping.path.startsWith(base+'/'))entry.prefixes.push(vscode.Uri.from({scheme:'library-file',authority:root.id,path:'/'+mapping.path.slice(base.length).replace(/^\//,'')}).toString());
   }
  }
 }
 const assignments={...(state.projects?.assignments||{})},providers={};
 for(const chat of state.chats||[]){
  providers[chat.key]=String(chat.source||chat.provider||'').toLowerCase();
  if(assignments[chat.key]||!chat.cwd)continue;
  let longest=-1;
  for(const project of catalog.projects||[])for(const mapping of project.paths||project.workspace_mappings||[]){const base=String(mapping.path||'').replace(/\/$/,'');const host=chat.host_id||chat.node||'mac';if(base&&(host===mapping.host||(['mac','local'].includes(host)&&['mac','local'].includes(mapping.host)))&&(chat.cwd===base||chat.cwd.startsWith(base+'/'))&&base.length>longest){assignments[chat.key]=project.id;longest=base.length;}}
 }
 return {assignments,projects,includeFiles,providers};
}
function register(vscode, context, api){
 const add=(name,callback)=>context.subscriptions.push(vscode.commands.registerCommand(name,callback));
 for(const action of ['createTabGroup','addTabToGroup','removeTabFromGroup']){
  add('library.'+action,(...args)=>vscode.commands.executeCommand('_library.'+action,args.find(arg=>Number.isInteger(arg?.groupId)&&Number.isInteger(arg?.editorIndex))));
 }
 async function auto(includeFiles){
  const [state,catalog,rootData]=await Promise.all([api('/api/state'),api('/api/project_catalog'),includeFiles?api('/api/files/roots').catch(()=>({roots:[]})):Promise.resolve({roots:[]})]);
  return vscode.commands.executeCommand('_library.groupTabsByProject',projectGrouping(vscode,state,catalog,rootData.roots,includeFiles));
 }
 add('library.arrangeAutomatically',()=>auto(true));
 add('library.groupChatsByProject',()=>auto(false));
 add('library.groupChatsAndFilesByProject',()=>auto(true));
}
module.exports={projectGrouping,register};
