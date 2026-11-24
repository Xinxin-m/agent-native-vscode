import os
import tempfile
import time
import unittest
from unittest.mock import patch

import live


def row(pid, status='idle', **extra):
    base = {'id': 'mac:%d' % pid, 'name': 'fake%d' % pid, 'pid': pid, 'tool': 'claude', 'node': 'mac', 'alive': True,
            'status': status, 'session_id': 'sid-%d' % pid, 'stop_allowed': True, 'observed_at': time.time()}
    base.update(extra)
    return base


class StatusTests(unittest.TestCase):
    def test_status_mapping_and_freshness(self):
        now = time.time()
        self.assertEqual(live.session_status(row(1, 'busy', observed_at=now - 1), now), 'working')
        self.assertEqual(live.session_status(row(1, 'waiting', observed_at=now - 1), now), 'waiting')
        self.assertEqual(live.session_status(row(1, 'idle', observed_at=now - 1), now), 'idle')
        self.assertIsNone(live.session_status(row(1, '?', observed_at=now - 1), now))
        self.assertIsNone(live.session_status(row(1, 'busy', observed_at=now - 1000), now))
        self.assertIsNone(live.session_status(row(1, 'busy', process_only=True, observed_at=now - 1), now))
        self.assertIsNone(live.session_status(row(1, 'busy', alive=False, observed_at=now - 1), now))

    def test_index_state_for_session_without_transcript(self):
        with patch.object(live, '_claude_transcript', return_value=False):
            self.assertEqual(live.index_state(row(1))['index_state'], 'no_transcript')
        with patch.object(live, '_claude_transcript', return_value=True):
            self.assertEqual(live.index_state(row(1))['index_state'], 'not_indexed')
        self.assertEqual(live.index_state(row(1, chat_key='claude-x')), {})


class StopIdleTests(unittest.TestCase):
    def run_stop(self, rows, verify=None, status_of=None):
        killed = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(live, 'OUTCOMES_JSON', os.path.join(tmp, 'o.json')):
            result = live.stop_idle_sessions(sessions=rows, verify=verify or (lambda r: None),
                                             signal_process=lambda r: killed.append(r['pid']),
                                             status_of=status_of or (lambda r: 'idle'))
        return result, killed

    def test_only_fresh_idle_allowed_rows_are_stopped(self):
        rows = [row(1), row(2, 'busy'), row(3, 'waiting'), row(4, stop_allowed=False, stop_reason='no'),
                row(5, process_only=True), row(6, tool='codex'), row(7, broker_run_id='r1'), row(8)]
        result, killed = self.run_stop(rows)
        self.assertEqual(killed, [1, 8])
        states = {o['pid']: o['state'] for o in result['outcomes']}
        self.assertEqual([states[p] for p in range(1, 9)], ['stop_requested'] + ['skipped'] * 6 + ['stop_requested'])

    def test_identity_change_or_activity_at_stop_time_refuses(self):
        def verify(r):
            if r['pid'] == 1:
                raise ValueError('Process identity changed')
        result, killed = self.run_stop([row(1), row(2), row(3)], verify, lambda r: 'busy' if r['pid'] == 2 else 'idle')
        self.assertEqual(killed, [3])
        self.assertEqual([o['state'] for o in result['outcomes']], ['refused', 'refused', 'stop_requested'])

    def test_duplicate_ids_are_ambiguous(self):
        result, killed = self.run_stop([row(1), row(1)])
        self.assertEqual(killed, [])
        self.assertTrue(all('ambiguous' in o['reason'] for o in result['outcomes']))


if __name__ == '__main__':
    unittest.main()
