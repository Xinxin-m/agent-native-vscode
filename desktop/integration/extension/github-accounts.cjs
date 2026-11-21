"use strict";
async function list(vscode,connect=false){
 if(connect)await vscode.authentication.getSession('github',['read:user','user:email'],{createIfNone:true});
 const accounts=await vscode.authentication.getAccounts('github');
 return {accounts:accounts.map(account=>({id:account.id,label:account.label}))};
}
function watch(vscode,refresh){return vscode.authentication.onDidChangeSessions(event=>{if(event.provider.id==='github')refresh();});}
module.exports={list,watch};
