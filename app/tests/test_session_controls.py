"""Session safety tests use fabricated processes and a fake signal sink only."""
import copy
import os
import time
import tempfile
import unittest
from unittest.mock import patch
import live


class SessionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name in ('POLICY_JSON', 'OUTCOMES_JSON'):
            patcher = patch.object(live, name, os.path.join(self.temp.name, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        host_patcher = patch('hosts.host_records', return_value=[])
        host_patcher.start()
        self.addCleanup(host_patcher.stop)
        self.now = time.time()
        self.row = {'id': 'mac:777777', 'tool': 'claude', 'node': 'mac', 'host': 'mac', 'name': 'synthetic', 'status': 'idle', 'alive': True, 'session_id': 'synthetic-session', 'pid': 777777, 'cwd': '/synthetic', 'observed_at': self.now, 'last_act_ts': self.now - 90000, 'identity': {'pid': 777777, 'uid': os.getuid(), 'start_sec': 100, 'start_usec': 4321, 'executable': '/usr/local/bin/claude', 'boot_id': 'boot-one'}}

    def require(self, name):
        self.assertTrue(hasattr(live, name), name + ' must exist')
        return getattr(live, name)

    def test_idle_review_excludes_stale_busy_unknown_and_recent_sessions(self):
        fn = self.require('idle_review')
        cases = [self.row, dict(self.row, id='stale', observed_at=self.now - 600), dict(self.row, id='busy', status='busy'), dict(self.row, id='unknown', last_act_ts=None), dict(self.row, id='recent', last_act_ts=self.now - 10)]
        self.assertEqual([r['id'] for r in fn({'sessions': cases}, now=self.now)], ['mac:777777'])

    def test_missing_native_identity_cannot_stop(self):
        self.require('stop_preview')
        with patch.object(live, 'mac_sessions', return_value=([dict(self.row, identity=None)], [])):
            with self.assertRaises(ValueError):
                live.stop_preview('mac:777777')

    def test_remote_and_ambiguous_codex_cannot_stop(self):
        self.require('stop_preview')
        for row in (dict(self.row, id='sc:777777', node='sc'), dict(self.row, tool='codex')):
            with self.subTest(row=row), patch.object(live, 'mac_sessions', return_value=([row], [])), patch('hosts.probe_host', return_value={'state': 'online', 'sessions': [row]}), self.assertRaises(ValueError):
                live.stop_preview(row['id'])

    def test_preview_never_signals_and_pid_reuse_blocks_commit(self):
        self.require('stop_preview')
        self.require('stop_commit')
        sent = []
        with patch.object(live, 'mac_sessions', return_value=([self.row], [])), patch.object(live, '_validate_stop_session', return_value=None), patch.object(live.os, 'kill', side_effect=lambda *args: sent.append(args)):
            preview = live.stop_preview(self.row['id'])
            self.assertEqual(sent, [])
            reused = copy.deepcopy(self.row)
            reused['identity']['start_usec'] = 9876
            with patch.object(live, 'mac_sessions', return_value=([reused], [])), self.assertRaises(ValueError):
                live.stop_commit(preview['token'])
            self.assertEqual(sent, [])

    def test_gone_since_preview_blocks_commit_and_token_is_single_use(self):
        self.require('stop_preview')
        self.require('stop_commit')
        with patch.object(live, 'mac_sessions', return_value=([self.row], [])), patch.object(live, '_validate_stop_session', return_value=None), patch.object(live.os, 'kill') as signal_sink:
            preview = live.stop_preview(self.row['id'])
            with patch.object(live, 'mac_sessions', return_value=([dict(self.row, alive=False)], [])), self.assertRaises(ValueError):
                live.stop_commit(preview['token'])
            with self.assertRaises(ValueError):
                live.stop_commit(preview['token'])
            signal_sink.assert_not_called()

    def test_commit_requests_only_selected_pid_and_never_force_kills(self):
        self.require('stop_preview')
        self.require('stop_commit')
        sent = []
        with patch.object(live, 'mac_sessions', return_value=([self.row], [])), patch.object(live, '_validate_stop_session', return_value=None), patch.object(live, 'process_identity', return_value=self.row['identity']), patch.object(live.os, 'kill', side_effect=lambda *args: sent.append(args)):
            preview = live.stop_preview(self.row['id'])
            result = live.stop_commit(preview['token'])
        self.assertEqual(result['state'], 'stop_requested')
        self.assertEqual(sent, [(777777, live.signal.SIGTERM)])

    def test_session_record_must_match_native_start_owner_workspace_and_idle_state(self):
        self.require('_validate_stop_session')
        record = {'pid': 777777, 'sessionId': 'synthetic-session', 'status': 'idle', 'cwd': '/synthetic', 'procStart': 'Thu Jan 01 00:01:40 1970'}
        with patch.object(live, 'process_identity', return_value=self.row['identity']), patch.object(live, '_jload', return_value=record):
            live._validate_stop_session(self.row)
        variants = [ dict(record, sessionId='different'), dict(record, cwd='/different'), dict(record, procStart=None), dict(record, procStart='Thu Jan 01 00:01:41 1970')]
        for invalid in variants:
            with self.subTest(record=invalid), patch.object(live, 'process_identity', return_value=self.row['identity']), patch.object(live, '_jload', return_value=invalid), self.assertRaises(ValueError):
                live._validate_stop_session(self.row)
        for identity in (dict(self.row['identity'], uid=os.getuid()+1), dict(self.row['identity'], executable='/bin/sleep'), dict(self.row['identity'], boot_id='')):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                live._stop_candidate(dict(self.row, identity=identity))

    def test_expired_preview_never_signals(self):
        self.require('stop_preview')
        with patch.object(live, 'mac_sessions', return_value=([self.row], [])), patch.object(live, '_validate_stop_session', return_value=None), patch.object(live.os, 'kill') as signal_sink:
            preview = live.stop_preview(self.row['id'])
            with patch.object(live.time, 'time', return_value=self.now + 70), self.assertRaises(ValueError):
                live.stop_commit(preview['token'])
            signal_sink.assert_not_called()


    def test_policy_validation_and_disabled_default(self):
        self.assertEqual(live.stop_policy(), {'enabled': False, 'basis': 'age', 'hours': 24})
        for value in ({}, {'enabled': 1, 'basis': 'age', 'hours': 24}, {'enabled': True, 'basis': 'x', 'hours': 24}, {'enabled': True, 'basis': 'age', 'hours': float('nan')}, {'enabled': True, 'basis': 'age', 'hours': 0}):
            with self.assertRaises(ValueError): live._policy(value)
        self.assertEqual(live.post_policy(None, {}, {'enabled': True, 'basis': 'idle', 'hours': 48})['hours'], 48)
        with patch.object(live, 'stop_policy', return_value=live.DEFAULT_POLICY), patch.object(live, 'mac_sessions') as inventory:
            live.enforce_stop_policy()
            inventory.assert_not_called()

    def test_auto_age_can_stop_busy_verified_session_and_idle_rule_skips_busy(self):
        row = dict(self.row, stop_allowed=True, status='busy')
        with patch.object(live, 'mac_sessions', return_value=([row], [])), patch.object(live, '_current_stop_session', return_value=row), patch.object(live, 'stop_preview', return_value={'token': 'test'}), patch.object(live, 'stop_commit') as commit:
            with patch.object(live, 'stop_policy', return_value={'enabled': True, 'basis': 'idle', 'hours': 24}):
                live.enforce_stop_policy()
                commit.assert_not_called()
            with patch.object(live, 'stop_policy', return_value={'enabled': True, 'basis': 'age', 'hours': 24}):
                live.enforce_stop_policy()
                commit.assert_called_once_with('test', source='automatic', policy={'enabled': True, 'basis': 'age', 'hours': 24})

    def test_auto_fresh_activity_and_disabled_rule_cancel_stop(self):
        row = dict(self.row, stop_allowed=True)
        policy = {'enabled': True, 'basis': 'idle', 'hours': 24}
        with patch.object(live, 'mac_sessions', return_value=([row], [])), patch.object(live, 'stop_preview', return_value={'token': 'test'}), patch.object(live, 'stop_commit') as commit:
            with patch.object(live, 'stop_policy', side_effect=[policy, live.DEFAULT_POLICY]):
                live.enforce_stop_policy()
                commit.assert_not_called()
            with patch.object(live, 'stop_policy', return_value=policy), patch.object(live, '_current_stop_session', return_value=dict(row, last_act_ts=time.time())):
                live.enforce_stop_policy()
                commit.assert_not_called()

    def test_stop_outcome_waits_for_confirmed_exit(self):
        live._record_outcome(self.row, 'stop_requested', 'manual')
        with patch.object(live, 'process_identity', return_value=None), patch.object(live.os, 'kill', return_value=None):
            self.assertEqual(live.stop_outcomes()[0]['state'], 'stop_requested')
        with patch.object(live, 'process_identity', return_value=None), patch.object(live.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(live.stop_outcomes()[0]['state'], 'stopped')

    def test_busy_manual_stop_still_requires_matching_native_identity(self):
        row = dict(self.row, status='busy')
        with patch.object(live, 'mac_sessions', return_value=([row], [])), patch.object(live, '_validate_stop_session'), patch.object(live, 'process_identity', return_value=row['identity']), patch.object(live.os, 'kill') as sink:
            live.stop_commit(live.stop_preview(row['id'])['token'])
            sink.assert_called_once_with(row['pid'], live.signal.SIGTERM)

if __name__ == '__main__':
    unittest.main()
