"use strict";
const {execFile}=require('node:child_process');
const helper=`import json,os,sys
request=json.load(sys.stdin)
folder=os.path.realpath(os.path.expanduser(request.get('path') or '~'))
entries=[]
with os.scandir(folder) as scan:
 for entry in scan:
  if len(entries)>=10000: break
  try:
   directory=entry.is_dir()
   if directory or entry.is_file(): entries.append({'name':entry.name,'directory':directory})
  except OSError: pass
print(json.dumps({'path':folder,'entries':entries,'truncated':len(entries)>=10000}))`;
function aliasFor(host){const alias=host.read_alias;if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,160}$/.test(alias||'')||(host.login_aliases||[]).includes(alias)||alias.startsWith('login.')||(host.kind==='slurm'&&alias===host.execution_alias))throw Error('Configure a data-transfer SSH alias for this host.');return alias;}
function quote(value){return "'"+value.replaceAll("'","'\\''")+"'";}
function list(host,path,execute=execFile){const alias=aliasFor(host);return new Promise((resolve,reject)=>{const child=execute('ssh',['-o','BatchMode=yes','-o','ConnectTimeout=8',alias,'python3 -c '+quote(helper)],{timeout:20000,maxBuffer:2000000},(error,stdout,stderr)=>{if(error)return reject(Error(String(stderr||error.message).trim().slice(0,600)));try{resolve(JSON.parse(stdout))}catch{reject(Error('Remote folder response was invalid'))}});child.stdin.end(JSON.stringify({path}));});}
async function browse(vscode,host){let folder='~';for(;;){let result;try{result=await vscode.window.withProgress({location:vscode.ProgressLocation.Notification,title:'Connecting to '+host.label,cancellable:false},()=>list(host,folder));}catch(error){const choice=await vscode.window.showWarningMessage(error.message,'Sign in via SSH','Retry','Cancel');if(choice==='Sign in via SSH'){const terminal=vscode.window.createTerminal({name:'Connect · '+host.label});terminal.show();terminal.sendText('ssh '+quote(aliasFor(host)));return;}if(choice==='Retry')continue;return;}
 const entries=result.entries.sort((a,b)=>Number(b.directory)-Number(a.directory)||a.name.localeCompare(b.name));
 const chosen=await vscode.window.showQuickPick([{label:'$(check) Open this folder',action:'select'},{label:'$(arrow-up) Parent folder',action:'up'},{label:'$(home) Home',action:'home'},{label:'$(root-folder) File system',action:'root'},...entries.map(entry=>({label:(entry.directory?'$(folder) ':'$(file) ')+entry.name,description:entry.directory?'Folder':'File',entry}))],{title:host.label+' · '+result.path,placeHolder:'Find a folder or file',ignoreFocusOut:false});
 if(!chosen)return;if(chosen.action==='select')return {folder:result.path};if(chosen.action==='home'){folder='~';continue;}if(chosen.action==='root'){folder='/';continue;}if(chosen.action==='up'){folder=require('node:path').posix.dirname(result.path);continue;}
 const selected=require('node:path').posix.join(result.path,chosen.entry.name);if(chosen.entry.directory){folder=selected;continue;}return {folder:result.path,file:selected};
}}
module.exports={aliasFor,list,browse};
