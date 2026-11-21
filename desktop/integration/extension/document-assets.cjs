'use strict';
const path=require('node:path').posix;
const TYPES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.avif':'image/avif','.svg':'image/svg+xml','.bmp':'image/bmp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf','.otf':'font/otf','.css':'text/css'};
// Asset bytes always use the backend's registered root walker, including for
// local files. Exact references deliberately cannot acquire an asset scope.
async function createAssetLoader({uri,api,bytes,maxAssetBytes=2*1024*1024,maxTotalBytes=8*1024*1024,maxAssets=64}){
 const empty={image:async()=>null,stylesheet:async()=>null,css:async value=>value.replace(/url\([^)]*\)/gi,'url("")').replace(/@import[^;]*;/gi,'')};
 if(!['file','library-file'].includes(uri.scheme))return empty;
 let roots;try{roots=(await api('/api/files/roots')).roots||[];}catch{return empty;}
 let root,entry;
 if(uri.scheme==='library-file'){root=roots.find(row=>(row.id||row.root_id)===uri.authority);if(root)entry=path.join(root.path,uri.path.replace(/^\//,''));}
 else {entry=uri.fsPath||uri.path;root=roots.filter(row=>['mac','local'].includes(row.host_id||'mac')&&(entry===row.path||entry.startsWith(row.path.replace(/\/$/,'')+'/'))).sort((a,b)=>b.path.length-a.path.length)[0];}
 if(!root)return empty;
 const base=root.path.replace(/\/$/,'')||'/',cache=new Map();let total=0,count=0,emitted=0;
 function reserve(length){if(emitted+length>Math.ceil(maxTotalBytes*1.5))return false;emitted+=length;return true;}
 function dataUrl(loaded){const value='data:'+loaded.type+';base64,'+loaded.data.toString('base64');return reserve(value.length)?value:null;}
 function resolve(value,from=entry){
  if(typeof value!=='string'||value.length>8192||/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value))return null;
  let decoded;try{decoded=decodeURIComponent(value.split(/[?#]/,1)[0]);}catch{return null;}
  if(!decoded||/[\\\\\0]/.test(decoded))return null;
  const target=decoded.startsWith('/')?(decoded===base||decoded.startsWith(base+'/')?path.normalize(decoded):path.resolve(base,'.'+decoded)):path.resolve(path.dirname(from),decoded);
  if(target===base||!(base==='/'||target.startsWith(base+'/')))return null;
  return target;
 }
 async function raw(target,kind){
  const type=TYPES[path.extname(target).toLowerCase()];
  if(!type||(kind==='image'&&!type.startsWith('image/'))||(kind==='css'&&type!=='text/css')||(kind==='asset'&&!/^(?:image|font)\//.test(type)))return null;
  const key=kind+':'+target;if(cache.has(key))return cache.get(key);
  const promise=(async()=>{if(++count>maxAssets)return null;try{
   const query=new URLSearchParams({root_id:root.id||root.root_id,path:path.relative(base,target)});
   const metadata=(await api('/api/files/stat?'+query)).resource;
   if(!metadata||!Number.isSafeInteger(metadata.size)||metadata.size<0||metadata.size>maxAssetBytes||total+metadata.size>maxTotalBytes)return null;
   total+=metadata.size;
   const data=Buffer.from(await bytes('/api/files/content?'+query));
   if(data.length!==metadata.size||data.length>maxAssetBytes)return null;
   return {data,type};
  }catch{return null;}})();cache.set(key,promise);return promise;
 }
 async function css(value,from=entry,ancestors=new Set()){
  if(value.length>maxAssetBytes)return '';
  // Unsupported/remote imports are removed; every remaining URL is embedded or empty.
  const imports=/@import\s+(?:url\(\s*(['"]?)(.*?)\1\s*\)|(['"])(.*?)\3)\s*([^;]*);/gi;
  let result='',offset=0,match;
  while((match=imports.exec(value))){result+=value.slice(offset,match.index);const target=resolve(match[2]||match[4],from);let replacement='';
   if(target&&!ancestors.has(target)&&ancestors.size<5){const loaded=await raw(target,'css');if(loaded){const chain=new Set(ancestors);chain.add(target);replacement=await css(loaded.data.toString('utf8'),target,chain);if(match[5].trim())replacement='@media '+match[5].trim()+'{'+replacement+'}';}}
   result+=replacement;offset=imports.lastIndex;
  }
  result+=value.slice(offset);value=result;
  const urls=/url\(\s*(?:(['"])(.*?)\1|([^)]*))\s*\)/gi;result='';offset=0;
  while((match=urls.exec(value))){result+=value.slice(offset,match.index);const original=(match[2]??match[3]).trim();let embedded='';
   if(/^data:(?:image\/(?:png|jpeg|gif|webp|avif|svg\+xml)|font\/(?:woff2?|ttf|otf));base64,[a-z0-9+/=]+$/i.test(original)&&original.length<=maxAssetBytes*1.4)embedded=original;
   else {const target=resolve(original,from),loaded=target&&await raw(target,'asset');if(loaded)embedded=dataUrl(loaded)||'';}
   result+='url("'+embedded+'")';offset=urls.lastIndex;
  }
  return result+value.slice(offset);
 }
 return {
  async image(value){const target=resolve(value),loaded=target&&await raw(target,'image');return loaded?dataUrl(loaded):null;},
  async stylesheet(value){const target=resolve(value),loaded=target&&await raw(target,'css');if(!loaded)return null;const rendered=await css(loaded.data.toString('utf8'),target,new Set([target]));return reserve(Math.ceil(rendered.length*4/3)+30)?rendered:null;},css
 };
}
module.exports={createAssetLoader};
