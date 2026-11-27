import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import server
import sidebar


class RunLivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_port_collision_starts_no_background_work(self):
        with patch.object(server, 'jload', return_value={}), \
                patch.object(server, 'ThreadingHTTPServer', side_effect=OSError('address in use')), \
                patch.object(server, 'load_plugins'), patch.object(server, 'run_background') as background, \
                patch.object(server, 'light_sync') as sync:
            with self.assertRaisesRegex(OSError, 'address in use'):
                server.main()
        background.assert_not_called()
        sync.assert_not_called()

    def test_second_broker_cannot_recover_an_owned_run(self):
        with patch.object(broker, 'DATA', self.root), patch.object(broker, '_BROKER', None):
            owner = broker.instance()
            self.addCleanup(lambda: getattr(owner, 'owner_lock', None) and owner.owner_lock.close())
            run = owner.store.create({'provider': 'codex'})
            owner.store.update(run['id'], status='running')
            child_code = 'import broker; from pathlib import Path; broker.DATA = Path(__import__("sys").argv[1]); broker.instance()'
            child = subprocess.run([sys.executable, '-c', child_code, str(self.root)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('already owns this execution store', child.stderr)
        saved = broker.RunStore(self.root / 'execution')
        self.assertEqual(saved.get(run['id'])['status'], 'running')
        self.assertFalse(any(event['event'] == 'recovery' for event in saved.events(run['id'])))

    def test_missing_stderr_does_not_lose_hosted_output(self):
        (self.root / 'out.jsonl').write_text('{"type":"result"}\n')
        proc = broker.HostedProcess(self.root, 0, 0)
        proc.returncode = 0
        stderr = []
        lines = broker.Broker._hosted_lines(None, proc, {}, stderr, lambda: None)
        self.assertEqual(list(lines), ['{"type":"result"}'])
        self.assertFalse(stderr)

    def test_stall_and_recovery_preserve_stop_and_queue_ownership(self):
        store = broker.RunStore(self.root / 'execution')
        controller = broker.Broker(store)
        rid = store.create({'provider': 'codex', 'key': 'codex-example'})['id']
        store.update(rid, status='running', phase='turn')
        ctrl = {'turn_open': True}
        controller._check_stall(rid, ctrl, 299)
        self.assertFalse(store.get(rid).get('stalled'))
        controller._check_stall(rid, ctrl, 300)
        controller._check_stall(rid, ctrl, 305)
        stalled = store.get(rid)
        self.assertTrue(stalled['stalled'])
        self.assertEqual(stalled['status'], 'running')
        self.assertEqual(stalled['phase'], 'turn')
        self.assertEqual(sidebar._active_runs(self.root), {'codex-example': 'stalled'})
        self.assertEqual(sidebar.resolve('stalled', None, None, True), ('stalled', False, 'stalled'))
        controller._check_stall(rid, ctrl, 0)
        self.assertFalse(store.get(rid)['stalled'])
        self.assertEqual([e['data']['stalled'] for e in store.events(rid) if 'stalled' in e['data']], [True, False])
        for control in ({'turn_open': True, 'pending_requests': {'q': {}}}, {'turn_open': False}):
            controller._check_stall(rid, control, 600)
            self.assertFalse(store.get(rid)['stalled'])

    def test_active_goal_between_turns_can_stall(self):
        store = broker.RunStore(self.root / 'execution')
        controller = broker.Broker(store)
        rid = store.create({'provider': 'codex'})['id']
        store.update(rid, status='running', phase='background')
        ctrl = {'turn_open': False, 'goal': {'status': 'active'}}
        controller._check_stall(rid, ctrl, 300)
        self.assertTrue(store.get(rid).get('stalled'))
        store.update(rid, status='interrupting')
        self.assertFalse(store.get(rid).get('stalled'))

    def test_stalled_provider_can_be_stopped_normally(self):
        store = broker.RunStore(self.root / 'execution')
        controller = broker.Broker(store)
        spec = {'provider': 'codex', 'cwd': str(self.root), 'host_id': 'mac', 'session_id': 'stalled',
                'prompt': 'hello', 'message': 'hello', 'key': 'codex-stalled'}
        launch = {'argv': [sys.executable, '-c', 'import time; time.sleep(30)'], 'cwd': str(self.root),
                  'env': os.environ.copy(), 'protocol': 'app-server'}
        with patch.object(controller, '_launch', return_value=launch), patch.object(controller, '_persist'), \
                patch.object(controller, '_reconcile_native'), patch.object(broker, 'STALL_AFTER_SECONDS', .05):
            rid = controller.start(spec)['id']
            control = controller.active[rid]
            try:
                deadline = time.monotonic() + 5
                while not store.get(rid).get('stalled') and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(store.get(rid).get('stalled'))
            finally:
                controller.control(rid, {'action': 'interrupt'})
                self.assertTrue(control['finished'].wait(8))
        stopped = store.get(rid)
        self.assertEqual(stopped['status'], 'interrupted')
        self.assertFalse(stopped['stalled'])
        self.assertFalse(stopped.get('error'))

    def test_clean_provider_exit_without_turn_receipt_fails(self):
        store = broker.RunStore(self.root / 'execution')
        controller = broker.Broker(store)
        spec = {'provider': 'codex', 'cwd': str(self.root), 'host_id': 'mac', 'session_id': 'existing',
                'prompt': 'hello', 'message': 'hello', 'key': 'codex-existing'}
        launch = {'argv': [sys.executable, '-c', 'pass'], 'cwd': str(self.root),
                  'env': os.environ.copy(), 'protocol': 'app-server'}
        with patch.object(controller, '_launch', return_value=launch), patch.object(controller, '_persist'), \
                patch.object(controller, '_reconcile_native'):
            run = controller.start(spec)
            self.assertTrue(controller.active[run['id']]['finished'].wait(8))
        self.assertEqual(store.get(run['id'])['status'], 'failed')
        self.assertIn('before Codex confirmed', store.get(run['id'])['error'])

    def test_failed_notice_lookup_does_not_initialize_the_broker(self):
        import notifications
        store = broker.RunStore(self.root / 'execution')
        rid = store.create({'provider': 'codex', 'host_id': 'sc'})['id']
        store.update(rid, status='failed')
        with patch.object(broker, 'DATA', self.root), patch.object(broker, '_BROKER', None), \
                patch.object(broker, 'instance', side_effect=AssertionError('initialized broker')):
            self.assertEqual(notifications._failed_run(rid)['host_id'], 'sc')
            with self.assertRaises(ValueError):
                notifications._failed_run('unknown-run')
