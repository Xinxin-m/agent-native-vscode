"""Round 3 backend (2026-11-23): search by session ID, failed mark on chat rows, Debug on feed items.
No agent is started and no provider is called: debug_agent.start_agent is a fake."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import debug_agent
import notifications as n
import search_api
import sidebar

SID = 'abcdef12-3456-4abc-8def-012345678901'


class SearchBySessionIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        index = {'claude-' + SID: {'id': SID, 'source': 'claude', 'ts_last': '2026-11-22T10:00:00Z', 'title_seed': 'Target chat'},
                 'claude-' + SID + '-agent-a1': {'id': SID + '-agent-a1', 'source': 'claude', 'is_child': True,
                                                 'parent_key': 'claude-' + SID, 'ts_last': '2026-11-22T11:00:00Z'},
                 'codex-other': {'id': 'ffff0000-0000-4000-8000-000000000001', 'source': 'codex', 'ts_last': '2026-11-22T09:00:00Z'}}
        (d / 'index.json').write_text(json.dumps(index))
        (d / 'chats').mkdir()
        (d / 'chats' / 'codex-other.json').write_text(json.dumps({'turns': [{'role': 'user', 'text': 'mentions abcdef in text'}]}))
        self.d = d

    def keys(self, q):
        return [(r['key'], r.get('match')) for r in search_api.search(self.d, {'q': q})['results']]

    def test_full_id_prefix_and_key_prefix_find_the_chat_first(self):
        for q in (SID, SID.upper(), 'abcdef', 'claude-abcdef12-3456', 'claude-' + SID):
            self.assertEqual(self.keys(q)[0], ('claude-' + SID, 'session_id'), q)

    def test_subagents_are_not_prefix_hits_and_text_hits_follow(self):
        found = self.keys('abcdef')
        self.assertEqual([k for k, _ in found], ['claude-' + SID, 'codex-other'])
        self.assertEqual(found[1][1], None)

    def test_short_or_non_hex_query_is_plain_text(self):
        self.assertEqual(self.keys('abcde'), [('codex-other', None)])  # text hit only, no id hit
        self.assertEqual(search_api.session_id_query('hello world'), None)


class FailedMarkTests(unittest.TestCase):
    def test_latest_failed_run_marks_the_chat_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(name, key, status, at):
                p = Path(tmp) / 'execution' / 'runs' / name
                p.mkdir(parents=True)
                (p / 'run.json').write_text(json.dumps({'status': status, 'key': key, 'conversation_key': key, 'created_at': at}))
            run('1', 'claude-a', 'failed', '2026-11-22T10:00:00Z')
            run('2', 'claude-b', 'failed', '2026-11-22T10:00:00Z')
            run('3', 'claude-b', 'completed', '2026-11-22T11:00:00Z')
            run('4', 'claude-c', 'failed', '2026-11-22T10:00:00Z')
            sidebar._RUNS.clear()
            self.assertEqual(sidebar.failed_keys(tmp), {'claude-a', 'claude-c'})
            rows = [{'key': k, 'ts_last': '2026-11-22T10:00:00Z', 'source': 'claude'} for k in ('claude-a', 'claude-b', 'claude-c')]
            out = sidebar.annotate(rows, tmp, statuses={'claude-c': 'working'}, now=time.time())
            # One mark per chat (2026-11-23): a standing failure beats and clears working.
            self.assertEqual([r['failed'] for r in out], [True, False, True])
            self.assertEqual([r['status_mark'] for r in out], ['failed', None, 'failed'])
            self.assertIsNone(out[2]['live_status'])


class DebugTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)
        self.store = str(self.tmpdir / 'notices.json')
        self.now = time.time()
        self.starts, self.logins, self.runs = [], [], {}
        self.rows = [{'id': 'c1', 'tool': 'claude', 'label': 'Main', 'email': 'a@x.test', 'logged_in': False, 'previously_used': True}]
        self.attention = []

        def fake_start(brief, model, path, request_id):
            self.starts.append((brief, model, path, request_id))
            return {'run_id': 'dbg1', 'agent_key': None}
        models = [{'id': '', 'name': 'Session default'}, {'id': 'claude-opus-9-9', 'name': 'Opus'}, {'id': 'claude-sonnet-4-0', 'name': 'S'},
                  {'id': 'claude-haiku-4-0[1m]', 'name': 'H long'}, {'id': 'claude-haiku-4-0', 'name': 'H'}]
        for p in (patch.object(n, '_path', lambda: self.store), patch.object(n, '_data', lambda: str(self.tmpdir)),
                  patch.object(n, '_config', lambda: {'monitor_dir': str(self.tmpdir / 'nomon')}),
                  patch.object(n, '_account_rows', lambda: self.rows), patch.object(n, '_attention', lambda: self.attention),
                  patch.object(n, '_running', lambda key: False), patch.object(n, '_chat_title', lambda key: key),
                  patch.object(n, '_live_rows', lambda: []), patch.object(n, '_failed_run', lambda rid: None),
                  patch.object(debug_agent, 'start_agent', fake_start), patch.object(debug_agent, '_models', lambda: models),
                  patch.object(debug_agent, '_login', lambda aid: self.logins.append(aid) or {'ok': True}),
                  patch.object(debug_agent, '_run', lambda rid: self.runs.get(rid))):
            p.start()
            self.addCleanup(p.stop)
        n._CACHE['value'] = None

    def approval(self):
        self.attention = [{'run_id': 'r9', 'request_id': 'q1', 'actionable': True, 'kind': 'approval', 'key': 'codex-x',
                           'created_at': '2026-11-23T10:00:00Z', 'payload': {'name': 'Bash', 'command': 'rm raw.csv'}}]
        return next(i for i in n.feed(now=self.now)['items'] if i['kind'] == 'approval')

    def test_debuggable_kinds(self):
        items = {i['kind']: i for i in n.feed(now=self.now)['items']}
        self.assertTrue(items['signed_out']['debuggable'])
        self.assertIsNone(items['signed_out']['debug'])
        self.assertTrue(self.approval()['debuggable'])
        self.assertFalse(debug_agent.debuggable({'state': 'archived', 'kind': 'approval'}))
        self.assertFalse(debug_agent.debuggable({'state': 'open', 'kind': 'usage_high'}))
        self.assertTrue(debug_agent.debuggable({'state': 'open', 'kind': 'other', 'run_id': 'r'}))

    def test_model_comes_from_the_live_list_cheapest_class(self):
        self.assertEqual(debug_agent.pick_model(debug_agent._models()), 'claude-haiku-4-0')
        self.assertEqual(debug_agent.pick_model([{'id': 'claude-sonnet-5'}, {'id': 'opus'}]), 'claude-sonnet-5')
        self.assertEqual(debug_agent.pick_model([{'id': 'opus'}]), '')

    def test_debug_starts_agent_with_caller_line_and_model(self):
        item = self.approval()
        code = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertNotIsInstance(code, tuple, code)
        got = code['item']['debug']
        self.assertEqual((got['state'], got['model']), ('queued', 'claude-haiku-4-0'))
        brief, model, path, rid = self.starts[0]
        self.assertTrue(brief.startswith('Claude: parent_session_id=none target_provider=Claude'))
        self.assertIn('approval_policy.py', brief)
        self.assertIn(path, brief)
        self.assertEqual(model, 'claude-haiku-4-0')

    def test_second_click_while_active_is_409_and_progress_reaches_the_feed(self):
        item = self.approval()
        n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        res, status = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertEqual((status, res['error']), (409, 'debug_running'))
        rev = n.feed(now=self.now)['revision']
        path = debug_agent.progress_path(str(self.tmpdir), item['id'])
        debug_agent.write_progress(path, 'running', 'Reading the run record', review_key='claude-rev')
        n._CACHE['value'] = None
        feed = n.feed(now=self.now)
        got = next(i for i in feed['items'] if i['id'] == item['id'])['debug']
        self.assertEqual((got['state'], got['text'], got['review_key']), ('running', 'Reading the run record', 'claude-rev'))
        self.assertNotEqual(feed['revision'], rev)
        debug_agent.write_progress(path, 'needs_approval', 'raw.csv is raw data')
        n._CACHE['value'] = None
        self.assertEqual(next(i for i in n.feed(now=self.now)['items'] if i['id'] == item['id'])['debug']['state'], 'needs_approval')
        res = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)  # a final state may be run again
        self.assertEqual(res['item']['debug']['state'], 'queued')

    def test_agent_run_that_stops_without_verdict_shows_failed_or_review(self):
        item = self.approval()
        n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.runs['dbg1'] = {'status': 'failed', 'error': 'boom', 'key': 'claude-dbg'}
        n._CACHE['value'] = None
        got = next(i for i in n.feed(now=self.now)['items'] if i['id'] == item['id'])['debug']
        self.assertEqual((got['state'], got['agent_key']), ('failed', 'claude-dbg'))
        self.assertIn('boom', got['text'])

    def test_not_debuggable_is_400_and_unknown_item_404(self):
        res, status = n.act({'id': 'nope', 'action': 'debug'}, now=self.now)
        self.assertEqual(status, 404)
        with patch.object(debug_agent, 'debuggable', lambda item: False):
            item = self.approval()
            res, status = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertEqual(status, 400)

    def test_signed_out_starts_background_login_without_an_agent(self):
        item = next(i for i in n.feed(now=self.now)['items'] if i['kind'] == 'signed_out')
        res = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertEqual(self.logins, ['c1'])
        self.assertEqual(self.starts, [])
        self.assertEqual(res['item']['debug']['state'], 'needs_signin')

    def test_no_cheap_model_is_refused(self):
        item = self.approval()
        with patch.object(debug_agent, '_models', lambda: [{'id': 'opus'}]):
            res, status = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertEqual((status, res['code']), (409, 'no_model'))
        self.assertEqual(self.starts, [])

    def test_start_failure_is_a_failed_state_not_an_error(self):
        item = self.approval()
        with patch.object(debug_agent, 'start_agent', side_effect=RuntimeError('no cli')):
            res = n.act({'id': item['id'], 'action': 'debug'}, now=self.now)
        self.assertEqual(res['item']['debug']['state'], 'failed')


if __name__ == '__main__':
    unittest.main()
