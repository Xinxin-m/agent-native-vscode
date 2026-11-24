"""Stop for broker-owned runs, against fake provider processes only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import broker
import live


class BrokerStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.store = broker.RunStore(self.data / 'execution')
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def fake_provider(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.procs.append(proc)
        for _ in range(50):
            identity = broker.process_identity(proc.pid)
            if identity:
                return proc, identity
            time.sleep(.02)
        self.skipTest('process identity unavailable on this platform')

    def owned_run(self, proc, identity, **fields):
        run = self.store.create({'provider': 'codex', 'key': 'codex-k', 'cwd': self.tmp.name, 'host_id': 'mac'})
        return self.store.update(run['id'], status='running', process_pid=proc.pid, process_identity=identity,
                                 process_transport='local', **fields)

    def test_live_inventory_marks_broker_process_stoppable_with_plain_reasons_elsewhere(self):
        proc, identity = self.fake_provider()
        run = self.owned_run(proc, identity)
        other = 424242
        procs = {proc.pid: (1, 'codex', 'codex app-server --listen stdio://'), other: (1, 'codex', 'codex')}
        with patch.object(live, 'DATA', str(self.data)), patch.object(live, 'HOME', self.tmp.name), \
                patch.object(live, 'proc_table', return_value=procs), patch.object(live, 'start_times', return_value={}), \
                patch.object(live, 'tmux_state', return_value=({}, {})), patch.object(live, 'sh', side_effect=lambda argv, **k: 'boot' if argv[0] == 'sysctl' else ''), \
                patch.object(live, 'codex_rollouts', return_value=[]):
            rows, _ = live.mac_sessions()
        by_pid = {r['pid']: r for r in rows}
        self.assertEqual(by_pid[proc.pid]['broker_run_id'], run['id'])
        self.assertTrue(by_pid[proc.pid]['stop_allowed'])
        self.assertFalse(by_pid[other]['stop_allowed'])
        reason = by_pid[other]['stop_reason']
        self.assertTrue(reason.endswith('.') and 'Codex' in reason, reason)

    def test_stop_route_interrupts_a_broker_run(self):
        controller = broker.Broker(self.store)
        row = {'id': 'mac:1', 'pid': 1, 'node': 'mac', 'tool': 'codex', 'alive': True, 'observed_at': time.time(),
               'broker_run_id': 'run-1', 'identity': {'pid': 1}}
        with patch.object(live, 'mac_sessions', return_value=([row], [])), \
                patch.object(live, 'process_identity', return_value={'pid': 1}), \
                patch.object(live, 'OUTCOMES_JSON', str(self.data / 'outcomes.json')), \
                patch.object(broker, 'instance', return_value=controller), \
                patch.object(controller, 'control', return_value={'ok': True}) as control:
            token = live.stop_preview('mac:1')['token']
            result = live.stop_commit(token)
            with self.assertRaises(ValueError):
                live.stop_commit(live.stop_preview('mac:1')['token'], source='automatic', policy={'basis': 'age', 'hours': 1})
        control.assert_called_once_with('run-1', {'action': 'interrupt'})
        self.assertEqual((result['state'], result['run_id']), ('stop_requested', 'run-1'))

    def test_run_left_running_by_a_server_restart_can_still_be_stopped(self):
        proc, identity = self.fake_provider()
        run = self.owned_run(proc, identity)
        restarted = broker.Broker(self.store)  # recovery blocks: the owned process is alive
        self.assertTrue(self.store.get(run['id'])['recovery_blocked'])
        result = restarted.control(run['id'], {'action': 'interrupt'})
        self.assertTrue(result['ok'])
        proc.wait(timeout=5)
        restarted.control(run['id'], {'action': 'reconcile'})
        self.assertFalse(self.store.get(run['id'])['recovery_blocked'])
        with self.assertRaises(ValueError):
            restarted.control(run['id'], {'action': 'interrupt'})

    def test_remote_orphan_gets_a_plain_reason(self):
        run = self.store.create({'provider': 'claude', 'key': 'k', 'host_id': 'sc'})
        run = self.store.update(run['id'], status='interrupted', recovery_blocked=True, process_transport='ssh', process_pid=99999)
        with self.assertRaisesRegex(ValueError, 'another host'):
            broker.Broker.__new__(broker.Broker)._stop_orphan(run)


if __name__ == '__main__':
    unittest.main()
