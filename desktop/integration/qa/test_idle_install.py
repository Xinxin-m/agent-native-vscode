import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import idle_install as hook


class IdleInstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(hook, 'JOURNALS', self.root))
        self.live = {'fetched_at': time.time(), 'nodes': [{'name': 'mac', 'ok': True}], 'sessions': []}
        self.runs = {'runs': []}
        self.enterContext(patch.object(hook, 'api', side_effect=lambda route: self.live if route == 'live' else self.runs))

    def test_completed_and_observed_idle_pass(self):
        self.runs['runs'].append({'status': 'completed'})
        self.live['sessions'].append({'alive': True, 'live_status': 'idle'})
        hook.require_idle()

    def test_unattributed_activity_and_stale_inventory_defer(self):
        self.live['sessions'] = [{'alive': True, 'process_only': True}]
        with self.assertRaises(hook.Busy):
            hook.require_idle()
        self.live.update(sessions=[], fetched_at=time.time() - 121)
        with self.assertRaises(hook.Busy):
            hook.require_idle()

    def test_wait_for_old_runs_outside_recent_page(self):
        self.runs['runs'] = [{'status': 'completed'}] * 100
        folder = self.root / 'old'
        folder.mkdir()
        (folder / 'run.json').write_text(json.dumps({'status': 'running'}))
        with self.assertRaises(hook.Busy):
            hook.require_idle()

    def test_poll_keeps_pending_when_backend_unavailable(self):
        pending = self.root / 'pending.json'
        job = {'sha256': 'test'}
        with patch.object(hook, 'api', side_effect=OSError('offline')), patch.object(hook.install, 'install') as install:
            hook.poll(job, pending)
        self.assertEqual(json.loads(pending.read_text())['waiting'], 'offline')
        install.assert_not_called()

    def test_restart_deferred_then_verified_without_second_signal(self):
        job = {'backend': 'tested'}
        pending = self.root / 'pending.json'
        with patch.object(hook, 'backend_hash', return_value='tested'), \
             patch.object(hook, 'require_idle', side_effect=hook.Busy('running')), \
             patch.object(hook, 'backend_pid', return_value=11), \
             patch.object(hook.subprocess, 'run') as signal:
            with self.assertRaises(hook.Busy):
                hook.refresh_backend(job, pending)
            signal.assert_not_called()
        with patch.object(hook, 'backend_hash', return_value='tested'), \
             patch.object(hook, 'require_idle'), \
             patch.object(hook, 'backend_pid', side_effect=[11, 11, 12, 12]), \
             patch.object(hook.subprocess, 'run') as signal:
            with self.assertRaises(hook.Busy):
                hook.refresh_backend(job, pending)
            hook.refresh_backend(json.loads(pending.read_text()), pending)
            self.assertEqual(signal.call_count, 1)
            self.assertTrue(json.loads(pending.read_text())['backend_ready'])

    def test_idle_installs_exact_queue_and_consumes_it(self):
        pending = self.root / 'pending.json'
        job = {'candidate': 'candidate.app', 'sha256': 'hash', 'extension': 'ext', 'previous': 'old'}
        hook.save(pending, job)
        with patch.object(hook, 'require_idle'), patch.object(hook, 'STATE', self.root), \
             patch.object(hook.install, 'bundle_hash', return_value='old'), \
             patch.object(hook.install, 'install', return_value={'ok': True}) as install:
            hook.poll(job, pending)
            self.assertEqual(install.call_args.args, (Path('candidate.app'), 'hash', 'ext'))
            self.assertEqual(install.call_args.kwargs['expected_previous'], 'old')
            self.assertIs(install.call_args.kwargs['before_replace'], hook.require_idle)
            self.assertFalse(pending.exists())
            self.assertTrue(json.loads((self.root / 'last-install.json').read_text())['result']['ok'])

    def test_unverified_open_client_waits_for_a_safe_install(self):
        pending = self.root / 'pending.json'
        job = {'candidate': 'candidate.app', 'sha256': 'hash', 'extension': 'ext', 'previous': 'old'}
        hook.save(pending, job)
        reason = 'The daily client must use the verified 19342 debugging endpoint for preservation checks'
        with patch.object(sys, 'argv', ['idle_install.py', 'poll']), patch.object(hook, 'STATE', self.root), \
             patch.object(hook, 'require_idle'), patch.object(hook.install, 'bundle_hash', return_value='old'), \
             patch.object(hook.install, 'install', side_effect=hook.install.GuardError(reason)):
            hook.main()
        queued = json.loads(pending.read_text())
        self.assertNotIn('error', queued)
        self.assertEqual(queued['waiting'], 'Waiting for Library Code to quit so its workspace can be preserved.')

    def test_shared_codex_service_follows_its_open_turns(self):
        from types import SimpleNamespace
        row = {'alive': True, 'tool': 'codex', 'node': 'mac', 'pid': 123, 'process_only': True,
               'activity_note': 'Shared app service; conversation activity is not attributable'}
        folder = self.root / 'sessions'
        folder.mkdir()
        rollout = folder / 'rollout-fixture.jsonl'
        result = SimpleNamespace(returncode=0, stdout='ccodex\nn' + str(rollout))
        with patch.object(hook.subprocess, 'run', return_value=result):
            for event, idle in [('task_started', False), ('task_complete', True)]:
                rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': event}}) + '\n')
                self.assertEqual(hook.session_idle(row), idle)
            result.stdout = 'ccodex\nn/tmp/regular-file'
            self.assertFalse(hook.session_idle(row))
            result.returncode = 1
            self.assertFalse(hook.session_idle(row))
