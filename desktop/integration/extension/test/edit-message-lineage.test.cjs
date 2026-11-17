const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ts=require('../../../vscode/node_modules/typescript');
const sources=[['native',path.resolve(__dirname,'../../ui/src/chat/Conversation.tsx')],['browser',path.resolve(__dirname,'../../../../chat-dashboard/workbench/src/chat/Conversation.tsx')]];
function setup(file,{key='claude-source',turns=[],failure}={}){
 const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let fn;
 function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='editMessage')fn=node;ts.forEachChild(node,visit)}visit(source);assert(fn,'Actual edit callback exists');
 const code=ts.transpileModule(fn.getText(source),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 const opened=[],notices=[],reads=[];
 const edit=new Function('wb','nativeKey','api','query','metadata','state','title','boundHost','crypto','message',code+';return editMessage;')(
 {open:value=>opened.push(value),notify:value=>notices.push(value)},key,async url=>{reads.push(url);if(failure)throw Error(failure);return {turns}},(url,params)=>url+'?key='+params.key,{cwd:'/project'},{provider:'claude',cwd:'/fallback'},'Original','sc',{randomUUID:()=> 'fixture-draft'},e=>e.message);
 return {edit,opened,notices,reads};
}
for(const [client,file] of sources){
 if(!fs.existsSync(file)){test(client+' source is external to this checkout',{skip:true},()=>{});continue;}
 test(client+': first message edits retain source and an empty direct prefix',async()=>{const t=setup(file);await t.edit('First',0);assert.equal(t.opened.length,1);assert.deepEqual(t.opened[0].meta,{source:'claude',cwd:'/project',execution_host:'sc',forkSourceKey:'claude-source',forkThroughTurn:-1,editText:'First',focusComposer:true});assert.equal(t.reads.length,0)});
 test(client+': later edits fork immediately before the selected turn',async()=>{const t=setup(file);await t.edit('Later',4);assert.equal(t.opened[0].meta.forkThroughTurn,3);assert.equal(t.opened[0].meta.forkSourceKey,'claude-source')});
 test(client+': freshly indexed first message uses the same branch contract',async()=>{const t=setup(file,{turns:[{role:'user',text:'First'},{role:'assistant',text:'Answer'}]});await t.edit('First');assert.equal(t.opened[0].meta.forkThroughTurn,-1);assert.equal(t.reads.length,1)});
 test(client+': unindexed messages cannot silently become unrelated new chats',async()=>{for(const options of [{key:''},{turns:[]}]){const t=setup(file,options);await t.edit('Missing');assert.equal(t.opened.length,0);assert.match(t.notices[0],/indexed/)}const t=setup(file);await t.edit('Missing',-1);assert.equal(t.opened.length,0)});
 test(client+': indexing failure preserves the original conversation',async()=>{const t=setup(file,{failure:'Unavailable'});await t.edit('First');assert.equal(t.opened.length,0);assert.deepEqual(t.notices,['Unavailable'])});
}
