'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create,buttonsFor}=require('../notifications.cjs');

const question={id:'broker:r1:q1',source:'agent',kind:'question',title:'Which set?',body:'',chat_title:'Sampling chat',options:['Small sample','Full set'],allow_text:true,needs_answer:true,state:'open',shown:false};
const approval={id:'broker:r1:a1',source:'agent',kind:'approval',title:'Run a command',body:'rm -rf build\nmore',options:['Allow','Allow for this session','Deny'],allow_text:false,needs_answer:true,state:'open',shown:false};

function fixture(items,{trusted=true,claim=true}={}){
 const calls=[],shown=[],contexts=[],box={items,picks:[],input:undefined,pick:undefined,error:null};
 const toast=kind=>(message,opts,...buttons)=>{shown.push({kind,message,titles:buttons.map(b=>b.title)});const choose=box.picks.shift();return Promise.resolve(choose?buttons.find(b=>b.title===choose):undefined);};
 const vscode={
  window:{showInformationMessage:toast('info'),showWarningMessage:toast('warn'),showInputBox:async()=>box.input,showQuickPick:async list=>list.find(i=>i.label===box.pick)},
  commands:{executeCommand:async(name,...args)=>{if(name==='setContext')contexts.push(args);else calls.push(['command',name]);}},
 };
 const api=async(path,body)=>{
  calls.push([path,body]);
  if(box.error)throw box.error;
  if(body?.action==='answer'&&box.answerError)throw box.answerError;
  if(path==='/api/notifications')return {items:box.items,counts:box.counts,settings:box.settings};
  if(body.action==='shown')return {ok:true,claimed:claim?body.ids||[body.id]:[]};
  return {ok:true};
 };
 const poller=create({vscode,api,canAct:()=>trusted,claimant:'t1',setTimer:()=>0,clearTimer:()=>{}});
 const acts=()=>calls.filter(c=>c[0]==='/api/notifications/act').map(c=>c[1]);
 const settle=()=>new Promise(r=>setImmediate(r));
 return {poller,calls,shown,contexts,box,acts,settle};
}

test('desktop syncs notification duration even with an empty feed and preserves Keep open', async()=>{
 const f=fixture([]);
 f.box.settings={duration_seconds:4};await f.poller.tick();await f.poller.tick();
 f.box.settings={duration_seconds:0};await f.poller.tick();
 await f.poller.syncDuration(undefined);await f.poller.syncDuration(61);
 assert.deepEqual(f.contexts.filter(([key])=>key==='library.notificationPopupSeconds'),
  [['library.notificationPopupSeconds',4],['library.notificationPopupSeconds',0]]);
});

test('a pending question raises one toast with its options, Type an answer and Open', async()=>{
 const f=fixture([question]);f.box.picks=[];
 await f.poller.tick();await f.settle();
 assert.equal(f.shown.length,1);
 assert.deepEqual(f.shown[0].titles,['Small sample','Full set','Type an answer','Open']);
 assert.match(f.shown[0].message,/Which set\?/);
 assert.deepEqual(f.acts()[0],{claimant:'t1',ids:[question.id],action:'shown'});
});

test('choosing an option sends act answer with that choice', async()=>{
 const f=fixture([question]);f.box.picks=['Full set'];
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.acts().at(-1),{claimant:'t1',id:question.id,action:'answer',choice:'Full set'});
});

test('an approval uses a warning toast with all three options and Open', async()=>{
 const f=fixture([approval]);f.box.picks=['Deny'];
 await f.poller.tick();await f.settle();
 assert.equal(f.shown[0].kind,'warn');
 assert.deepEqual(f.shown[0].titles,['Allow','Allow for this session','Deny','Open']);
 assert.equal(f.acts().at(-1).choice,'Deny');
});

test('Type an answer opens an input box and sends the text', async()=>{
 const f=fixture([question]);f.box.picks=['Type an answer'];f.box.input='  both, please ';
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.acts().at(-1),{claimant:'t1',id:question.id,action:'answer',text:'both, please'});
});

test('an empty or cancelled input box sends nothing', async()=>{
 const f=fixture([question]);f.box.picks=['Type an answer'];f.box.input=undefined;
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.acts().map(a=>a.action),['shown']);
});

test('more options than fit show the first ones plus More, which opens a quick pick', async()=>{
 const wide={...question,id:'x',options:['A','B','C','D'],allow_text:true};
 assert.deepEqual(buttonsFor(wide,true),{options:['A'],more:true,text:true});
 const f=fixture([wide]);f.box.picks=['More'];f.box.pick='C';
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.shown[0].titles,['A','More','Type an answer','Open']);
 assert.deepEqual(f.acts().at(-1),{claimant:'t1',id:'x',action:'answer',choice:'C'});
});

test('Open shows the Notifications page', async()=>{
 const f=fixture([question]);f.box.picks=['Open'];
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.calls.filter(c=>c[0]==='command'),[['command','library.notifications']]);
 assert.deepEqual(f.acts().map(a=>a.action),['shown']);
});

test('the cross sends shown and nothing else', async()=>{
 const f=fixture([question]);f.box.picks=[];
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.acts().map(a=>a.action),['shown','shown']);
 assert.ok(f.acts().every(a=>!('choice' in a)&&!('text' in a)));
});

test('the next poll does not raise a second toast for the same item', async()=>{
 const f=fixture([question]);
 await f.poller.tick();await f.poller.tick();await f.settle();
 assert.equal(f.shown.length,1);
});

test('an item claimed by another window is not shown here', async()=>{
 const f=fixture([question],{claim:false});
 await f.poller.tick();await f.settle();
 assert.equal(f.shown.length,0);
});

test('a new app item toasts with Open only; answered and archived items do not toast', async()=>{
 const app={id:'notice:1',source:'app',kind:'limit_reached',title:'Notice',body:'Claude limit reached',options:[],allow_text:false,needs_answer:false,state:'open',shown:false};
 const done={...question,id:'d',state:'answered'};
 const f=fixture([app,done]);
 await f.poller.tick();await f.settle();
 assert.equal(f.shown.length,1);
 assert.deepEqual(f.shown[0].titles,['Open']);
 assert.equal(f.shown[0].message,'Claude limit reached');
});

test('a signed-out account offers Sign in and Not now', async()=>{
 const out={id:'signedout:c1',source:'app',kind:'signed_out',title:'Claude Easy Labs signed out',body:'',options:['Sign in','Not now'],allow_text:false,needs_answer:true,state:'open',shown:false};
 const f=fixture([out]);f.box.picks=['Sign in'];
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.shown[0].titles,['Sign in','Not now','Open']);
 assert.equal(f.acts().at(-1).choice,'Sign in');
});

test('an untrusted workspace gets Open only, never an answer button', async()=>{
 const f=fixture([question],{trusted:false});
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.shown[0].titles,['Open']);
});

test('a 404 from an old server is silent', async()=>{
 const f=fixture([question]);const e=Error('HTTP 404');e.status=404;f.box.error=e;
 await assert.rejects(f.poller.tick());
 assert.equal(f.shown.length,0);
});

test('the loop swallows a 404 and slows down', async()=>{
 const timers=[],calls=[],fns=[];
 const e=Error('HTTP 404');e.status=404;
 const p=create({vscode:{window:{showWarningMessage:()=>calls.push('warn')},commands:{}},api:async()=>{throw e;},slow:777,setTimer:(fn,ms)=>{fns.push(fn);timers.push(ms);return 1;},clearTimer:()=>{}});
 p.start();await fns.shift()();
 assert.deepEqual(calls,[]);
 assert.equal(timers.at(-1),777);
});

test('a failed claim lets the next poll try again', async()=>{
 const f=fixture([question]);
 const real=f.box.error;f.box.error=Error('down');
 await assert.rejects(f.poller.tick());
 f.box.error=null;
 await f.poller.tick();await f.settle();
 assert.equal(f.shown.length,1);
});

test('the open count (counts.open) drives the native bell badge, seen does not clear it, and it is sent only when it changes', async()=>{
 const f=fixture([{...question,seen:false},{...approval,seen:true}]);
 await f.poller.tick();await f.poller.tick();await f.settle();
 f.box.counts={open:3,unseen:3};await f.poller.tick();
 f.box.counts={open:3,unseen:0};await f.poller.tick();
 f.box.counts={open:0,unseen:0};await f.poller.tick();await f.settle();
 assert.deepEqual(f.contexts,[['library.notificationsOpen',2],['library.notificationsOpen',3],['library.notificationsOpen',0]]);
});

test('a failed-run item offers its recovery options as toast buttons; a refused recovery shows its message', async()=>{
 const failed={id:'notice:k1',source:'app',kind:'limit_reached',title:'Weekly limit reached',body:'',chat_title:'Chat 7',chat_key:'claude-7',run_id:'r7',options:['Retry message','Continue on lab@example.test','Move to Codex'],allow_text:false,needs_answer:true,state:'open',shown:false};
 const f=fixture([failed]);f.box.picks=['Continue on lab@example.test'];
 await f.poller.tick();await f.settle();
 assert.deepEqual(f.shown[0].titles,['Retry message','Continue on lab@example.test','Move to Codex','Open']);
 assert.deepEqual(f.acts().at(-1),{claimant:'t1',id:failed.id,action:'answer',choice:'Continue on lab@example.test'});
 const g=fixture([failed]);g.box.picks=['Retry message'];g.box.answerError=Object.assign(Error('A newer message exists in this chat.'),{status:409});
 await g.poller.tick();await g.settle();await g.settle();
 assert.equal(g.acts().at(-1).choice,'Retry message');
 assert.deepEqual(g.shown.at(-1),{kind:'warn',message:'A newer message exists in this chat.',titles:[]});
});

test('the bell follows the utility window preference and History keeps its own identity',()=>{
 const {resourceKey,utilityDestination}=require('../panes.cjs'),host=require('node:fs').readFileSync(require('node:path').join(__dirname,'../extension.cjs'),'utf8');
 assert.equal(resourceKey({kind:'notifications',title:'Notifications'}),'notifications:');
 assert.equal(resourceKey({kind:'notifications',title:'History',meta:{view:'history'}}),'notifications:history');
 assert.ok(host.includes("command('library.notifications',()=>open({kind:'notifications',title:'Notifications'},'tab'))"));
 for(const kind of ['notifications','settings','accounts','recent','projects']){
  assert.equal(utilityDestination(kind,'tab',undefined,{}),true);
  assert.equal(utilityDestination(kind,undefined,undefined,{utilityPopups:false}),false);
  assert.equal(utilityDestination(kind,'right',undefined,{}),undefined);
  assert.equal(utilityDestination(kind,'tab',2,{}),undefined);
 }
 assert.equal(utilityDestination('chat','tab',undefined,{}),undefined);
});
test('Cmd+L opens Notifications in the secondary side bar, or hides the bar when Notifications is showing',()=>{
 const fs=require('node:fs'),path=require('node:path'),pkg=JSON.parse(fs.readFileSync(path.join(__dirname,'../package.json'),'utf8')),c=pkg.contributes;
 const host=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),boot=fs.readFileSync(path.join(__dirname,'../../ui/src/native/boot.ts'),'utf8');
 const cmdL=c.keybindings.filter(k=>k.mac==='cmd+l');
 assert.deepEqual(cmdL.map(k=>[k.command,k.key,k.when]),[['library.toggleNotificationsSidebar','ctrl+l',undefined]]);
 assert.match(host,/registerCommand\('library\.toggleNotificationsSidebar',\(\)=>vscode\.commands\.executeCommand\(notificationsView\?\.visible\?'workbench\.action\.closeAuxiliaryBar':'library\.notificationsSidebar\.focus'\)\)/);
 assert.deepEqual(c.viewsContainers.secondarySidebar,[{id:'library-notifications',title:'Notifications',icon:'$(bell)'}]);
 assert.deepEqual(c.views['library-notifications'],[{id:'library.notificationsSidebar',name:'Notifications',type:'webview'}]);
 assert.match(host,/registerWebviewViewProvider\('library\.notificationsSidebar',\{resolveWebviewView\(view\)\{notificationsView=view;.*?connect\(view,'notifications',\{kind:'notifications',meta:\{view:'sidebar'\}\},'notifications:sidebar'\);\}\}/);
 // A focused webview swallows keys, so the UI forwards Cmd+L to the same command.
 assert.match(boot,/'terminal':'toggleSecondarySidebar'/);
 assert.match(host,/m\.action==='toggleSecondarySidebar'\)return vscode\.commands\.executeCommand\('library\.toggleNotificationsSidebar'\)/);
 assert.ok(require('../rpc-flow.cjs')&&/'toggleSecondarySidebar'/.test(fs.readFileSync(path.join(__dirname,'../rpc-flow.cjs'),'utf8')));
});
test('an old server answering a new route with an HTML 404 rejects with status 404 and code not_json',async()=>{
 const http=require('node:http'),bridge=require('../bridge.cjs');
 const server=http.createServer((req,res)=>{res.writeHead(404,{'Content-Type':'text/html'});res.end('<html>Not found</html>')});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{await assert.rejects(bridge.request('http://127.0.0.1:'+server.address().port+'/','/api/notifications'),e=>e.status===404&&e.data?.code==='not_json');}
 finally{server.close()}
});
