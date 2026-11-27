const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {localReviewStore,resolveReviewEditor,flushEditors}=require('../document-review.cjs');
async function fixture(){
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'lc-review-'));
 const value={version:'one',text:'First paragraph'};
 const uri={scheme:'library-file',authority:'photos',path:'/a.md',toString:()=>'library-file://photos/a.md'};
 const meta=()=>({document_editable:true,version:value.version,path:'/root/a.md',name:'a.md',host_id:'cluster'});
 const calls=[];
 const api=async (route,body)=>{
  if(route==='/api/files/roots')return {roots:[{id:'photos',host_id:'cluster',path:'/root'}]};
  if(route==='/api/files/stat?root_id=photos&path=a.md')return {resource:meta()};
  if(route==='/api/files/document-save'){
   assert.equal(body.path,'a.md');assert.equal(body.root_id,'photos');assert.equal(body.file_id,undefined);
   if(body.base_version!==value.version)throw Error('The file changed');
   value.text=body.text;value.version+='!';calls.push(value.text);return{resource:meta()};
  }
  throw Error('Unexpected request '+route);
 };
 const vscode={Uri:{joinPath:(s,n)=>s+'/'+n},workspace:{onDidChangeTextDocument:()=>({dispose(){}})}};
 let receive,close;const events=[];
 const panel={webview:{cspSource:'https://viewer.invalid',asWebviewUri:x=>x,onDidReceiveMessage:fn=>{receive=fn;return{dispose(){}}},postMessage:m=>{events.push(m);return Promise.resolve(true);}},onDidDispose:fn=>close=fn};
 await resolveReviewEditor(vscode,{extensionUri:'ext',globalStorageUri:{fsPath:dir}},{uri,getText:()=>value.text},panel,{api,bytes:async()=>Buffer.from(value.text)});
 const stage=(text,n)=>receive({type:'document-review',action:'stage',id:String(n),revision:n,version:'one',text});
 const request=async(action,extra={})=>{const id=String(Math.random());receive({type:'document-review',action,id,...extra});await flushEditors();return events.find(e=>e.id===id);};
 return {dir,uri,value,calls,close,stage,events,request};
}
test('closing during debounce flushes the final document to its original host',async()=>{
 const f=await fixture();f.stage('First change',1);f.stage('Final change',2);
 assert.equal(localReviewStore(f.dir,f.uri).drafts()[0].text,'Final change');
 f.close();await flushEditors();
 assert.deepEqual(f.calls,['Final change']);assert.deepEqual(localReviewStore(f.dir,f.uri).drafts(),[]);
});
test('window shutdown flushes before disposal and a disk conflict retains the staged draft',async()=>{
 const f=await fixture();f.stage('My document',1);f.value.version='other';f.value.text='Other writer';
 await flushEditors();f.close();await flushEditors();
 assert.equal(f.value.text,'Other writer');assert.deepEqual(f.calls,[]);
 assert.equal(localReviewStore(f.dir,f.uri).drafts()[0].text,'My document');
 assert.equal(f.events.at(-1).type,'review-failed');
});
test('overlapping stores keep the immediately preceding version and preserve each comment',async()=>{
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'lc-review-notes-')),a=localReviewStore(dir,'x'),b=localReviewStore(dir,'x');
 a.observe('Starting document');a.comment({id:'a',text:'Note A',quote:'A'});
 b.observe('Later version');b.observe('Newest version');a.observe('Newest version');b.comment({id:'b',text:'Note B',quote:'B'});
 a.comment({id:'a',text:'Note A',quote:'A'});
 assert.equal(a.read().previous,'Later version');assert.deepEqual(b.read().comments.map(c=>c.id),['a','b']);
});

test('renew reads a changed remote document before updating the visible editor',async()=>{
 const f=await fixture();
 try{
  f.value.version='two';f.value.text='Second paragraph';
  const reply=await f.request('renew');
  assert.equal(reply.error,undefined);
  const update=f.events.find(e=>e.type==='review-update');
  assert.equal(update.version,'two');assert.equal(update.text,'Second paragraph');
 }finally{f.close();await flushEditors();}
});
