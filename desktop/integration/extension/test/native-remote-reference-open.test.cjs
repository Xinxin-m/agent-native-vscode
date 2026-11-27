const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createProvider}=require('../remote-document-reference.cjs');
const {browserWindowCommand}=require('../external-open.cjs');
function fixture(roots=[],record={file_id:'one',host_id:'cluster',path:'/r/report.md',size:3}){
 const calls=[],vscode={FileType:{File:1},FilePermission:{Readonly:1},EventEmitter:class{event=()=>{};dispose(){}},Disposable:class{},FileSystemError:{NoPermissions:message=>Error(message)},Uri:{from:options=>({...options,toString(){return this.scheme+'://'+this.authority+this.path+'?'+this.query}}),file:path=>({scheme:'file',path,toString:()=>path}),parse:url=>({scheme:'http',toString:()=>url})},window:{tabGroups:{all:[{viewColumn:1}],activeTabGroup:{viewColumn:1}}},commands:{executeCommand:async(...args)=>calls.push(args)}};
 const api=async(route,body)=>{calls.push([route,body]);if(route.startsWith('/api/files/roots'))return {roots};return {resource:record};};
 const bytes=async route=>{calls.push([route]);return Buffer.from('one');};
 const scope={preferenceWrites:{settled:Promise.resolve()},utilityDestination:require('../panes.cjs').utilityDestination,vscode,path,URLSearchParams,preferences:{maxAutoPanes:3,openDefault:'tab'},targetColumn:()=>2,base:()=> 'http://127.0.0.1:8750',api,remoteProvider:{readFile:bytes},referenceProvider:createProvider(vscode,api,bytes),context:{globalStorageUri:{fsPath:'/fixture'}},randomUUID:()=> 'copy',fs:{stat:async()=>{throw Object.assign(Error('missing'),{code:'ENOENT'})},mkdir:async()=>{},writeFile:async(...args)=>calls.push(['write',...args])},require:()=>({openExternal:async(...args)=>{calls.push(['external',...args]);return true;}})};
 const source=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),start=source.indexOf(' async function open(resource,destination,explicitColumn){'),end=source.indexOf('\n function connect(',start);
 vm.runInNewContext(source.slice(start,end)+'\nthis.open=open;',scope);
 return {scope,calls};
}
test('actual native open uses an exact transcript reference when no root exists',async()=>{const {scope,calls}=fixture();await scope.open({kind:'file',host_id:'cluster',path:'/r/report.md',meta:{chat_key:'thread'}},'tab');const opened=calls.find(([command])=>command==='vscode.open');assert.equal(opened[1].scheme,'library-reference');assert.equal(new URLSearchParams(opened[1].query).get('chat_key'),'thread');assert.equal(opened[2].viewColumn,2);assert.equal(calls.some(([route,body])=>route==='/api/files/roots'&&body),false);});
test('actual native open retains root scoping for registered files',async()=>{const {scope,calls}=fixture([{id:'root',host_id:'cluster',path:'/r'}]);await scope.open({kind:'file',host_id:'cluster',path:'/r/report.md'},'tab');const opened=calls.find(([command])=>command==='vscode.open');assert.equal(opened[1].scheme,'library-file');assert.equal(opened[1].authority,'root');assert.equal(calls.some(([route])=>route==='/api/files/reference'),false);});
test('external opening of an exact reference copies only the referenced bytes',async()=>{const {scope,calls}=fixture();await scope.open({kind:'file',host_id:'cluster',path:'/r/report.md',meta:{chat_key:'thread'}},'default-browser');assert.equal(calls.find(([route])=>route.startsWith('/api/files/content'))[0],'/api/files/content?file_id=one');assert.equal(calls.find(([operation])=>operation==='write')[1],'/fixture/remote-open/copy/report.md');assert.equal(calls.find(([operation])=>operation==='external')[1],'/fixture/remote-open/copy/report.md');});
test('a Mac conversation opens a PDF that is not on this Mac in the native exact-file reader',async()=>{
 const path='/cluster/data/research/J_windows.pdf';
 const {scope,calls}=fixture([{id:'sc-root',host_id:'cluster',path:'/cluster/data/research'}],{file_id:'pdf',host_id:'cluster',path,size:24});
 await scope.open({kind:'file',host_id:'mac',path,meta:{chat_key:'codex-123'}},'tab');
 assert.equal(calls[0][0],'/api/files/reference');
 assert.equal(calls[0][1].chat_key,'codex-123');
 const opened=calls.find(([command])=>command==='vscode.open');
 assert.equal(opened[1].scheme,'library-reference');
 assert.equal(opened[1].authority,'cluster');
 assert.equal(opened[1].path,path);
});
test('a moved Mac chat link opens the current native file',async()=>{
 const current='/r/reports/markouts.html',original='/r/old-markouts.html';
 const {scope,calls}=fixture([],{file_id:'moved',host_id:'mac',path:current,evidence_path:original,size:24});
 scope.fs.stat=async target=>{assert.equal(target,current);return {isFile:()=>true}};
 await scope.open({kind:'file',host_id:'mac',path:original,meta:{chat_key:'codex-123'}},'default-browser');
 assert.equal(calls[0][0],'/api/files/reference');
 assert.equal(calls[0][1].chat_key,'codex-123');
 assert.equal(calls[0][1].path,original);
 assert.equal(calls.find(([operation])=>operation==='external')[1],current);
});
test('an external PDF keeps its page and requests a separate browser window',async()=>{
 const {scope,calls}=fixture([],{file_id:'pdf',host_id:'mac',path:'/r/report.pdf'});
 scope.fs.stat=async()=>({isFile:()=>true});
 await scope.open({kind:'file',host_id:'mac',path:'/r/report.pdf',page:3,meta:{chat_key:'thread'}},'default-browser-window');
 assert.deepEqual(calls.find(([action])=>action==='external'),['external','/r/report.pdf#page=3',true,true]);
});
test('a remote document opens its authorized Workbench link in a separate browser window',async()=>{
 const path='/cluster/report.pdf';
 const {scope,calls}=fixture([],{file_id:'pdf',host_id:'cluster',path});
 await scope.open({kind:'file',host_id:'mac',path,page:3,meta:{chat_key:'thread'}},'default-browser-window');
 const [,href,browser,newWindow]=calls.find(([action])=>action==='external');
 const url=new URL(href);
 assert.equal(url.origin+url.pathname,'http://127.0.0.1:8750/workbench');
 assert.deepEqual(JSON.parse(new URLSearchParams(url.hash.slice(1)).get('resource')),
  {kind:'file',host_id:'cluster',path,page:3,file_id:'pdf',meta:{chat_key:'thread'}});
 assert.equal(browser,true);assert.equal(newWindow,true);
 assert.equal(calls.some(([action])=>action==='write'||action.startsWith('/api/files/content')),false);
});
test('the browser command keeps the URL as one argument and requests a window',()=>{
 const url='file:///tmp/report%20%231.pdf#page=3';
 assert.deepEqual(browserWindowCommand(url,'com.google.Chrome'),['/usr/bin/open',['-n','-b','com.google.Chrome','--args','--new-window',url]]);
 assert.deepEqual(browserWindowCommand(url,'org.mozilla.firefox'),['/usr/bin/open',['-n','-b','org.mozilla.firefox','--args','-new-window',url]]);
 const [command,args]=browserWindowCommand(url,'com.apple.Safari');
 assert.equal(command,'/usr/bin/osascript');
 assert.deepEqual(args.slice(-2),['--',url]);
 assert.throws(()=>browserWindowCommand(url,'com.example.unknown'),/unavailable/);
});
test('first-party Workbench file links prefer a covering SC root over transcript evidence',async()=>{
 const path='/workspace/reports/weekly-activity.html';
 const {scope,calls}=fixture([{id:'sc-root',host_id:'sc',path:'/workspace/reports'}],{file_id:'report',host_id:'sc',path,size:4});
 await scope.open({kind:'file',host_id:'sc',path,mode:'render',meta:{workbench_link:true,chat_key:'source-chat'}},'tab');
 assert.deepEqual(calls.map(([action])=>action),['/api/files/roots?host_id=sc','vscode.open']);
 const [,uri]=calls[1];
 assert.equal(uri.scheme,'library-file');
 assert.equal(uri.authority,'sc-root');
});
test('an uncovered first-party Workbench file preserves encoded conversation evidence',async()=>{
 const path='/workspace/reports/weekly-activity.html';
 const {scope,calls}=fixture([],{file_id:'report',host_id:'sc',path,size:4});
 await scope.open({kind:'file',host_id:'sc',path,mode:'render',meta:{workbench_link:true,chat_key:'source-chat'}},'tab');
 assert.equal(calls[0][0],'/api/files/roots?host_id=sc');
 assert.deepEqual([calls[1][0],calls[1][1].chat_key],['/api/files/reference','source-chat']);
 assert.equal(calls.find(([action])=>action==='vscode.open')[1].scheme,'library-reference');
});
