const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const {connect}=require('./webviews.cjs');
(async()=>{const output=process.argv[2];if(!output||fs.existsSync(output))throw Error('Supply a new private evidence path');const c=await connect();try{
 const workbench=await c.page.evaluate(()=>({
  title:document.title,
  groups:[...document.querySelectorAll('.editor-group-container')].map(e=>({
   id:e.getAttribute('data-group-id'),
   tabs:[...e.querySelectorAll('[role=tab]')].map(t=>({name:t.getAttribute('aria-label'),dirty:t.classList.contains('dirty')}))
  }))
 }));
 const result={at:new Date().toISOString(),workbench,views:[]};
 for(const view of await c.views()){const value=await view.evaluate(`(()=>{const b=window.__LIBRARY_BOOT__;if(!b)return null;const storage={};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k?.startsWith('library.'))storage[k]=localStorage.getItem(k)}return {kind:b.kind,resource:b.resource,build:b.build,storage}})()`);if(value){value.draftHashes={};for(const [key,raw] of Object.entries(value.storage)){try{const r=JSON.parse(raw).record;if(r?.text?.trim())value.draftHashes[key]=crypto.createHash('sha256').update(r.text).digest('hex')}catch{}}result.views.push(value)}}
 result.processes=execFileSync('ps',['-axo','pid,ppid,lstart,comm'],{encoding:'utf8'}).split('\n').filter(l=>/Library Code|\/codex$|\/claude$/.test(l));
 const response=await fetch('http://127.0.0.1:8744/api/runs');const runs=await response.json();result.activeRuns=(runs.runs||[]).filter(r=>!['completed','failed','error','interrupted','cancelled','canceled','stopped'].includes(r.status)).map(r=>({id:r.id,status:r.status,process_pid:r.process_pid,process_identity:r.process_identity}));
 fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(result,null,2),{flag:'wx',mode:0o600});console.log(JSON.stringify({output,views:result.views.length,nonemptyDrafts:result.views.reduce((n,v)=>n+Object.keys(v.draftHashes).length,0),activeRuns:result.activeRuns.length}));
 }finally{await c.browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
