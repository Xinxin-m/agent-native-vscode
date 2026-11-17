const {test}=require('node:test');const assert=require('node:assert/strict');const http=require('node:http');
let bridge;try{bridge=require('../bridge.cjs')}catch{}
test('bridge allows exact routes on loopback origins only',()=>{
 assert.ok(bridge,'bridge implementation exists');
 for(const url of ['https://example.com','http://127.0.0.1.evil.test','http://user@localhost:8744','http://localhost:8744/path'])assert.throws(()=>bridge.endpoint(url));
 for(const route of ['//example.com/api/state','/api/state/../config','/api/state#x','/api/%73tate','/private/file'])assert.throws(()=>bridge.route(route));
 assert.equal(bridge.route('/api/conversation/run?key=codex-test'),'/api/conversation/run?key=codex-test');
 for(const name of ['resources','resource','tool-analytics']){const route='/api/settings/'+name+'?host=mac';assert.equal(bridge.route(route),route);assert.throws(()=>bridge.route(route,'POST'));}
 assert.throws(()=>bridge.route('/api/settings/other'));
 assert.throws(()=>bridge.route('/api/state','POST'));
 for(const route of ['/api/projects','/api/assign']){assert.equal(bridge.route(route,'POST'),route);assert.throws(()=>bridge.route(route));assert.throws(()=>bridge.route(route+'/unexpected','POST'));}
});
test('bridge preserves conflict status and stable retry payload',async()=>{
 assert.ok(bridge,'bridge implementation exists');
 const seen=[];const server=http.createServer((req,res)=>{let raw='';req.on('data',b=>raw+=b);req.on('end',()=>{seen.push(raw);res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:'Active writer',code:'session_busy',active_run_id:'run-1'}));});});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{const base='http://127.0.0.1:'+server.address().port;for(let i=0;i<2;i++)await assert.rejects(bridge.request(base,'/api/runs',{request_id:'same-retry',message:'hello'}),e=>e.status===409&&e.data.active_run_id==='run-1');assert.equal(seen[0],seen[1]);}finally{await new Promise(r=>server.close(r));}
});
test('SSE decodes fragmented Unicode without losing records',()=>{
 assert.ok(bridge,'bridge implementation exists');const events=[];const parser=bridge.eventParser(e=>events.push(e));
 const raw=Buffer.from('event: delta\ndata: {"seq":1,"event":"delta","data":{"text":"你好"}}\n\n');for(const b of raw)parser(Buffer.from([b]));
 assert.deepEqual(events,[{seq:1,event:'delta',data:{text:'你好'}}]);
});

test('bridge permits the account login code submission only as a write',()=>{
 assert.equal(bridge.route('/api/accounts/login/code','POST'),'/api/accounts/login/code');
 assert.throws(()=>bridge.route('/api/accounts/login/code'));
});

test('remote model discovery is an explicit metadata request',()=>{assert.equal(bridge.route('/api/models/discover','POST'),'/api/models/discover');assert.throws(()=>bridge.route('/api/models/discover'));assert.throws(()=>bridge.route('/api/models/discover/extra','POST'));});
