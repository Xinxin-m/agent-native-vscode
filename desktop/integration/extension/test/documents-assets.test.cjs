const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
async function fixture(uri){
 const html=await import('../html-source.mjs'),providers={},calls=[];
 const vscode={window:{registerCustomEditorProvider:(id,provider)=>(providers[id]=provider,{dispose(){}})},workspace:{fs:{isWritableFileSystem:scheme=>scheme==='file'},onDidChangeTextDocument:()=>({dispose(){}})},Uri:{joinPath:(_,name)=>({with(){return 'https://webview.invalid/'+name}})}};
 const context={subscriptions:[],extensionUri:{}};
 const module={exports:{}},realRequire=require('node:module').createRequire(path.resolve(__dirname,'../documents.cjs'));
 const code=fs.readFileSync(path.resolve(__dirname,'../documents.cjs'),'utf8');
 vm.runInNewContext(code,{require:name=>name==='vscode'?vscode:name==='./html-source.cjs'?html:realRequire(name),module,exports:module.exports,Buffer});
 const api=async route=>{calls.push(route);if(route==='/api/files/roots')return {roots:[{id:'root',host_id:'cluster',path:'/r'}]};return {resource:{size:4}};};
 module.exports.register(context,{api,bytes:async route=>{calls.push(route);return Buffer.from('test')}});
 const panel={webview:{cspSource:'https://webview.invalid',asWebviewUri:x=>x,onDidReceiveMessage:()=>({dispose(){}})},onDidDispose(){}};
 return {panel,providers,calls,document:{uri,version:1,getText:()=>'<p>Hello</p><img src="pic.png">'}};
}
test('registered remote documents embed scoped assets into their preview boot payload',async()=>{const f=await fixture({scheme:'library-file',authority:'root',path:'/report.html'});await f.providers['library.html'].resolveCustomTextEditor(f.document,f.panel);const match=f.panel.webview.html.match(/window.__DOCUMENT_BOOT__=(.*?);<\/script>/);assert.ok(match);const boot=JSON.parse(match[1]);assert.match(boot.preview,/data:image\/png;base64,dGVzdA==/);assert.equal(boot.text,f.document.getText());assert.equal(boot.readOnly,true);assert.ok(f.calls.some(p=>p.includes('root_id=root&path=pic.png')));});
test('exact-reference documents remain rendered and cannot load siblings',async()=>{const f=await fixture({scheme:'library-reference',authority:'cluster',path:'/r/report.html',query:'file_id=exact&chat_key=thread'});await f.providers['library.html'].resolveCustomTextEditor(f.document,f.panel);const boot=JSON.parse(f.panel.webview.html.match(/window.__DOCUMENT_BOOT__=(.*?);<\/script>/)[1]);assert.equal(boot.readOnly,true);assert.doesNotMatch(boot.preview,/src="pic.png"|data:image/);assert.deepEqual(f.calls,['/api/files/stat?file_id=exact']);});
