import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helpers under either runner
import server
from test_latency_caches import chat, write


class RenameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        files = {name: str(self.dir / (name + '.json')) for name in server.FILES}
        write(files['index'], {'claude-a': chat('claude-a')})
        write(files['projects'], {'projects': [], 'assignments': {}})
        write(files['config'], {'port': 1})
        for patcher in (patch.dict(server.FILES, files), patch.object(server, 'CODEX_MODELS_CACHE', str(self.dir / 'none')),
                        patch.object(server, '_STATE', {})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.files = files
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_port

    def post(self, body):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        client.request('POST', '/api/chat_state', json.dumps(body), {'Content-Type': 'application/json'})
        response = client.getresponse()
        result = json.loads(response.read())
        client.close()
        return response.status, result

    def title(self):
        return json.loads(server.state_body()['body'])['chats'][0]['title']

    def test_rename_persists_and_changes_state_and_revision(self):
        before = server.state_body()['etag']
        self.assertEqual(self.title(), 'claude-a')
        self.assertEqual(self.post({'action': 'rename', 'key': 'claude-a', 'title': '  New name '}), (200, {'ok': True, 'title': 'New name'}))
        self.assertEqual(json.load(open(self.files['flags']))['titles'], {'claude-a': 'New name'})
        self.assertEqual(self.title(), 'New name')
        self.assertNotEqual(server.state_body()['etag'], before)
        self.assertFalse(os.path.exists(self.files['flags'] + '.tmp'))

    def test_rejects_bad_titles_and_unknown_key(self):
        for title in ('', '   ', 'x' * 201, 'a\nb', None, 5):
            self.assertEqual(self.post({'action': 'rename', 'key': 'claude-a', 'title': title})[0], 400)
        self.assertEqual(self.post({'action': 'rename', 'key': 'nope', 'title': 'ok'})[0], 404)
        self.assertFalse(os.path.exists(self.files['flags']))
        self.assertEqual(self.title(), 'claude-a')

    def test_relabel_and_archive_keep_manual_title(self):
        self.post({'action': 'rename', 'key': 'claude-a', 'title': 'Mine'})
        write(self.files['enrich'], {'claude-a': {'title': 'Auto label'}})
        self.assertEqual(self.title(), 'Mine')
        self.assertEqual(self.post({'action': 'archive', 'key': 'claude-a'})[0], 200)
        self.assertEqual(json.load(open(self.files['flags']))['titles'], {'claude-a': 'Mine'})
        self.assertEqual(self.title(), 'Mine')


if __name__ == '__main__':
    unittest.main()
