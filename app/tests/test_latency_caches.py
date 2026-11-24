import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import broker
import server


def write(path, value):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w') as handle:
        json.dump(value, handle)
    os.replace(tmp, path)


def chat(key, ts='2026-11-15T10:00:00Z'):
    return {'key': key, 'source': 'claude', 'id': key, 'file': '/f/' + key, 'cwd': '/w',
            'title_seed': key, 'ts_first': ts, 'ts_last': ts, 'n_user': 1, 'n_asst': 1,
            'n_files': 1, 'first_prompt': 'hello', 'src_sig': [['/f/' + key, 1, 1]]}


class StateCacheTests(unittest.TestCase):
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
        self.builds = 0
        real = server.build_state

        def counted():
            self.builds += 1
            return real()
        patcher = patch.object(server, 'build_state', side_effect=counted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hit_reuses_bytes_and_body_matches_uncached_payload(self):
        first = server.state_body()
        second = server.state_body()
        self.assertEqual(self.builds, 1)
        self.assertIs(first['body'], second['body'])
        decoded = json.loads(first['body'])
        self.assertEqual(list(decoded)[-1], 'revision')
        self.assertEqual(decoded, {**server.build_state(), 'revision': first['etag']})
        self.assertEqual(server.data_revision(), first['etag'])

    def test_revision_does_not_wait_for_a_rebuild(self):
        etag = server.data_revision()
        write(self.files['index'], {})
        with server.STATE_LOCK:  # a rebuild is in progress elsewhere
            self.assertEqual(server.data_revision(), etag)
        self.assertEqual(server.data_revision(), etag)  # starts the background rebuild
        for _ in range(100):
            if server.data_revision() != etag:
                break
            threading.Event().wait(.02)
        self.assertEqual(server.data_revision(), server.state_body()['etag'])
        self.assertNotEqual(server.data_revision(), etag)

    def test_changed_file_rebuilds_but_identical_rewrite_keeps_revision(self):
        before = server.state_body()['etag']
        os.utime(self.files['projects'], ns=(1, 1))  # rewritten, same content
        self.assertEqual(server.state_body()['etag'], before)
        self.assertEqual(self.builds, 2)
        write(self.files['index'], {'claude-a': chat('claude-a', '2026-11-15T11:00:00Z')})
        self.assertNotEqual(server.state_body()['etag'], before)

    def test_http_304_gzip_and_plain_clients(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def get(**headers):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
            client.request('GET', '/api/state', headers=headers)
            response = client.getresponse()
            body = response.read()
            client.close()
            return response, body
        plain, body = get()
        self.assertEqual(plain.status, 200)
        self.assertIsNone(plain.getheader('Content-Encoding'))
        self.assertEqual(json.loads(body)['revision'], plain.getheader('ETag').strip('"'))
        zipped, packed = get(**{'Accept-Encoding': 'gzip, deflate'})
        self.assertEqual(zipped.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(packed), body)
        same, empty = get(**{'If-None-Match': plain.getheader('ETag')})
        self.assertEqual((same.status, empty), (304, b''))
        write(self.files['index'], {})
        changed, fresh = get(**{'If-None-Match': plain.getheader('ETag')})
        self.assertEqual(changed.status, 200)
        self.assertEqual(json.loads(fresh)['chats'], [])

    def test_concurrent_readers_during_rewrites_get_consistent_bodies(self):
        bodies, errors = [], []

        def read():
            try:
                for _ in range(20):
                    state = server.state_body()
                    bodies.append(state)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=read) for _ in range(8)]
        for thread in threads:
            thread.start()
        for n in range(10):
            write(self.files['index'], {'claude-a': chat('claude-a', f'2026-11-15T10:00:{n:02d}Z')})
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        for state in bodies:
            payload = state['body'][:state['body'].rindex(b', "revision"')] + b'}'
            self.assertEqual(hashlib.sha256(payload).hexdigest()[:24], state['etag'])
            self.assertEqual(json.loads(state['body'])['revision'], state['etag'])
        self.assertLessEqual(self.builds, 12)


class RunListCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = broker.RunStore(tmp.name)
        self.run = self.store.create({'provider': 'codex', 'host_id': 'mac', 'key': 'codex-a'})

    def test_unchanged_runs_are_not_reread_and_records_are_private(self):
        self.store.list()
        with patch.object(Path, 'read_text', side_effect=AssertionError('reread')):
            listed = self.store.list()
        listed[0]['status'] = 'mutated'
        self.assertEqual(self.store.list()[0]['status'], 'queued')

    def test_updates_are_cached_and_external_removal_is_seen_only_on_restart(self):
        self.store.list()
        self.store.update(self.run['id'], status='running')
        self.assertEqual(self.store.list()[0]['status'], 'running')
        os.remove(self.store._dir(self.run['id']) / 'run.json')
        self.assertEqual(self.store.list()[0]['status'], 'running')
        self.assertEqual(broker.RunStore(self.store.root).list(), [])

    def test_concurrent_list_and_update(self):
        errors = []

        def read():
            try:
                for _ in range(50):
                    self.assertEqual(len(self.store.list()), 1)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=read) for _ in range(6)]
        for thread in threads:
            thread.start()
        for n in range(30):
            self.store.update(self.run['id'], text=str(n))
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(self.store.list()[0]['text'], '29')


class AttentionCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.obj = broker.Broker(broker.RunStore(tmp.name))
        self.run = self.obj.store.create({'provider': 'codex', 'host_id': 'mac', 'key': 'codex-a', 'session_id': 's'})
        self.rid = self.run['id']
        self.obj.store.update(self.rid, status='running')
        self.ctrl = {'protocol': 'app-server', 'pending_requests': {'7': {'kind': 'approval', 'native_id': 7}}}
        self.obj.active[self.rid] = self.ctrl
        self.journal = self.obj.store._dir(self.rid) / 'events.jsonl'

    def items(self):
        return [(i['request_id'], i['payload'].get('command')) for i in self.obj.attention()['items']]

    def test_reads_only_appended_bytes_and_tracks_resolution(self):
        self.obj.store.emit(self.rid, 'approval', {'request_id': '7', 'command': 'ls'})
        self.assertEqual(self.items(), [('7', 'ls')])
        offset = self.obj.evidence[self.rid][1]
        self.assertEqual(offset, self.journal.stat().st_size)
        for _ in range(5):
            self.obj.store.emit(self.rid, 'delta', {'text': 'x'})
        with patch.object(self.obj.store, 'events', side_effect=AssertionError('full journal read')):
            self.assertEqual(self.items(), [('7', 'ls')])
        self.obj.store.emit(self.rid, 'request_resolved', {'request_id': '7'})
        self.assertEqual(self.items(), [])

    def test_partial_record_waits_and_replaced_journal_resets(self):
        self.obj.store.emit(self.rid, 'approval', {'request_id': '7', 'command': 'ls'})
        line = json.dumps({'seq': 99, 'event': 'request_resolved', 'data': {'request_id': '7'}})
        with self.journal.open('a') as handle:
            handle.write(line[:10])
        self.assertEqual(self.items(), [('7', 'ls')])
        with self.journal.open('a') as handle:
            handle.write(line[10:] + '\n')
        self.assertEqual(self.items(), [])
        replacement = str(self.journal) + '.new'
        Path(replacement).write_text(json.dumps({'seq': 1, 'event': 'approval', 'ts': 't', 'data': {'request_id': '7', 'command': 'pwd'}}) + '\n')
        os.replace(replacement, self.journal)
        self.assertEqual(self.items(), [('7', 'pwd')])

    def test_returned_payload_is_not_shared_and_finished_runs_are_pruned(self):
        self.obj.store.emit(self.rid, 'approval', {'request_id': '7', 'command': 'ls'})
        self.obj.attention()['items'][0]['payload']['command'] = 'mutated'
        self.assertEqual(self.items(), [('7', 'ls')])
        del self.obj.active[self.rid]
        self.obj.attention()
        self.assertEqual(self.obj.evidence, {})

    def test_concurrent_attention_during_appends(self):
        self.obj.store.emit(self.rid, 'approval', {'request_id': '7', 'command': 'ls'})
        errors = []

        def poll():
            try:
                for _ in range(30):
                    self.assertEqual(self.items(), [('7', 'ls')])
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=poll) for _ in range(4)]
        for thread in threads:
            thread.start()
        for _ in range(40):
            self.obj.store.emit(self.rid, 'delta', {'text': 'y'})
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
