import type {Resource} from '../state/types';
export type ViewerKind='markdown'|'html'|'image'|'pdf'|'source'|'unsupported';
export interface FileRecord {resource_id?:string;file_id?:string;encoding?:string;newline?:string;host_id:string;path:string;name:string;mime:string;size:number;version:string;editable:boolean;document_editable?:boolean;root_id:string;relative_path?:string}
export function viewerKind(name:string,mime=''):ViewerKind {
 const ext=name.split('.').pop()?.toLowerCase()||'';
 if(ext==='pdf'||mime==='application/pdf')return 'pdf';
 if(['md','markdown','mdown'].includes(ext))return 'markdown';
 if(['html','htm'].includes(ext)||mime==='text/html')return 'html';
 if(mime.startsWith('image/')||['png','jpg','jpeg','webp','gif','svg','avif','bmp','ico'].includes(ext))return 'image';
 if(mime.startsWith('text/')||['js','jsx','ts','tsx','json','css','scss','less','txt','tex','bib','py','rs','go','c','h','cpp','sh','zsh','yaml','yml','toml','csv','tsv','sql','xml','ipynb','r','jl','ini','log'].includes(ext))return 'source';
 return 'unsupported';
}
export function coveringRoot<T extends {path:string;host_id?:string;id?:string;root_id?:string}>(roots:T[],host:string,path:string):T|undefined{
 return roots.filter(r=>(r.host_id||'mac')===host&&(path===r.path||path.startsWith(r.path.replace(/\/$/,'')+'/'))).sort((a,b)=>b.path.length-a.path.length)[0];
}
export const fileIdentity=(host:string,path:string)=>JSON.stringify([host,path]);
export function decodeText(buffer:ArrayBuffer):string {
 const bytes=new Uint8Array(buffer);
 if(bytes.includes(0))throw new Error('This file uses a binary or unsupported text encoding. Download it to open in another app.');
 try{return new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{throw new Error('This file is not UTF-8. Open it in your editor to preserve its encoding.');}
}
export function isSafePreviewUrl(url:string,origin:string):boolean{try{const u=new URL(url);return ['http:','https:'].includes(u.protocol)&&u.origin!==origin&&!u.username&&!u.password;}catch{return false;}}
export function previewLocation(path:string){const slash=path.lastIndexOf('/');return {artifact_directory:path.slice(0,slash)||'/',entry_path:path.slice(slash+1)};}
export function languageFor(name:string){const ext=name.split('.').pop()?.toLowerCase();return ({js:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',py:'python',md:'markdown',tex:'latex',sh:'shell',yml:'yaml',rs:'rust',h:'cpp',txt:'plaintext'} as Record<string,string>)[ext]||ext||'plaintext';}
export function contentUrl(file:FileRecord,download=false){const q=new URLSearchParams(file.file_id&&!file.root_id?{file_id:file.file_id}:{root_id:file.root_id,path:file.relative_path||'.'});if(download)q.set('download','1');return '/api/files/content?'+q;}

export function previewAuthorized(registeredRoot:boolean,approvedScope:string,host:string,path:string){return registeredRoot||approvedScope===JSON.stringify([host,previewLocation(path).artifact_directory]);}

export const fileViewIdentity=(r:Resource)=>JSON.stringify([r.host_id||'mac',r.path||r.file_id||'',r.meta?.viewId||'default']);
