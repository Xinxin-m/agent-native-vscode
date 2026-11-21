'use strict';
const {query:scopedQuery}=require('./remote-files.cjs');
const MAX_FILES=500,MAX_MATCHES=200;
const cancelled=token=>token?.isCancellationRequested===true;
async function untilCancelled(work,token){
 if(cancelled(token))return null;
 /** @type {import('vscode').Disposable|undefined} */ let subscription;
 const stop=new Promise(resolve=>{subscription=token?.onCancellationRequested(()=>resolve(null));});
 try{return await Promise.race([Promise.resolve().then(()=>cancelled(token)?null:work()),stop]);}
 finally{if(subscription)subscription.dispose();}
}
async function delay(ms,token){
 if(ms<=0)return !cancelled(token);
 let timer;try{return (await untilCancelled(()=>new Promise(resolve=>{timer=setTimeout(()=>resolve(true),ms);}),token))===true;}
 finally{clearTimeout(timer);}
}
function bounded(value,fallback,maximum){return Number.isFinite(value)?Math.max(0,Math.min(Math.floor(value),maximum)):fallback;}
function folderScope(folder){
 const value=new URLSearchParams(scopedQuery(folder));
 return {root:value.get('root_id'),path:value.get('path')||'',folder};
}
function filters(values,scope,relative=false){
 if(!Array.isArray(values))return [];
 if(values.length>64)throw Error('Remote search supports at most 64 include/exclude patterns.');
 return values.map(value=>{
  if(typeof value==='string'){if(value.length>500)throw Error('Remote search pattern is too long.');return value;}
  if(!relative||!value||typeof value.pattern!=='string'||!value.baseUri)throw Error('Unsupported remote search glob.');
  if(value.baseUri.scheme!==scope.folder.scheme||value.baseUri.authority!==scope.folder.authority)return null;
  const base=folderScope(value.baseUri).path;
  if(value.pattern.length>500)throw Error('Remote search pattern is too long.');
  return {base,pattern:value.pattern};
 }).filter(value=>value!==null);
}
function resultUri(vscode,scope,path){
 if(typeof path!=='string'||!path||path.startsWith('/')||path.length>8192)throw Error('Remote search returned an invalid scoped path.');
 const uri=vscode.Uri.from({scheme:'library-file',authority:scope.root,path:'/'+path});
 scopedQuery(uri);
 if(scope.path&&!(path.startsWith(scope.path.replace(/\/$/,'')+'/')))throw Error('Remote search result escaped its selected folder scope.');
 return uri;
}
function checkReply(reply,scope){
 if(reply?.search_version!==1)throw Error('The Library server needs native remote-search support before this workspace can be searched.');
 if(reply.root_id!==scope.root)throw Error('Remote search returned a different workspace root.');
}
/**
 * @param {typeof import('vscode')} vscode
 * @param {(route:string)=>Promise<any>} api
 */
function createProviders(vscode,api,{debounceMs=120}={}){
 const request=async(scope,mode,pattern,options,token)=>{
  const encoded=JSON.stringify(options);if(encoded.length>12000)throw Error('Remote search filters exceed the request limit.');
  return untilCancelled(()=>api('/api/files/search?'+new URLSearchParams({root_id:scope.root,path:scope.path,mode,q:pattern,options:encoded})),token);
 };
 /** @type {import('vscode').FileSearchProvider2} */
 const files={async provideFileSearchResults(pattern,options,token){
  if(typeof pattern!=='string'||pattern.length>200)throw Error('Remote filename patterns are limited to 200 characters.');
  const scopes=(options.folderOptions||[]).map(folder=>({...folder,scope:folderScope(folder.folder)}));
  const limit=bounded(options.maxResults,MAX_FILES,MAX_FILES),results=[];
  if(!limit||!await delay(debounceMs,token))return results;
  for(const folder of scopes){
   if(cancelled(token)||results.length>=limit)break;
   const scope=folder.scope,reply=await request(scope,'files',pattern,{includes:filters(folder.includes,scope),excludes:filters(folder.excludes,scope,true),maxResults:limit-results.length},token);
   if(!reply||cancelled(token))break;checkReply(reply,scope);
   if(!Array.isArray(reply.entries))throw Error('Remote search returned invalid file results.');
   for(const row of reply.entries){if(results.length>=limit||cancelled(token))break;results.push(resultUri(vscode,scope,row.path));}
  }
  return results;
 }};
 /** @type {import('vscode').TextSearchProvider2} */
 const text={async provideTextSearchResults(query,options,progress,token){
  if(query.isRegExp||query.isMultiline||query.pattern?.includes('\n')||query.pattern?.includes('\r'))throw Error('Remote text search supports single-line literal patterns; regex and multiline search are unavailable.');
  if(typeof query.pattern!=='string'||query.pattern.length>200)throw Error('Remote text patterns are limited to 200 characters.');
  const scopes=(options.folderOptions||[]).map(folder=>({...folder,scope:folderScope(folder.folder)}));
  const limit=bounded(options.maxResults,MAX_MATCHES,MAX_MATCHES);
  let count=0,limitHit=false,ignoreFiles=false;
  if(!query.pattern||!limit||!await delay(debounceMs,token))return {limitHit:false};
  for(const folder of scopes){
   if(cancelled(token))break;
   if(count>=limit){limitHit=true;break;}
   if(folder.encoding&&!['utf8','utf8bom','utf-8'].includes(folder.encoding.toLowerCase()))throw Error('Remote content search supports UTF-8 files only.');
   ignoreFiles ||= !!(folder.useIgnoreFiles?.local||folder.useIgnoreFiles?.parent||folder.useIgnoreFiles?.global);
   const scope=folder.scope,reply=await request(scope,'text',query.pattern,{includes:filters(folder.includes,scope),excludes:filters(folder.excludes,scope,true),maxResults:limit-count,maxFileSize:bounded(options.maxFileSize,1024*1024,1024*1024),previewChars:bounded(options.previewOptions?.charsPerLine,200,2000),isCaseSensitive:query.isCaseSensitive===true,isWordMatch:query.isWordMatch===true},token);
   if(!reply||cancelled(token))break;checkReply(reply,scope);limitHit ||= reply.truncated===true;
   if(!Array.isArray(reply.matches))throw Error('Remote search returned invalid text results.');
   for(const row of reply.matches){
    if(cancelled(token))break;if(count>=limit){limitHit=true;break;}
    const uri=resultUri(vscode,scope,row.path);
    if(typeof row.preview!=='string'||row.preview.length>4000||![row.line,row.start,row.end,row.preview_start,row.preview_end].every(v=>Number.isSafeInteger(v)&&v>=0)||row.end<row.start||row.preview_end<row.preview_start||row.preview_end>row.preview.length)throw Error('Remote search returned invalid match ranges.');
    progress.report(new vscode.TextSearchMatch2(uri,[{sourceRange:new vscode.Range(row.line,row.start,row.line,row.end),previewRange:new vscode.Range(0,row.preview_start,0,row.preview_end)}],row.preview));count++;
   }
  }
  const message=[];
  if(limitHit)message.push({text:'Remote search reached a result, time, depth or byte limit. Narrow the folder or include filter to search further.',type:vscode.TextSearchCompleteMessageType.Information});
  if(ignoreFiles)message.push({text:'Remote search uses include/exclude filters. Ignore files are not applied; symlinks are never followed.',type:vscode.TextSearchCompleteMessageType.Information});
  return {limitHit,...message.length?{message}:{}};
 }};
 return {files,text};
}
/**
 * @param {typeof import('vscode')} vscode
 * @param {import('vscode').ExtensionContext} context
 * @param {(route:string)=>Promise<any>} api
 */
function register(vscode,context,api){
 if(typeof vscode.workspace.registerFileSearchProvider2!=='function'||typeof vscode.workspace.registerTextSearchProvider2!=='function')throw Error('This Library Code build does not expose the required remote search APIs.');
 const providers=createProviders(vscode,api);
 context.subscriptions.push(vscode.workspace.registerFileSearchProvider2('library-file',providers.files),vscode.workspace.registerTextSearchProvider2('library-file',providers.text));
}
module.exports={createProviders,register};
