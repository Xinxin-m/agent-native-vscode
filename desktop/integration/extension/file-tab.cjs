'use strict';
const path=require('node:path').posix;

// Tabs include restored editors that have not loaded their document or webview yet.
async function revealFileTab(vscode,uri,api,options={}){
 const groups=vscode.window.tabGroups;
 const tabs=[groups.activeTabGroup,...groups.all.filter(group=>group!==groups.activeTabGroup)]
  .flatMap(group=>(group.tabs||[]).filter(tab=>tab.input?.uri).map(tab=>({group,tab,uri:tab.input.uri})));
 let match=tabs.find(candidate=>candidate.uri.toString()===uri.toString());
 if(!match&&uri.scheme!=='file'){
  const candidates=tabs.filter(candidate=>['library-reference','library-file'].includes(candidate.uri.scheme));
  if(!candidates.length)return false;
  const needsRoot=[uri,...candidates.map(item=>item.uri)].some(item=>item.scheme==='library-file');
  const roots=needsRoot?(await api('/api/files/roots')).roots||[]:[];
  function key(u){
   if(u.scheme==='library-reference')return JSON.stringify([u.authority,new URLSearchParams(u.query).get('canonical_path')||u.path]);
   const root=roots.find(item=>(item.id||item.root_id)===u.authority);
   return root&&JSON.stringify([root.host_id,path.join(root.path,u.path)]);
  }
  const target=key(uri);
  match=target&&candidates.find(candidate=>key(candidate.uri)===target);
 }
 if(!match)return false;
 // Preserve a custom editor or source view, including a tab restored lazily by Code.
 const viewType=match.tab.input.viewType||'default';
 await vscode.commands.executeCommand('vscode.openWith',match.uri,viewType,{...options,viewColumn:match.group.viewColumn,preserveFocus:false});
 return true;
}
function serializeFileOpens(){
 let queue=Promise.resolve();
 return fn=>{
  const result=queue.then(fn);
  queue=result.catch(()=>{});
  return result;
 };
}
module.exports={revealFileTab,serializeFileOpens};
