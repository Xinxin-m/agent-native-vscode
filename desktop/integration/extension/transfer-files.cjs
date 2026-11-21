'use strict';
const path=require('node:path');
function locate(vscode,roots,value){
 const uri=typeof value==='string'?vscode.Uri.parse(value):value;
 const candidates=roots.filter(root=>uri.scheme==='file'?root.host_id==='mac'&&(uri.fsPath===root.path||uri.fsPath.startsWith(root.path.replace(/\/$/,'')+'/')):uri.scheme==='library-file'&&uri.authority===root.id).sort((a,b)=>b.path.length-a.path.length);
 const root=candidates[0];if(!root||root.scope_known===false)throw Error('The selected file is outside an identified open workspace.');
 const relative=uri.scheme==='file'?path.posix.relative(root.path,uri.fsPath):uri.path.replace(/^\//,'');
 if(relative.startsWith('../')||path.posix.isAbsolute(relative)||relative.split('/').some(p=>p==='..'||p==='.'))throw Error('The path is outside its workspace.');
 return {root_id:root.id,path:relative,uri};
}
async function transferFiles(vscode,filesTab,request,refresh){
 if(!request||!Array.isArray(request.items)||!request.items.length||request.items.length>500)throw Error('Choose files or folders to transfer.');
 const roots=(await filesTab.request('/api/files/roots')).roots||[],items=request.items.map(value=>locate(vscode,roots,value)),dest=locate(vscode,roots,request.target);
 if(items.some(item=>!item.path))throw Error('A whole workspace cannot be transferred.');
 const choices=[{label:'Move',description:'Copy, verify, then put originals in recoverable Trash',id:'move'},{label:'Copy',description:'Keep originals in place',id:'copy'}];
 const choice=await vscode.window.showQuickPick(choices,{title:'Transfer between this Mac and a remote host',placeHolder:(items.length===1?path.posix.basename(items[0].path):items.length+' items')+' → '+((roots.find(root=>root.id===dest.root_id)?.label)||dest.root_id)+(dest.path?'/'+dest.path:''),ignoreFocusOut:false,activeItem:request.copy?choices[1]:choices[0]});if(!choice)return;
 const copy=choice.id==='copy';
 if(!copy&&(vscode.workspace.textDocuments||[]).some(doc=>doc.isDirty&&items.some(item=>doc.uri.toString()===item.uri.toString()||doc.uri.toString().startsWith(item.uri.toString().replace(/\/$/,'')+'/'))))throw Error('Save edited files before moving them between machines. Copy keeps unsaved editors in place.');
 const body={items:items.map(({root_id,path})=>({root_id,path})),dest_root_id:dest.root_id,dest_dir:dest.path,copy,confirmed_move:!copy,conflict:'ask'};
 const run=async()=>{try{return await filesTab.request('/api/files/move',body);}catch(error){if(error.data?.code!=='destination_exists'&&error.code!=='destination_exists'){const locations=[...(error.data?.completed||[]),...(error.data?.recovery||[])];if(locations.length)error.message+='\nSource data was preserved. Retained locations:\n'+locations.map(row=>(row.host_id||'mac')+': '+row.path).join('\n');throw error;}const answer=await vscode.window.showWarningMessage('Files already exist in the destination. Existing files will be preserved.','Keep both');if(answer!=='Keep both')return;return filesTab.request('/api/files/move',{...body,conflict:'keep_both'});}};
 const result=vscode.window.withProgress?await vscode.window.withProgress({location:vscode.ProgressLocation.Notification,title:copy?'Copying files between this Mac and a remote host':'Moving files between this Mac and a remote host',cancellable:false},run):await run();
 if(!result)return;await refresh?.();
 const locations=(result.recovery||[]).map(row=>`${row.host_id}: ${row.path}`).join('\n');
 const staged=result.recovery?.some(row=>row.kind==='staging');const message=`${result.copy?'Copied':'Moved'} ${result.moved.length} item${result.moved.length===1?'':'s'}.`+(locations?(staged?' Originals remain at the reported recovery locations; that source space is still in use.':' Originals are in recoverable Trash; source space is freed when Trash is emptied.'):'')+(result.warnings?.length?' Check the recovery locations.':'');
 const action=await vscode.window.showInformationMessage(message,...(locations?['Copy recovery paths']:[]));if(action==='Copy recovery paths')await vscode.env.clipboard.writeText(locations);
 return result;
}
module.exports={locate,transferFiles};
