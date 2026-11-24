import json
import os
import tempfile
import unittest

import json_cache
import sidebar

T0 = 1795400000  # a fixed "first load after deploy"


def chat(key, result):
    return {'key': key, 'last_assistant_ts': result}


class SeenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        index = {'claude-a': {}, 'claude-b': {}}
        with open(os.path.join(self.data, 'index.json'), 'w') as handle:
            json.dump(index, handle)
        json_cache._FILES.clear()

    def tearDown(self):
        self.tmp.cleanup()
        json_cache._FILES.clear()

    def test_first_load_marks_every_existing_result_seen(self):
        # The symptom: with no stored view, the old Library showed every chat with a result as new.
        old = chat('claude-a', '2026-11-01T10:00:00.000Z')
        views = {}
        self.assertTrue(old['last_assistant_ts'] and not views.get(old['key']))  # old rule: unread
        rows = sidebar.annotate([old], self.data, now=T0)
        self.assertFalse(rows[0]['unseen'])
        self.assertTrue(os.path.exists(os.path.join(self.data, 'seen.json')))

    def test_result_after_first_load_is_unseen_until_reported_visible_and_focused(self):
        sidebar.load_seen(self.data, T0)
        later = sidebar._iso(T0 + 600)
        row = sidebar.annotate([chat('claude-a', later)], self.data, now=T0 + 700)[0]
        self.assertTrue(row['unseen'])
        self.assertEqual(sidebar.mark_seen(['claude-a'], True, False, self.data, T0 + 800)['marked'], 0)
        self.assertEqual(sidebar.mark_seen(['claude-a'], False, True, self.data, T0 + 800)['marked'], 0)
        self.assertTrue(sidebar.annotate([chat('claude-a', later)], self.data, now=T0 + 900)[0]['unseen'])
        self.assertEqual(sidebar.mark_seen(['claude-a'], True, True, self.data, T0 + 800)['marked'], 1)
        self.assertFalse(sidebar.annotate([chat('claude-a', later)], self.data, now=T0 + 900)[0]['unseen'])
        newer = sidebar._iso(T0 + 1000)  # a reply arriving while the chat is open is unseen again
        self.assertTrue(sidebar.annotate([chat('claude-a', newer)], self.data, now=T0 + 1100)[0]['unseen'])

    def test_unknown_or_malformed_keys_are_ignored_and_time_never_moves_back(self):
        sidebar.load_seen(self.data, T0)
        self.assertEqual(sidebar.mark_seen(['../x', 'claude-zzz', 5], True, True, self.data, T0 + 5)['marked'], 0)
        sidebar.mark_seen(['claude-a'], True, True, self.data, T0 + 500)
        sidebar.mark_seen(['claude-a'], True, True, self.data, T0 + 100)
        seen = sidebar.load_seen(self.data)
        self.assertEqual(seen['seen']['claude-a'], sidebar._iso(T0 + 500))

    def test_legacy_view_time_counts_and_fields_are_added(self):
        sidebar.load_seen(self.data, T0)
        later = sidebar._iso(T0 + 600)
        views = {'claude-b': {'views': 1, 'last': sidebar._iso(T0 + 650)}}
        row = sidebar.annotate([chat('claude-b', later)], self.data, {'claude-b': 'p1'}, views, {'claude-b': 'working'}, T0 + 700)[0]
        self.assertFalse(row['unseen'])
        self.assertEqual((row['live_status'], row['project_id']), ('working', 'p1'))

    def test_corrupt_file_is_kept_not_overwritten(self):
        path = os.path.join(self.data, 'seen.json')
        open(path, 'w').write('{broken')
        self.assertEqual(sidebar.load_seen(self.data, T0)['seen'], {})
        self.assertEqual(open(path).read(), '{broken')

    def test_live_statuses_priority(self):
        import time
        now = time.time()
        sessions = [{'chat_key': 'k1', 'alive': True, 'status': 'idle', 'session_id': 's', 'observed_at': now - 1},
                    {'chat_key': 'k2', 'alive': True, 'status': 'busy', 'session_id': 's', 'observed_at': now - 1},
                    {'chat_key': 'k3', 'alive': True, 'status': 'idle', 'session_id': 's', 'observed_at': now - 500}]
        out = sidebar.live_statuses(sessions, {'k1': 'working', 'k4': 'working'}, ['k2'], now)
        self.assertEqual(out, {'k1': 'working', 'k2': 'waiting', 'k4': 'working'})


if __name__ == '__main__':
    unittest.main()
