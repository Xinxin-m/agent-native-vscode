#!/usr/bin/env python3
"""Loopback-only, read-only graph viewer. Does not start provider sessions."""
import argparse,json,mimetypes,os
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from graph_data import Catalog,HERE

catalog=Catalog();WEB=HERE/'web'
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):
  if args and str(args[1]) not in ('200','304'):super().log_message(fmt,*args)
 def do_GET(self):
  parsed=urlparse(self.path);q={k:v[0] for k,v in parse_qs(parsed.query).items()};host=self.headers.get('Host','')
  if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.json({'error':'Invalid host'},403)
  if self.headers.get('Sec-Fetch-Site')=='cross-site':return self.json({'error':'Cross-site read blocked'},403)
  try:
   if parsed.path=='/api/projects':return self.json({'projects':catalog.projects()})
   if parsed.path=='/api/graph':return self.json(catalog.graph(q.get('project','view-a16z'),q.get('q',''),max(0,int(q.get('offset',0))),min(40,max(1,int(q.get('limit',16)))),q.get('period','7')))
   if parsed.path=='/api/session':return self.json(catalog.session(q.get('key','')))
   if parsed.path=='/api/resource':return self.json(catalog.resource(q.get('id','')))
   if parsed.path.startswith('/api/'):return self.json({'error':'Unknown route'},404)
   name=parsed.path.lstrip('/') or 'index.html';p=(WEB/name).resolve()
   if not p.is_relative_to(WEB.resolve()) or not p.is_file():return self.json({'error':'Not found'},404)
   data=p.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(p))[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' {os.environ.get('LIBRARY_URL','http://127.0.0.1:8744')}");self.end_headers();self.wfile.write(data)
  except (KeyError,ValueError) as e:self.json({'error':str(e)},400)
  except BrokenPipeError:pass
  except Exception as e:
   import traceback;traceback.print_exc();self.json({'error':type(e).__name__+': '+str(e)},500)
 def json(self,data,status=200):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
 def do_POST(self):self.json({'error':'This dashboard is read-only'},405)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=0);a=p.parse_args();server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);url=f'http://127.0.0.1:{server.server_port}';(HERE/'output').mkdir(exist_ok=True);(HERE/'output/url.txt').write_text(url);print(url,flush=True);server.serve_forever()
