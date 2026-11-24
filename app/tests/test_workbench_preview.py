import json
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import unittest

import preview_server as ps


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'index.html').write_text('<script type="module" src="./app.js"></script>')
        (self.root / 'app.js').write_text('fetch("./data.json")')
        (self.root / 'data.json').write_text('{"ok":true}')
        self.server = ps.PreviewServer()
        self.addCleanup(self.server.close)

    def test_distinct_origin_relative_assets_and_security_headers(self):
        item = self.server.register('mac', str(self.root), 'index.html', 'http://127.0.0.1:8744')
        with urllib.request.urlopen(item['url']) as response:
            self.assertIn(b'app.js', response.read())
            self.assertIn("form-action 'none'", response.headers['Content-Security-Policy'])
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        with urllib.request.urlopen(item['url'].rsplit('/',1)[0] + '/data.json') as response:
            self.assertEqual(json.load(response), {'ok':True})
        self.assertNotEqual(self.server.http.server_port, 8744)

    def test_escape_expiry_api_and_foreign_host_fail(self):
        item = self.server.register('mac', str(self.root), 'index.html', 'http://127.0.0.1:8744')
        base = item['url'].rsplit('/',1)[0]
        (self.root / 'escape').symlink_to(self.root.parent)
        for suffix in ('/%2e%2e/secret', '/escape/secret', '/api/runs'):
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + suffix)
        request = urllib.request.Request(item['url'], headers={'Host':'evil.test'})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(request)
        self.server.tokens[item['preview_id']]['expires_at'] = 0
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(item['url'])
        self.assertEqual(caught.exception.code, 410)

    def test_https_web_assets_require_explicit_scope_choice(self):
        item=self.server.register('mac',str(self.root),'index.html','http://127.0.0.1:8744',allow_network=True)
        with urllib.request.urlopen(item['url']) as response:
            policy=response.headers['Content-Security-Policy']
        self.assertIn("connect-src 'self' https:",policy)
        self.assertIn("form-action 'none'",policy)
        self.assertTrue(item['allow_network'])

    def test_root_relative_nested_modules_data_and_scope_origins(self):
        (self.root/'nested').mkdir()
        (self.root/'nested'/'page.html').write_text('<link rel="stylesheet" href="/style.css"><script type="module" src="/app.js"></script>')
        (self.root/'style.css').write_text('body { color: red }')
        (self.root/'app.js').write_text('import {value} from "/nested/module.js"; fetch("/data.json")')
        (self.root/'nested'/'module.js').write_text('export const value=7;')
        first=self.server.register('mac',str(self.root),'nested/page.html','http://127.0.0.1:8744')
        origin=first['url'].split('/p/',1)[0]
        for path,expected in (('/style.css',b'color: red'),('/app.js',b'fetch("/data.json")'),('/nested/module.js',b'value=7'),('/data.json',b'"ok":true')):
            request=urllib.request.Request(origin+path,headers={'Sec-Fetch-Site':'same-origin'})
            with self.subTest(path=path),urllib.request.urlopen(request) as response:
                self.assertIn(expected,response.read())
                self.assertEqual(response.headers['Cross-Origin-Resource-Policy'],'same-origin')
        second_root=self.root/'second'
        second_root.mkdir()
        (second_root/'index.html').write_text('second scope')
        second=self.server.register('mac',str(second_root),'index.html','http://127.0.0.1:8744')
        second_origin=second['url'].split('/p/',1)[0]
        self.assertNotEqual(origin,second_origin)
        for url,headers in ((origin+'/p/'+second['preview_id']+'/index.html',{}),(origin+'/data.json',{'Origin':second_origin,'Sec-Fetch-Site':'same-site'}),(second_origin+'/style.css',{})):
            with self.subTest(url=url),self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(url,headers=headers))

    def test_root_relative_head_expiry_and_encoded_escape(self):
        item=self.server.register('mac',str(self.root),'index.html','http://127.0.0.1:8744')
        origin=item['url'].split('/p/',1)[0]
        with urllib.request.urlopen(urllib.request.Request(origin+'/data.json',method='HEAD')) as response:
            self.assertEqual(response.read(),b'')
            self.assertEqual(response.headers['Content-Type'],'application/json')
        for path in ('/%2e%2e/secret','/api/runs'):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(origin+path)
        self.server.tokens[item['preview_id']]['expires_at']=0
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(origin+'/data.json')
        self.assertEqual(caught.exception.code,410)
    def fetch(self, url, method='GET'):
        with urllib.request.urlopen(urllib.request.Request(url, method=method)) as response:
            return response.read(), response.headers

    def test_html_gets_pinch_script_appended_and_other_types_are_untouched(self):
        original = (self.root / 'index.html').read_bytes()
        png = bytes(range(256)) * 3
        (self.root / 'pic.png').write_bytes(png)
        item = self.server.register('mac', str(self.root), 'index.html', 'http://127.0.0.1:8744')
        base = item['url'].rsplit('/', 1)[0]
        body, headers = self.fetch(item['url'])
        self.assertTrue(body.startswith(original))
        self.assertTrue(body.endswith(b'</script>'))
        self.assertIn(b'gesturechange', body[len(original):])
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertEqual((self.root / 'index.html').read_bytes(), original)
        self.assertEqual(self.fetch(base + '/app.js')[0], b'fetch("./data.json")')
        self.assertEqual(self.fetch(base + '/pic.png')[0], png)

    def test_head_length_equals_get_length(self):
        item = self.server.register('mac', str(self.root), 'index.html', 'http://127.0.0.1:8744')
        base = item['url'].rsplit('/', 1)[0]
        for url in (item['url'], base + '/app.js'):
            with self.subTest(url=url):
                body, get_headers = self.fetch(url)
                head_body, head_headers = self.fetch(url, 'HEAD')
                self.assertEqual(head_body, b'')
                self.assertEqual(head_headers['Content-Length'], get_headers['Content-Length'])
                self.assertEqual(int(get_headers['Content-Length']), len(body))

    def test_xhtml_script_goes_inside_the_root_element_in_the_xhtml_namespace(self):
        (self.root / 'page.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><body>x</body></html>')
        item = self.server.register('mac', str(self.root), 'page.xhtml', 'http://127.0.0.1:8744')
        body, headers = self.fetch(item['url'])
        self.assertEqual(headers['Content-Type'], 'application/xhtml+xml')
        self.assertTrue(body.endswith(b'</script></html>'))
        self.assertIn(b'<script xmlns="http://www.w3.org/1999/xhtml">', body)
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertEqual(self.fetch(item['url'], 'HEAD')[1]['Content-Length'], headers['Content-Length'])
