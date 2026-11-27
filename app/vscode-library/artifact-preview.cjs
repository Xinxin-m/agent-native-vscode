'use strict';
const http = require('node:http');
const path = require('node:path').posix;
const {randomBytes} = require('node:crypto');
const {readFile} = require('node:fs/promises');
const runtimeFiles = ['katex.min.js', 'auto-render.min.js', 'runtime.js'];

// Each open document owns an origin. Only a registered root can supply sibling assets.
async function artifactPreview({name, target, source, api, bytes}) {
 const token = randomBytes(24).toString('hex');
 const prefix = '/' + token + '/';
 const runtimePrefix = '/' + token + '-runtime/';
 const scripts = runtimeFiles.map(file => '<script src="' + runtimePrefix + file + '"></script>').join('');
 let allowNetwork = false;
 const server = http.createServer(async (req, res) => {
  const fail = status => { res.writeHead(status); res.end(); };
  if (req.headers.host !== '127.0.0.1:' + server.address()?.port || !['GET','HEAD'].includes(req.method)) return fail(403);
  try {
   const pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
   if (pathname.startsWith(runtimePrefix)) {
    const asset = pathname.slice(runtimePrefix.length);
    if (!runtimeFiles.includes(asset)) return fail(404);
    const body = await readFile(__dirname + '/media/preview/' + asset);
    res.writeHead(200, {'Content-Type':'text/javascript','Content-Length':body.length,'X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'});
    return res.end(req.method === 'HEAD' ? undefined : body);
   }
   const scoped = pathname.startsWith(prefix);
   if (!scoped && req.headers['sec-fetch-site'] !== 'same-origin') return fail(403);
   const relative = pathname.slice(scoped ? prefix.length : 1);
   if (!relative || relative.split('/').some(part => !part || part === '.' || part === '..') || /[\\\0]/.test(relative)) return fail(404);
   let body, mime;
   if (relative === name) { body = Buffer.from(source()); mime = 'text/html; charset=utf-8'; }
   else {
    if (!target?.root_id) return fail(404);
    const query = new URLSearchParams({root_id:target.root_id, path:path.join(path.dirname(target.path),relative)});
    const {resource} = await api('/api/files/stat?' + query);
    if (!resource || resource.size > 64 * 1024 * 1024) return fail(413);
    body = Buffer.from(await bytes('/api/files/content?' + query)); mime = resource.mime;
   }
   if (mime.split(';', 1)[0] === 'text/html') body = Buffer.concat([body, Buffer.from(scripts)]);
   const network = allowNetwork ? ' https:' : '';
   res.writeHead(200, {'Content-Type':mime,'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"sandbox allow-scripts allow-same-origin; default-src 'none'; script-src 'self' 'unsafe-inline' blob:" + network + "; style-src 'self' 'unsafe-inline'" + network + "; img-src 'self' data: blob:" + network + "; font-src 'self' data:" + network + "; connect-src 'self'" + network + "; worker-src 'self' blob:; frame-src 'self' about:; base-uri 'none'; form-action 'none'"});
   res.end(req.method === 'HEAD' ? undefined : body);
  } catch { fail(404); }
 });
 await new Promise((resolve,reject) => { server.once('error',reject); server.listen(0,'127.0.0.1',resolve); });
 server.unref();
 const origin = 'http://127.0.0.1:' + server.address().port;
 return {origin, url:origin + prefix + encodeURIComponent(name), network(value){allowNetwork = value === true;}, close(){server.close();server.closeAllConnections();}};
}
module.exports = {artifactPreview};
