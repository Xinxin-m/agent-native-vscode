"""Review fixes BX (2026-11-23): agent tree status and counts, account picker with signed-out
rows, login cancel, conversation defaults for a chat with no indexed transcript, live row provider."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import accounts
import accounts_guard  # noqa: F401
import conversation_settings
import live
import session_lineage


class TreeStatusTests(unittest.TestCase):
    def test_parent_uses_sidebar_status_and_stopped_is_counted_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = {'claude-p': {'id': 'p', 'source': 'claude', 'last_assistant_ts': '2026-11-22T10:00:00Z'}}
            for name in 'abcde':
                index['claude-p-agent-' + name] = {'id': name, 'is_child': True, 'parent_key': 'claude-p', 'root_key': 'claude-p',
                                                   'agent_id': name, 'last_assistant_ts': '2026-11-22T11:00:00Z'}
            for name, value in (('index', index), ('projects', {'assignments': {}}), ('seen', {'baseline': '2026-11-22T10:30:00.000Z', 'seen': {}})):
                (Path(tmp) / (name + '.json')).write_text(json.dumps(value))
            activity = {'a': 'running', 'b': 'finished', 'c': 'finished', 'd': 'stopped', 'e': 'failed'}
            with patch.object(session_lineage, 'DATA', tmp), patch.object(session_lineage, '_cached_resume', return_value={}), \
                    patch.object(session_lineage.agent_activity, '_live', return_value={}), \
                    patch.object(session_lineage.agent_activity, 'child_activity',
                                 side_effect=lambda meta, index, live=None: {'activity': activity[meta['id']]}), \
                    patch('sidebar.current_statuses', return_value={'claude-p': 'idle'}):
                tree = session_lineage.session_tree(None, {'key': 'claude-p'}, None)
        nodes = {n['key']: n for n in tree['nodes']}
        self.assertEqual((nodes['claude-p']['live_status'], nodes['claude-p']['state'], nodes['claude-p']['unseen']), ('idle', 'idle', False))
        self.assertTrue(nodes['claude-p-agent-a']['unseen'])  # result newer than the baseline, same rule as the sidebar row
        self.assertEqual(tree['counts'], {'starting': 0, 'working': 1, 'waiting': 0, 'idle': 1, 'finished': 2, 'stopped': 1, 'failed': 1, 'unknown': 0})

    def test_node_state_order(self):
        s = session_lineage.node_state
        self.assertEqual(s({'live_status': 'waiting', 'activity': 'finished'}), 'waiting')
        self.assertEqual(s({'activity': 'unknown'}), 'unknown')
        self.assertEqual(s({'status': 'failed'}), 'failed')
        self.assertEqual(s({}), 'finished')


ROWS = [{'id': 'c1', 'tool': 'claude', 'label': 'Main', 'email': 'me@x.test', 'logged_in': True}]
PENDING = [{'id': 'c2', 'tool': 'claude', 'label': 'Easy Labs', 'email': '', 'logged_in': False, 'previously_used': True},
           {'id': 'c3', 'tool': 'claude', 'label': 'Half set up', 'logged_in': False, 'previously_used': False},
           {'id': 'c4', 'tool': 'claude', 'label': 'Twin', 'logged_in': False, 'previously_used': True, 'duplicate_of': 'c1'}]


class PickerSignedOutTests(unittest.TestCase):
    def test_signed_out_rows_listed_once_and_never_auto(self):
        with patch.object(accounts, 'resolve_launch', side_effect=lambda tool, model, d=None: {'account_id': 'c2' if tool == 'claude' else 'x'}):
            out = accounts.picker_list({}, rows=ROWS, pending=PENDING)
        self.assertEqual([(a['id'], a['logged_in']) for a in out['accounts']], [('c1', True), ('c2', False)])
        self.assertIsNone(out['auto']['claude'])  # Auto resolved to a signed-out row: it is not named
        with patch.object(accounts, 'resolve_launch', return_value={'account_id': 'c1'}):
            self.assertEqual(accounts.picker_list({}, rows=ROWS, pending=PENDING)['auto']['claude']['label'], 'Main')


FAKE = '#!/usr/bin/python3\nimport sys,time\nprint("https://claude.com/cai/oauth/authorize?state=%s",flush=True)\nprint("Paste code here",flush=True)\ntime.sleep(60)\n'


class LoginCancelTests(unittest.TestCase):
    def test_cancel_stops_only_that_accounts_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('a', 'b'):
                path = Path(tmp) / ('claude-' + name)
                path.write_text(FAKE % name)
                path.chmod(0o700)
            d = {'accounts': [{'id': 'claude-default', 'tool': 'claude', 'config_dir': tmp + '/default'},
                              {'id': 'claude-b', 'tool': 'claude', 'config_dir': tmp + '/b'}],
                 'pending_accounts': [], 'cache': {}, 'state': {'global_active': {'claude': 'claude-default'}, 'active': {}}}
            rows = [{'id': 'claude-default', 'logged_in': False}, {'id': 'claude-b', 'logged_in': False}]
            clis = iter([str(Path(tmp) / 'claude-a'), str(Path(tmp) / 'claude-b')])
            with patch.object(accounts, 'load', return_value=d), patch.object(accounts, '_login_activity', return_value=None), \
                    patch.object(accounts, '_open_login_browser', return_value=True), patch.object(accounts, '_cli', side_effect=lambda t: next(clis)), \
                    patch.object(accounts, '_auth_env', return_value=dict(os.environ)), \
                    patch.object(accounts, 'payload', return_value={'accounts': [], 'pending_accounts': rows}):
                try:
                    one = accounts.start_login('claude-default')
                    two = accounts.start_login('claude-b')
                    procs = {s['id']: s['process'] for s in accounts._login_sessions.values()}
                    self.assertEqual(len(procs), 2)
                    result = accounts.cancel_login('claude-default')
                    self.assertEqual((result['ok'], result['cancelled'], result['account']['id']), (True, True, 'claude-default'))
                    self.assertIsNotNone(procs['claude-default'].poll())
                    self.assertIsNone(procs['claude-b'].poll())  # the other account's sign-in keeps running
                    self.assertEqual([s['id'] for s in accounts._login_sessions.values()], ['claude-b'])
                    again = accounts.cancel_login('claude-default')  # nothing pending: safe
                    self.assertEqual((again['ok'], again['cancelled']), (True, False))
                    with self.assertRaises(ValueError):
                        accounts.cancel_login('nope')
                    self.assertEqual(accounts._h_login_cancel(None, {}, {'id': 'nope'})[1], 404)
                finally:
                    accounts.cancel_login('claude-b')
                    accounts.cancel_login('claude-default')
            self.assertEqual(accounts._login_sessions, {})


class ConversationDefaultsTests(unittest.TestCase):
    def test_unindexed_key_is_200_with_native_false_and_indexed_is_native_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'index.json').write_text(json.dumps({'claude-real': {'key': 'claude-real', 'id': 'real', 'source': 'claude', 'cwd': '/w', 'execution_host': 'sc'}}))
            with patch.object(conversation_settings, 'DATA', Path(tmp)), \
                    patch.object(conversation_settings, 'local_defaults', return_value={'provider': 'claude', 'host_id': 'mac', 'cwd': '/home'}):
                synthetic = conversation_settings.defaults(None, {'key': 'claude-qa-fixture'}, {})
                real = conversation_settings.defaults(None, {'key': 'claude-real'}, {})
                blank = conversation_settings.defaults(None, {'provider': 'codex'}, {})
        self.assertEqual((synthetic['native'], synthetic['defaults']['provider']), (False, 'claude'))
        self.assertEqual((real['native'], real['defaults']['host_id']), (True, 'sc'))
        self.assertFalse(blank['native'])

    def test_reads_the_server_data_folder_not_the_source_folder(self):
        # The synthetic QA server remaps module-level paths under data/; the index must come from there.
        self.assertEqual(conversation_settings.DATA, conversation_settings.HERE / 'data')


class LiveProviderTests(unittest.TestCase):
    def test_no_transcript_row_keeps_its_provider(self):
        row = {'id': 'mac:23', 'tool': 'claude', 'node': 'mac', 'status': 'waiting', 'alive': True, 'session_id': 'sid-nothing', 'chat_key': '',
               'observed_at': time.time()}
        cached = {'fetched_at': 1, 'sessions': [row], 'nodes': []}
        with patch.object(live, '_claude_transcript', return_value=False), patch.object(live, '_index', return_value={}):
            out = live._with_host_sessions(cached)['sessions'][0]
        self.assertEqual((out['tool'], out['index_state'], out['live_status']), ('claude', 'no_transcript', 'waiting'))


if __name__ == '__main__':
    unittest.main()
