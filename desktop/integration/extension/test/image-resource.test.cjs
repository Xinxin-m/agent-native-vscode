const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http');
const {imageResource}=require('../bridge.cjs');
test('native images preserve bytes and reject non-image or non-allowlisted routes',async()=>{
 const server=http.createServer((req,res)=>{if(req.url.includes('bad')){res.setHeader('Content-Type','text/html');res.end('<script>bad</script>')}else{res.setHeader('Content-Type','image/png');res.end(Buffer.from([137,80,78,71]))}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 try{
  assert.equal(await imageResource(base,'/api/attachment?id=good'),'data:image/png;base64,iVBORw==');
  assert.equal(await imageResource(base,'/api/files/content?file_id=good'),'data:image/png;base64,iVBORw==');
  await assert.rejects(imageResource(base,'/api/attachment?id=bad'),/unavailable/);
  await assert.rejects(imageResource(base,'/api/accounts'),/Unsupported image route/);
  await assert.rejects(imageResource(base,'//example.test/api/attachment'),/Unsupported image route/);
  await assert.rejects(imageResource(base,'/api/attachment#secret'),/Unsupported image route/);
 }finally{await new Promise(resolve=>server.close(resolve))}
});
