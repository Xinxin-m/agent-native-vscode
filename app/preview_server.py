"""One scoped loopback origin per artifact, with relative/root-relative assets."""
import secrets
import posixpath
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit

import file_service as fs


class PreviewServer:
    def __init__(self):
        self.tokens = {}
        self.listeners = {}
        self.lock = threading.RLock()
        # Retain the first listener for existing diagnostics/tests.
        self.http = None

    def start(self, token):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.do_GET()

            def do_POST(self):
                self.send_error(405)

            def do_GET(self):
                origin = f'http://127.0.0.1:{self.server.server_port}'
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    self.send_error(403)
                    return
                with owner.lock:
                    scope = owner.tokens.get(token)
                if not scope:
                    self.send_error(404)
                    return
                if scope['expires_at'] <= time.time():
                    self.send_error(410, 'Preview expired; refresh from the workbench')
                    return
                path = urlsplit(self.path).path
                token_path = path.startswith('/p/')
                if token_path:
                    fields = path.split('/', 3)
                    if len(fields) != 4 or fields[2] != token:
                        self.send_error(404)
                        return
                    relative = unquote(fields[3])
                else:
                    # Each listener has exactly one scope. Root assets need no
                    # referrer/cookie lookup and cannot select another preview.
                    if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
                        self.send_error(403)
                        return
                    if self.headers.get('Origin') not in (None, origin):
                        self.send_error(403)
                        return
                    relative = unquote(path.lstrip('/'))
                if not relative or relative.endswith('/'):
                    relative += 'index.html'
                try:
                    if scope.get('file_id'):
                        if relative != scope['entry']:
                            raise fs.FileError('outside_root', 'This preview only permits its referenced file', 403)
                        resource = scope['service'].stat(file_id=scope['file_id'])
                        raw = scope['service'].read(file_id=scope['file_id']) if self.command != 'HEAD' else None
                    else:
                        scoped_path = '/'.join(filter(None, (scope.get('prefix',''), relative)))
                        resource = scope['service'].stat(scope['root_id'], scoped_path)
                        raw = scope['service'].read(scope['root_id'], scoped_path) if self.command != 'HEAD' else None
                except fs.FileError as exc:
                    self.send_error(exc.status, str(exc))
                    return
                self.send_response(200)
                self.send_header('Content-Type', resource['mime'])
                self.send_header('Content-Length', str(len(raw) if raw is not None else resource['size']))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
                network = ' https:' if scope['allow_network'] else ''
                self.send_header('Content-Security-Policy',
                    "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' blob:" + network + "; "
                    "style-src 'self' 'unsafe-inline'" + network + "; img-src 'self' data: blob:" + network + "; "
                    "font-src 'self' data:" + network + "; connect-src 'self'" + network + "; object-src 'none'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors " + scope['app_origin'] + "; sandbox allow-scripts allow-same-origin")
                self.end_headers()
                if raw is not None:
                    self.wfile.write(raw)

        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        http.daemon_threads = True
        with self.lock:
            self.listeners[token] = http
            if self.http is None:
                self.http = http
        threading.Thread(target=lambda:http.serve_forever(poll_interval=.02), daemon=True,
                         name='workbench-preview-'+token[:6]).start()
        return http

    def register(self, host_id, directory, entry, app_origin, allow_network=False, root_id=None, file_id=None):
        if not isinstance(allow_network, bool):
            raise fs.FileError('invalid_request', 'allow_network must be a boolean')
        parsed = urlsplit(app_origin)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise fs.FileError('invalid_origin','Preview parent must be the loopback workbench')
        fs.parts(entry)
        if not entry:
            raise fs.FileError('invalid_path', 'Preview entry is required')
        prefix = ''
        if file_id:
            service = fs.instance()
            resource = service.stat(file_id=file_id)
            if resource['host_id'] != (host_id or 'mac') or resource['path'] != posixpath.join(directory, entry):
                raise fs.FileError('outside_root', 'Preview does not match its file reference', 403)
            root = {'id':None, 'path':directory}
        elif root_id:
            service = fs.instance()
            root = service.get_root(root_id)
            if root['host_id'] != (host_id or 'mac'):
                raise fs.FileError('outside_root', 'Preview host does not match its root', 403)
            prefix = posixpath.relpath(directory, root['path'])
            if prefix == '.': prefix = ''
            fs.parts(prefix)
            service.stat(root_id, '/'.join(filter(None, (prefix,entry))))
        else:
            fs.local_host(host_id)
            service = fs.FileService(default_root=None)
            root = service._root(directory,'read')
            service.roots = {root['id']:root}
            service.stat(root['id'],entry)
        token = secrets.token_urlsafe(32)
        expires = time.time()+1800
        expired_listeners = []
        with self.lock:
            for old in [key for key,value in self.tokens.items() if value['expires_at'] <= time.time()]:
                self.tokens.pop(old, None)
                listener = self.listeners.pop(old, None)
                if listener:
                    expired_listeners.append(listener)
                    if self.http is listener:
                        self.http = next(iter(self.listeners.values()), None)
            if len(self.tokens) >= 128:
                raise fs.FileError('preview_limit','Close or wait for existing previews to expire',429)
            self.tokens[token] = {'service':service,'root_id':root['id'],'expires_at':expires,
                                  'app_origin':app_origin,'allow_network':allow_network,
                                  'file_id':file_id,'entry':entry,'prefix':prefix}
        for listener in expired_listeners:
            listener.shutdown()
            listener.server_close()
        try:
            http = self.start(token)
        except OSError:
            with self.lock:
                self.tokens.pop(token, None)
            raise fs.FileError('preview_unavailable', 'Cannot start an isolated preview listener',503)
        return {'ok':True,'preview_id':token,
                'url':f'http://127.0.0.1:{http.server_port}/p/{token}/' + quote(entry,safe='/'),
                'expires_at':expires,'artifact_directory':root['path'],'allow_network':allow_network}

    def close(self):
        with self.lock:
            listeners = list(self.listeners.values())
            self.listeners.clear()
            self.tokens.clear()
            self.http = None
        for listener in listeners:
            listener.shutdown()
            listener.server_close()


_INSTANCE = None
_LOCK = threading.Lock()


@fs.guarded
def register(req, query, body):
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            _INSTANCE = PreviewServer()
    return _INSTANCE.register(body.get('host_id'),body.get('artifact_directory'),body.get('entry_path'),
                              'http://' + req.headers.get('Host',''),body.get('allow_network',False),
                              body.get('root_id'),body.get('file_id'))


ROUTES = {('POST','/api/previews'):register}
BACKGROUND = []
