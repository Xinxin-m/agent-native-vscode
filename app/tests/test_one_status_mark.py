"""One status mark per chat (user request 2026-11-23): failed > waiting > working > new; latest event wins."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import sidebar


def run(root, name, key, status, created, finished=None):
    p = Path(root) / 'execution' / 'runs' / name
    p.mkdir(parents=True)
    (p / 'run.json').write_text(json.dumps({'status': status, 'key': key, 'conversation_key': key,
                                            'created_at': created, **({'finished_at': finished} if finished else {})}))


class OneStatusMarkTests(unittest.TestCase):
    def test_precedence(self):
        t = sidebar._parse('2026-11-23T07:03:38Z')
        self.assertEqual(sidebar.resolve('working', t, '2026-11-23T07:03:38Z', True), (None, True, 'failed'))
        self.assertEqual(sidebar.resolve('waiting', t, '2026-11-23T07:03:38Z', False), (None, True, 'failed'))
        self.assertEqual(sidebar.resolve('waiting', None, '2026-11-23T07:03:38Z', True)[2], 'waiting')
        self.assertEqual(sidebar.resolve('working', None, '2026-11-23T07:03:38Z', True)[2], 'working')
        self.assertEqual(sidebar.resolve('idle', None, '2026-11-23T07:03:38Z', True)[2], 'new')
        self.assertEqual(sidebar.resolve('idle', None, '2026-11-23T07:03:38Z', False)[2], None)
        self.assertEqual(sidebar.resolve(None, t, '2026-11-23T07:03:38Z', True), (None, True, 'failed'))

    def test_running_then_failed_shows_failed_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run(tmp, 'a1', 'claude-a', 'completed', '2026-11-23T06:00:00Z', '2026-11-23T06:10:00Z')
            run(tmp, 'a2', 'claude-a', 'failed', '2026-11-23T07:03:35Z', '2026-11-23T07:03:38Z')
            sidebar._RUNS.clear()
            # The process inventory still says busy (a stale flag): the failure is the latest event.
            row = sidebar.annotate([{'key': 'claude-a', 'ts_last': '2026-11-23T07:03:38Z', 'last_assistant_ts': '2026-11-23T07:03:38Z'}],
                                   tmp, statuses={'claude-a': 'working'}, now=time.time())[0]
            self.assertEqual((row['status_mark'], row['failed'], row['live_status']), ('failed', True, None))

    def test_later_activity_or_newer_run_supersedes_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run(tmp, 'b1', 'claude-b', 'failed', '2026-11-23T07:03:35Z', '2026-11-23T07:03:38Z')
            run(tmp, 'c1', 'claude-c', 'failed', '2026-11-23T02:34:24Z', '2026-11-23T02:34:25Z')
            run(tmp, 'c2', 'claude-c', 'running', '2026-11-23T20:16:32Z')
            sidebar._RUNS.clear()
            rows = [{'key': 'claude-b', 'ts_last': '2026-11-23T20:28:59Z'}, {'key': 'claude-c', 'ts_last': '2026-11-23T20:20:56Z'}]
            out = sidebar.annotate(rows, tmp, statuses={'claude-b': 'working', 'claude-c': 'working'}, now=time.time())
            self.assertEqual([(r['status_mark'], r['failed']) for r in out], [('working', False), ('working', False)])


class LiveStateCheck(unittest.TestCase):
    """Every chat in the live /api/state has at most one mark, and the mark follows the precedence."""
    def test_live_state(self):
        import urllib.request
        try:
            with urllib.request.urlopen('http://127.0.0.1:8744/api/state', timeout=60) as r:
                chats = json.load(r)['chats']
        except OSError:
            self.skipTest('Library server not running')
        if not chats or 'status_mark' not in chats[0]:
            self.skipTest('running server predates status_mark')
        for c in chats:
            want = 'failed' if c['failed'] else c['live_status'] if c['live_status'] in ('waiting', 'working') else 'new' if c['unseen'] else None
            self.assertEqual(c['status_mark'], want, c['key'])
            self.assertFalse(c['failed'] and c['live_status'] in ('working', 'waiting'), c['key'])


if __name__ == '__main__':
    unittest.main()
