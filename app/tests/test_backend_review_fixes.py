"""Red-team fixes of 2026-11-15: journal line splitting, state build errors, manual titles."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker
import live
import server
import session_lineage
import project_review
from test_latency_caches import chat, write

ODD = 'a b c\u0085d\re\nf'


class JournalSplitTests(unittest.TestCase):
    def test_unicode_line_separators_inside_event_text_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = broker.RunStore(tmp)
            run = store.create({'provider': 'claude'})
            store.emit(run['id'], 'delta', {'text': ODD})
            store.emit(run['id'], 'approval', {'request_id': '7', 'text': ODD})
            raw = (Path(tmp) / 'runs' / run['id'] / 'events.jsonl').read_bytes()
            self.assertIn(' '.encode(), raw)  # written raw, so a str.splitlines reader would break it
            texts = [e['data'].get('text') for e in store.events(run['id'])]
            self.assertEqual(texts[-2:], [ODD, ODD])
            self.assertEqual([e['seq'] for e in broker.RunStore(tmp).events(run['id'], after=1)], [2, 3])
            controller = broker.Broker(store)
            self.assertIn('7', controller._pending_evidence(run['id']))

    def test_transcript_tail_keeps_records_with_unicode_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 't.jsonl'
            path.write_text(json.dumps({'type': 'user', 'text': ODD}, ensure_ascii=False) + '\n', encoding='utf-8')
            lines = [l for l in live._tail_lines(str(path)) if l]
            self.assertEqual([json.loads(l)['text'] for l in lines], [ODD])


class ServerFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        files = {name: str(self.dir / (name + '.json')) for name in server.FILES}
        write(files['index'], {'claude-a': chat('claude-a')})
        write(files['projects'], {'projects': [], 'assignments': {}})
        write(files['config'], {'port': 1})
        for patcher in (patch.object(server, 'DATA', str(self.dir)), patch.dict(server.FILES, files), patch.object(server, 'CODEX_MODELS_CACHE', str(self.dir / 'none')),
                        patch.object(server, '_STATE', {}), patch.dict(server._STATE_ERROR, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.files = files
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_port

    def call(self, method, path, body=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        client.request(method, path, json.dumps(body) if body is not None else None,
                       {'Content-Type': 'application/json'} if body is not None else {})
        response = client.getresponse()
        result = json.loads(response.read())
        client.close()
        return response.status, result


class StateErrorTests(ServerFixture):
    def test_failing_build_is_reported_throttled_and_logged_once(self):
        calls = []

        def broken():
            calls.append(1)
            raise ValueError('index is corrupt')
        out = io.StringIO()
        with patch.object(server, 'build_state', side_effect=broken), contextlib.redirect_stdout(out):
            status, body = self.call('GET', '/api/state')
            self.assertEqual(status, 500)
            self.assertIn('index is corrupt', body['error'])
            for _ in range(5):
                status, body = self.call('GET', '/api/revision')
                self.assertEqual(status, 200)
                self.assertIn('index is corrupt', body['error'])
            self.assertEqual(len(calls), 1, 'a failing build must not restart on every poll')
            server._STATE_ERROR['at'] = time.time() - server.STATE_RETRY_S - 1
            self.call('GET', '/api/state')
            self.assertEqual(len(calls), 2)
        self.assertEqual(out.getvalue().count('index is corrupt'), 1, 'log once per distinct error')
        status, body = self.call('GET', '/api/state')  # throttle window still open after the retry
        self.assertEqual(status, 500)
        server._STATE_ERROR['at'] = 0
        status, body = self.call('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(self.call('GET', '/api/revision')[1], {'notice_center': {'revision': '0', 'unread': 0}, 'revision': body['revision'], 'runs': ''})

    def test_error_after_a_good_build_keeps_old_revision_and_reports(self):
        good = self.call('GET', '/api/revision')[1]['revision']
        write(self.files['index'], {'claude-b': chat('claude-b')})
        with patch.object(server, 'build_state', side_effect=RuntimeError('boom')), contextlib.redirect_stdout(io.StringIO()):
            self.call('GET', '/api/revision')
            for _ in range(100):
                if server._STATE_ERROR:
                    break
                time.sleep(.02)
            self.assertEqual(self.call('GET', '/api/revision')[1], {'notice_center': {'revision': '0', 'unread': 0}, 'revision': good, 'runs': '', 'error': 'RuntimeError: boom'})


class ManualTitleTests(ServerFixture):
    def test_reset_title_restores_generated_title(self):
        write(self.files['enrich'], {'claude-a': {'title': 'Generated'}})
        self.assertEqual(self.call('POST', '/api/chat_state', {'action': 'rename', 'key': 'claude-a', 'title': 'Mine'})[0], 200)
        self.assertEqual(json.loads(server.state_body()['body'])['chats'][0]['title'], 'Mine')
        self.assertEqual(self.call('POST', '/api/chat_state', {'action': 'reset_title', 'key': 'claude-a'}),
                         (200, {'ok': True, 'title': 'Generated'}))
        self.assertEqual(json.load(open(self.files['flags']))['titles'], {})
        self.assertEqual(json.loads(server.state_body()['body'])['chats'][0]['title'], 'Generated')
        self.assertEqual(self.call('POST', '/api/chat_state', {'action': 'reset_title', 'key': 'nope'})[0], 404)

    def test_session_tree_and_review_prefer_manual_title(self):
        data = self.dir / 'data'
        data.mkdir()
        write(data / 'index.json', {'claude-a': chat('claude-a')})
        write(data / 'enrich.json', {'claude-a': {'title': 'Generated'}})
        write(data / 'chat_flags.json', {'titles': {'claude-a': 'Mine'}})
        write(data / 'projects.json', {'assignments': {}})
        with patch.object(session_lineage, 'DATA', str(data)):
            nodes = session_lineage.session_tree(None, {'key': 'claude-a'}, None)['nodes']
        self.assertEqual(nodes[0]['title'], 'Mine')
        review = project_review.audit({'claude-a': chat('claude-a')}, {'claude-a': {'title': 'Generated'}},
                                      {'projects': [], 'assignments': {}}, {}, flags={'titles': {'claude-a': 'Mine'}})
        self.assertEqual({row['title'] for row in review['items']}, {'Mine'})


if __name__ == '__main__':
    unittest.main()
