import unittest
from unittest.mock import patch

import live
import live_refresh
import sidebar


class FakeServer:
    def __init__(self, polls=2):
        self.polls, self.started = polls, 0

    def start_history_refresh(self, rescan=False):
        self.started += 1
        assert rescan is False  # parse only: a rescan runs the uncapped labelling pass
        return {'ok': True, 'started': True, 'refresh': {'id': 'h1'}}

    def history_refresh_status(self, hid):
        self.polls -= 1
        return {'running': self.polls > 0, 'phase': 'local', 'errors': []}


class RefreshTests(unittest.TestCase):
    def test_done_only_after_live_and_history_finish(self):
        calls = []
        with patch.object(live, 'refresh', side_effect=lambda remote=True: calls.append(remote)), \
                patch.object(live_refresh, 'publish', return_value=0), patch.object(sidebar.time, 'sleep'):
            job = sidebar.start_refresh(FakeServer(), sync=True)
        self.assertTrue(job['done'] and job['ok'])
        self.assertEqual(calls, [False])
        self.assertEqual(job['history']['id'], 'h1')

    def test_live_failure_is_reported_not_raised(self):
        with patch.object(live, 'refresh', side_effect=RuntimeError('probe')), patch.object(sidebar.time, 'sleep'):
            job = sidebar.start_refresh(FakeServer(1), sync=True)
        self.assertTrue(job['done'])
        self.assertFalse(job['ok'])
        self.assertIn('probe', job['live']['error'])

    def test_unknown_id_is_404(self):
        self.assertEqual(sidebar.get_refresh(None, {'id': 'nope'}, None)[1], 404)


if __name__ == '__main__':
    unittest.main()
