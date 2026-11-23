"use strict";
const http=require('node:http');const zlib=require('node:zlib');const {StringDecoder}=require('node:string_decoder');
const read=new Set(['notices','project_suggestions','session/reference','state','revision','history_refresh','accounts','hosts','runs','run','run/events','conversation/run','conversation/defaults','capabilities','chat','attention','session_tree','search','live','live/policy','files/roots','files/list','files/stat','files/search','files/diff','usage_history','settings/resources','settings/resource','settings/tool-analytics','project_catalog','commands','client','run/queue','run/decisions'].map(x=>'/api/'+x));
const write=new Set(['notices','project_suggestions','rescan','sync','models/discover','models/refresh','runs','run/control','run/recover','run/queue','attachments','conversation/folder','hosts/probe','live/refresh','live/policy','live/stop/preview','live/stop/commit','files/roots','files/move','files/reference','previews','project_catalog','projects','assign','chat_state','delete_chat','accounts/add','accounts/login','accounts/login/code','accounts/remove','accounts/refresh','accounts/config','accounts/default','accounts/verify','accounts/usage','accounts/delete','config'].map(x=>'/api/'+x));
function endpoint(value){const u=new URL(value);if(u.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(u.hostname)||u.username||u.password||u.pathname!=='/'||u.search||u.hash)throw Error('Library endpoint must be an HTTP loopback origin.');return u.origin;}
function route(value,method='GET'){
 if(typeof value!=='string'||!value.startsWith('/api/')||value.includes('#')||value.includes('\\')||value.length>32768)throw Error('Unsupported Library route');
 const pathname=value.split('?')[0];if(!(method==='GET'?read:write).has(pathname))throw Error('Unsupported Library route: '+pathname);return value;
}
function request(base,path,body){return new Promise((resolve,reject)=>{
 let origin,raw;try{origin=endpoint(base);route(path,body===undefined?'GET':'POST');raw=body===undefined?undefined:JSON.stringify(body);if(raw&&Buffer.byteLength(raw)>30*1024*1024)throw Error('Request too large');}catch(e){reject(e);return;}
 const req=http.request(origin+path,{method:raw===undefined?'GET':'POST',headers:raw===undefined?{}:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(raw)}},res=>{const chunks=[];let size=0;res.on('data',chunk=>{size+=chunk.length;if(size>64*1024*1024)res.destroy(Error('Library response exceeds 64 MB'));else chunks.push(chunk);});res.on('error',reject);res.on('end',()=>{try{const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(res.statusCode<200||res.statusCode>=300||data.ok===false||data.error){const e=Error(data.error||'HTTP '+res.statusCode);e.status=res.statusCode;e.data=data;throw e;}resolve(data);}catch(e){reject(e);}});});
 const routePath=path.split('?')[0],timeout=routePath==='/api/files/move'?3600000:routePath==='/api/models/discover'?35000:routePath==='/api/models/refresh'?750000:routePath==='/api/accounts/login/code'?40000:routePath==='/api/accounts/login'?25000:routePath==='/api/run/recover'?150000:20000;
 req.setTimeout(timeout,()=>req.destroy(Error(raw===undefined?'Library did not answer within 20 seconds.':'Library timed out. Retry keeps the same request ID.')));req.on('error',reject);req.end(raw);
});}
function eventParser(receive){const decoder=new StringDecoder('utf8');let buffer='';return chunk=>{buffer+=decoder.write(chunk);if(buffer.length>8*1024*1024)throw Error('Oversized event');let end;while((end=buffer.indexOf('\n\n'))>=0){const record=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=record.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');if(data)receive(JSON.parse(data));}};}
function subscribe(base,id,after,receive,onEnd){
 const path='/api/run/events?'+new URLSearchParams({id,after:String(after||0)});const parse=eventParser(receive);let closed=false;
 const req=http.get(endpoint(base)+path,res=>{if(res.statusCode!==200){res.resume();onEnd(Error('Event stream HTTP '+res.statusCode));return;}res.on('data',chunk=>{try{parse(chunk);}catch(e){req.destroy(e);}});res.on('end',()=>{if(!closed)onEnd();});res.on('error',e=>{if(!closed)onEnd(e);});});
 req.on('error',e=>{if(!closed)onEnd(e);});return()=>{closed=true;req.destroy();};
}
module.exports={endpoint,route,request,eventParser,subscribe};

// The chat list is large: revalidate with the last ETag and accept gzip.
// Resolves {notModified:true} on 304, otherwise {etag,value}.
function requestState(base,etag,since){return new Promise((resolve,reject)=>{
 let origin;try{origin=endpoint(base);}catch(e){reject(e);return;}
 const req=http.get(origin+'/api/state'+(since?'?since='+encodeURIComponent(since):''),{headers:{'Accept-Encoding':'gzip',...(etag?{'If-None-Match':etag}:{})}},res=>{
  if(res.statusCode===304){res.resume();resolve({notModified:true});return;}
  const chunks=[];let size=0;res.on('data',chunk=>{size+=chunk.length;if(size>64*1024*1024)res.destroy(Error('Library response exceeds 64 MB'));else chunks.push(chunk);});res.on('error',reject);
  res.on('end',()=>{const body=Buffer.concat(chunks);const parse=raw=>{try{const data=JSON.parse(raw.toString('utf8'));if(res.statusCode!==200||data.ok===false||data.error){const e=Error(data.error||'HTTP '+res.statusCode);e.status=res.statusCode;e.data=data;throw e;}resolve({etag:res.headers.etag||'',value:data});}catch(e){reject(e);}};
   if(res.headers['content-encoding']==='gzip')zlib.gunzip(body,{maxOutputLength:256*1024*1024},(e,raw)=>e?reject(e):parse(raw));else parse(body);});
 });
 req.setTimeout(20000,()=>req.destroy(Error('Library did not answer within 20 seconds.')));req.on('error',reject);
});}
module.exports.requestState=requestState;

function requestBytes(base,path){return new Promise((resolve,reject)=>{
 let origin;try{origin=endpoint(base);if(typeof path!=='string'||path.split('?')[0]!=='/api/files/content'||path.includes('#')||path.includes('\\'))throw Error('Invalid file content route');}catch(e){reject(e);return;}
 const req=http.get(origin+path,res=>{const chunks=[];let length=0;res.on('data',chunk=>{length+=chunk.length;if(length>64*1024*1024)res.destroy(Error('Remote file exceeds 64 MB'));else chunks.push(chunk);});res.on('error',reject);res.on('end',()=>{if(res.statusCode!==200){reject(Error('Remote file read failed: HTTP '+res.statusCode));return;}resolve(Buffer.concat(chunks));});});req.setTimeout(40000,()=>req.destroy(Error('Remote file read timed out')));req.on('error',reject);
});}
module.exports.requestBytes=requestBytes;

// Webviews cannot resolve relative backend image URLs. Only bounded image
// responses from the two existing file/attachment routes cross this bridge.
function imageResource(base,path){return new Promise((resolve,reject)=>{
 let origin;try{origin=endpoint(base);if(typeof path!=='string'||path.length>32768||path.includes('#')||path.includes('\\')||!['/api/files/content','/api/attachment'].includes(path.split('?')[0]))throw Error('Unsupported image route');}catch(e){reject(e);return;}
 const req=http.get(origin+path,res=>{const mime=String(res.headers['content-type']||'').split(';')[0].trim().toLowerCase();if(res.statusCode!==200||!/^image\/(png|jpeg|gif|webp|avif|svg\+xml|bmp|x-icon)$/.test(mime)){res.resume();reject(Error('The referenced image is unavailable'));return;}const chunks=[];let length=0;res.on('data',chunk=>{length+=chunk.length;if(length>8*1024*1024)res.destroy(Error('Image preview exceeds 8 MB; open the file to view it'));else chunks.push(chunk)});res.on('error',reject);res.on('end',()=>resolve('data:'+mime+';base64,'+Buffer.concat(chunks).toString('base64')));});req.setTimeout(20000,()=>req.destroy(Error('Image preview timed out')));req.on('error',reject);
});}
module.exports.imageResource=imageResource;
