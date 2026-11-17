'use strict';
const items=[['reference','reference'],['sessionId','session_id'],['transcriptPath','transcript_path'],['resumeCommand','resume_command']];
function register(vscode,context,api){
 for(const [suffix,field] of items)context.subscriptions.push(vscode.commands.registerCommand('library.copyChat.'+suffix,async(...args)=>{
  try{
   let key=args.find(arg=>arg&&typeof arg.libraryChatKey==='string')?.libraryChatKey;
   if(!key){
    const group=vscode.window.tabGroups.activeTabGroup,index=group?.tabs.indexOf(group.activeTab),identities=await vscode.commands.executeCommand('_library.chatTabIdentities');
    key=Array.isArray(identities)?identities.find(row=>row.viewColumn===group?.viewColumn&&row.editorIndex===index)?.key:undefined;
   }
   if(!/^(claude|codex)-[A-Za-z0-9_-]{1,200}$/.test(key||''))throw Error('This tab does not yet have an identified saved session.');
   const result=await api('/api/session/reference?key='+encodeURIComponent(key)),value=result[field];
   if(typeof value!=='string'||!value)throw Error('This session has no '+field.replaceAll('_',' ')+'.');
   await vscode.env.clipboard.writeText(value);vscode.window.setStatusBarMessage('Copied',1500);
  }catch(error){void vscode.window.showErrorMessage(error.message||String(error));}
 }));
}
module.exports={items,register};
