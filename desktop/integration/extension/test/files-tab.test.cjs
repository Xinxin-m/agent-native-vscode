'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const nodeFs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {create,freeName}=require('../files-tab.cjs');

class Uri{constructor(p,scheme='file',authority=''){this.path=path.posix.normalize(p);this.scheme=scheme;this.authority=authority;this.fsPath=this.path;}
 toString(){return this.scheme+'://'+this.authority+this.path;}static file(p){return new Uri(p);}static joinPath(u,...p){return new Uri(path.posix.join(u.path,...p),u.scheme,u.authority);}}
function fakeVscode(folders){
 const renames=[];
 return {renames,Uri,FileType:{File:1,Directory:2},
  WorkspaceEdit:class{constructor(){this.ops=[];}renameFile(a,b,o){this.ops.push([a,b,o]);}},
  workspace:{workspaceFolders:folders.map((p,index)=>({uri:Uri.file(p),name:path.basename(p),index})),getConfiguration:()=>({get:()=>({'**/.DS_Store':true})}),
   applyEdit:async edit=>{for(const [a,b,o] of edit.ops){if(nodeFs.existsSync(b.fsPath)){if(!o?.overwrite)return false;nodeFs.rmSync(b.fsPath,{recursive:true});}nodeFs.mkdirSync(path.dirname(b.fsPath),{recursive:true});nodeFs.renameSync(a.fsPath,b.fsPath);renames.push([a.fsPath,b.fsPath]);}return true;},
   fs:{stat:async u=>{const s=nodeFs.statSync(u.fsPath);return {type:s.isDirectory()?2:1};},readDirectory:async u=>nodeFs.readdirSync(u.fsPath,{withFileTypes:true}).map(d=>[d.name,d.isDirectory()?2:1]),
    copy:async(a,b,o)=>{if(nodeFs.existsSync(b.fsPath)&&!o?.overwrite)throw Error('exists');nodeFs.mkdirSync(path.dirname(b.fsPath),{recursive:true});nodeFs.cpSync(a.fsPath,b.fsPath,{recursive:true});},
    delete:async u=>nodeFs.rmSync(u.fsPath,{recursive:true})}}};
}
function setup(){
 const base=nodeFs.mkdtempSync(path.join(os.tmpdir(),'files-tab-')),a=path.join(base,'wsA'),b=path.join(base,'wsB');
 nodeFs.mkdirSync(path.join(a,'docs','inner'),{recursive:true});nodeFs.mkdirSync(b);
 nodeFs.writeFileSync(path.join(a,'note.md'),'a');nodeFs.writeFileSync(path.join(b,'note.md'),'b');nodeFs.writeFileSync(path.join(a,'docs','inner','deep.txt'),'d');nodeFs.writeFileSync(path.join(a,'.DS_Store'),'');
 const vscode=fakeVscode([a,b]),tab=create(vscode,{api:async()=>({roots:[]}),backups:Uri.file(path.join(base,'backups'))});
 return {a,b,vscode,tab,ra:Uri.file(a).toString(),rb:Uri.file(b).toString()};
}
const move=(t,body)=>t.tab.request('/api/files/move',body);

test('Keep both follows VS Code naming in every client',()=>{
 assert.equal(freeName(new Set(['note.md']),'note.md',false),'note copy.md');
 assert.equal(freeName(new Set(['note.md','note copy.md']),'note.md',false),'note copy 2.md');
 assert.equal(freeName(new Set(['docs']),'docs',true),'docs copy');
 assert.equal(freeName(new Set(['.env']),'.env',false),'.env copy');
});
test('lists workspace folders and folder contents like the Library server',async()=>{
 const t=setup();const roots=(await t.tab.request('/api/files/roots')).roots;
 assert.deepEqual(roots.map(r=>[r.label,r.host_id,r.access]),[['wsA','mac','write'],['wsB','mac','write']]);
 assert.deepEqual((await t.tab.request('/api/files/list?root_id='+encodeURIComponent(t.ra)+'&path=')).entries.map(e=>[e.path,e.type]),[['docs','directory'],['note.md','file']]);
});
test('moves across workspaces through a workspace edit, asks on a name clash, and undoes',async()=>{
 const t=setup();
 await assert.rejects(move(t,{items:[{root_id:t.ra,path:'note.md'}],dest_root_id:t.rb}),e=>e.data.code==='destination_exists'&&e.data.names[0]==='note.md');
 const kept=await move(t,{items:[{root_id:t.ra,path:'note.md'}],dest_root_id:t.rb,conflict:'keep_both'});
 assert.equal(kept.moved[0].dest_path,'note copy.md');assert.equal(nodeFs.readFileSync(path.join(t.b,'note copy.md'),'utf8'),'a');assert.equal(t.vscode.renames.length,1);
 await t.tab.request('/api/files/move/undo',{token:kept.token});assert.equal(nodeFs.readFileSync(path.join(t.a,'note.md'),'utf8'),'a');
 const replaced=await move(t,{items:[{root_id:t.ra,path:'note.md'}],dest_root_id:t.rb,conflict:'replace'});
 assert.equal(nodeFs.readFileSync(path.join(t.b,'note.md'),'utf8'),'a');
 await t.tab.request('/api/files/move/undo',{token:replaced.token});
 assert.equal(nodeFs.readFileSync(path.join(t.b,'note.md'),'utf8'),'b');assert.equal(nodeFs.readFileSync(path.join(t.a,'note.md'),'utf8'),'a');
});
test('copies with Option, refuses moving a folder into itself, and refuses Mac to cluster',async()=>{
 const t=setup();
 const copied=await move(t,{items:[{root_id:t.ra,path:'docs'}],dest_root_id:t.ra,copy:true});
 assert.equal(copied.moved[0].dest_path,'docs copy');assert.ok(nodeFs.existsSync(path.join(t.a,'docs','inner','deep.txt')));
 await assert.rejects(move(t,{items:[{root_id:t.ra,path:'docs'}],dest_root_id:t.ra,dest_dir:'docs/inner'}),e=>e.data.code==='into_itself');
 await assert.rejects(move(t,{items:[{root_id:t.ra,path:'../wsB/note.md'}],dest_root_id:t.rb}),e=>e.data.code==='outside_root');
 t.vscode.workspace.workspaceFolders.push({uri:new Uri('/','library-file','r1'),name:'proj · Cluster',index:2});
 await assert.rejects(move(t,{items:[{root_id:t.ra,path:'note.md'}],dest_root_id:'r1'}),e=>e.data.code==='move_confirmation_required');
 assert.ok(nodeFs.existsSync(path.join(t.a,'note.md')));
});

test('creates a new sibling file or child folder and never overwrites an existing item',async()=>{
 const t=setup();try{
  const request=(name,type)=>t.tab.request('/api/files/create',{root_id:t.ra,path:name,type});
  assert.deepEqual(await request('docs/new.md','file'),{ok:true,root_id:t.ra,path:'docs/new.md',type:'file'});
  await request('docs/new-folder','directory');assert.ok(nodeFs.statSync(path.join(t.a,'docs/new-folder')).isDirectory());
  nodeFs.writeFileSync(path.join(t.a,'docs/new.md'),'keep');
  await assert.rejects(request('docs/new.md','file'),e=>e.data.code==='destination_exists');
  await assert.rejects(request('docs/new-folder','directory'),e=>e.data.code==='destination_exists');
  assert.equal(nodeFs.readFileSync(path.join(t.a,'docs/new.md'),'utf8'),'keep');
 }finally{nodeFs.rmSync(path.dirname(t.a),{recursive:true,force:true});}
});
test('creation refuses traversal, symlink escapes, remote writes and invalid types',async()=>{
 const t=setup();try{
  nodeFs.symlinkSync(t.b,path.join(t.a,'escape'));
  for(const name of ['../outside','/outside','escape/no.txt',''])await assert.rejects(t.tab.request('/api/files/create',{root_id:t.ra,path:name,type:'file'}));
  await assert.rejects(t.tab.request('/api/files/create',{root_id:t.ra,path:'no.txt',type:'other'}));
  t.vscode.workspace.workspaceFolders.push({uri:new Uri('/','library-file','r1'),name:'Cluster',index:2});
  await assert.rejects(t.tab.request('/api/files/create',{root_id:'r1',path:'no.txt',type:'file'}),e=>e.data.code==='read_only');
 }finally{nodeFs.rmSync(path.dirname(t.a),{recursive:true,force:true});}
});
