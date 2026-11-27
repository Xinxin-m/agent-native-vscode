import http.client
import json
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import unittest

import file_service
import server


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = file_service.FileService(self.root / 'data',default_root=None)
        self.rid = self.files.register('mac',str(self.root),'write')['id']
        (self.root / 'sample.pdf').write_bytes(b'%PDF-123456789')
        (self.root / 'page.html').write_text('<script>alert(1)</script>')
        (self.root / 'worker.mjs').write_text('export const ok=true')
        (self.root / 'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect width="10" height="10"/></svg>')
        self.addCleanup(patch.stopall)
        patch.object(file_service,'_SERVICE',self.files).start()
        patch.object(server,'WEB',str(self.root)).start()
        patch.dict(server.PLUGIN_ROUTES,file_service.ROUTES).start()
        self.http = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def request(self,path,method='GET',headers=None,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.http.server_port)
        self.addCleanup(conn.close)
        conn.request(method,path,headers=headers or {},body=body)
        response=conn.getresponse()
        return response.status,dict(response.getheaders()),response.read()

    def test_head_ranges_and_executable_content_is_plain_source(self):
        url='/api/files/content?root_id='+self.rid+'&path=sample.pdf'
        status,headers,body=self.request(url,headers={'Range':'bytes=0-3'})
        self.assertEqual((status,body),(206,b'%PDF'))
        self.assertEqual(headers['Content-Range'],'bytes 0-3/14')
        status,headers,body=self.request(url,'HEAD')
        self.assertEqual((status,body),(200,b''))
        self.assertEqual(headers['Content-Type'],'application/pdf')
        status,headers,body=self.request('/api/files/content?root_id='+self.rid+'&path=page.html')
        self.assertTrue(headers['Content-Type'].startswith('text/plain'))
        self.assertIn('sandbox',headers['Content-Security-Policy'])

    def test_observe_external_edit_and_rewind_over_http(self):
        target = {'root_id':self.rid, 'path':'page.html'}
        url = '/api/files/stat?root_id='+self.rid+'&path=page.html'
        before = json.loads(self.request(url)[2])['resource']
        (self.root / 'page.html').write_text('<h1>Next</h1>')
        after = json.loads(self.request(url)[2])['resource']
        self.assertTrue(after['has_previous'])
        for version, expected in [(before['version'], 409), (after['version'], 200)]:
            status, _, _ = self.request('/api/files/document-rewind', 'POST',
                                        {'Content-Type':'application/json'},
                                        json.dumps({**target, 'base_version':version}))
            self.assertEqual(status, expected)
        self.assertEqual((self.root / 'page.html').read_text(), '<script>alert(1)</script>')

    def test_module_mime_and_cross_origin_guard(self):
        status,headers,body=self.request('/static/worker.mjs')
        self.assertIn(headers['Content-Type'],('text/javascript','application/javascript'))
        status,_,_=self.request('/api/files/roots',headers={'Origin':'http://evil.test'})
        self.assertEqual(status,403)

    def test_svg_is_renderable_image_with_script_blocking_sandbox(self):
        status,headers,body=self.request('/api/files/content?root_id='+self.rid+'&path=diagram.svg')
        self.assertEqual((status,headers['Content-Type']),(200,'image/svg+xml'))
        self.assertIn('sandbox',headers['Content-Security-Policy'])
        self.assertNotIn('allow-scripts',headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
