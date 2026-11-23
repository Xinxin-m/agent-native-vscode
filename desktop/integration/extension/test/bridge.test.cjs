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

test('model update check is a write route with a long timeout for CLI installs',()=>{assert.equal(bridge.route('/api/models/refresh','POST'),'/api/models/refresh');assert.throws(()=>bridge.route('/api/models/refresh'));assert.match(require('node:fs').readFileSync(require.resolve('../bridge.cjs'),'utf8'),/'\/api\/models\/refresh'\?750000/);});

test('redesign routes: reads are GET only, writes are POST only, near misses are refused',()=>{
 const reads=['/api/notifications?archived=1','/api/refresh?id=job1','/api/project_workspace?id=p1','/api/placements?key=claude-1&limit=1','/api/accounts/picker?model=m'];
 const writes=['/api/notifications/act','/api/seen','/api/view','/api/refresh','/api/live/stop_idle','/api/accounts/logout','/api/accounts/rename','/api/accounts/add','/api/accounts/login','/api/accounts/login/cancel','/api/project_catalog','/api/assign','/api/config'];
 for(const r of reads){assert.equal(bridge.route(r),r);if(!r.startsWith('/api/refresh'))assert.throws(()=>bridge.route(r,'POST'));}
 for(const w of writes){assert.equal(bridge.route(w,'POST'),w);}
 for(const w of ['/api/notifications/act','/api/seen','/api/live/stop_idle','/api/accounts/logout','/api/accounts/rename'])assert.throws(()=>bridge.route(w));
 for(const bad of ['/api/notifications/other','/api/live/stop_all','/api/accounts/pickers','/api/seen/x','/api/placements/x'])assert.throws(()=>bridge.route(bad,'POST'),bad);
 assert.throws(()=>bridge.route('/api/notifications/act'));
});
