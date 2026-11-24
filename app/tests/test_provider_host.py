"""A provider started through provider_host.py survives a server restart.

The "server" runs in a child process that is SIGKILLed, as launchd does on a
restart; a new Broker on the same run store must reattach to the provider,
journal each provider line once and finish the run with every turn.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import broker

# Fake Claude: first turn starts a background task, which finishes when the
# test writes `go`; later stdin messages each get a turn; EOF ends it.
FAKE = '''import json,sys,time,pathlib
here = pathlib.Path(sys.argv[1])
def send(x): print(json.dumps(x), flush=True)
def read(): return json.loads(sys.stdin.readline())
def text(m): return m['message']['content'][0]['text']
assert read()['request']['subtype']=='initialize'
send({'type':'control_response','response':{'request_id':'initialize','subtype':'success'}})
first = read()
send({'type':'user','isReplay':True,'session_id':'s','message':first['message']})
send({'type':'system','subtype':'background_tasks_changed','session_id':'s','tasks':[{'task_id':'b1','description':'sleep job'}]})
send({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'STARTED'}}})
send({'type':'result','session_id':'s','result':'STARTED'})
while not (here/'go').exists():
    time.sleep(.02)
send({'type':'system','subtype':'task_notification','session_id':'s','task_id':'b1','status':'completed'})
send({'type':'system','subtype':'background_tasks_changed','session_id':'s','tasks':[]})
send({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'BACKGROUND FINISHED'}}})
send({'type':'result','session_id':'s','origin':{'kind':'task-notification'},'result':'BACKGROUND FINISHED'})
if (here/'exit-after-background').exists():
    sys.exit(0)
for line in sys.stdin:
    m = json.loads(line)
    send({'type':'user','isReplay':True,'session_id':'s','message':m['message']})
    send({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'echo: '+text(m)}}})
    send({'type':'result','session_id':'s','result':'echo: '+text(m)})
(here/'eof').write_text('eof')
'''

# The old service: start (or reattach to) the run, then wait to be killed.
SERVER = '''import os,sys,time
from unittest.mock import patch
sys.path.insert(0, {root!r})
import broker
tmp = {tmp!r}
launch = {launch!r}
with patch.object(broker.stream, 'INDEX', os.path.join(tmp, 'index')), patch.object(broker.stream, 'CHATS', os.path.join(tmp, 'chats')), \\
        patch.object(broker.Broker, '_launch', return_value=launch), patch.object(broker.Broker, '_persist'):
    obj = broker.Broker(broker.RunStore(os.path.join(tmp, 'store')))
    if {start!r}:
        obj.start({{'provider': 'claude', 'cwd': tmp, 'host_id': 'mac', 'session_id': '', 'prompt': 'hello',
                   'message': 'hello', 'key': ''}})
    while True:
        time.sleep(1)
'''


class ProviderHostRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp)
        (self.dir / 'fake.py').write_text(FAKE)
        self.launch = {'argv': [sys.executable, str(self.dir / 'fake.py'), self.tmp], 'cwd': self.tmp,
                       'env': os.environ.copy(), 'protocol': 'claude-stream'}
        self.servers = []
        self.controllers = []
        for name in ('INDEX', 'CHATS'):
            guard = patch.object(broker.stream, name, os.path.join(self.tmp, name.lower()))
            guard.start()
            self.addCleanup(guard.stop)

    def tearDown(self):
        for server in self.servers:
            if server.poll() is None:
                server.kill()
            server.wait()
        for controller in self.controllers:
            for ctrl in list(controller.active.values()):
                controller._signal(ctrl, signal.SIGKILL)
                ctrl['finished'].wait(8)
        for run in self.read_runs():
            pid = run.get('process_pid')
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
        subprocess.run(['rm', '-rf', self.tmp])

    def read_runs(self):
        return [json.loads(p.read_text()) for p in (self.dir / 'store' / 'runs').glob('*/run.json')]

    def journal(self, rid):
        path = self.dir / 'store' / 'runs' / rid / 'events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except (OSError, ValueError, IndexError, KeyError):
                pass
            time.sleep(.05)
        return False

    def deltas(self, rid):
        return [e['data'].get('text', '').strip() for e in self.journal(rid) if e['event'] == 'delta']

    def old_server(self, start):
        code = SERVER.format(root=str(ROOT), tmp=self.tmp, launch=self.launch, start=start)
        server = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL)
        self.servers.append(server)
        return server

    def kill(self, server):
        server.send_signal(signal.SIGKILL)
        server.wait()

    def new_server(self):
        guards = [patch.object(broker.Broker, '_launch', return_value=self.launch),
                  patch.object(broker.Broker, '_persist'), patch.object(broker, 'IDLE_GRACE', 1)]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)
        controller = broker.Broker(broker.RunStore(self.dir / 'store'))
        self.controllers.append(controller)
        return controller

    def start_and_kill_after_first_turn(self):
        server = self.old_server(True)
        self.assertTrue(self.until(lambda: len(self.read_runs()) == 1 and 'STARTED' in self.deltas(self.read_runs()[0]['id'])))
        rid = self.read_runs()[0]['id']
        self.kill(server)
        return rid

    def assert_each_line_journaled_once(self, rid):
        offsets = [e['data']['offset'] for e in self.journal(rid) if e['event'] == 'provider_event']
        self.assertEqual(offsets, sorted(set(offsets)), 'a provider line was journaled twice or out of order')

    def test_provider_survives_restart_and_run_continues(self):
        rid = self.start_and_kill_after_first_turn()
        run = self.read_runs()[0]
        os.kill(run['process_pid'], 0)  # provider outlived the service
        controller = self.new_server()
        self.assertIn(rid, controller.active)
        ctrl = controller.active[rid]
        self.assertTrue(self.until(lambda: any(e['event'] == 'reattached' for e in self.journal(rid))))
        self.assertEqual(controller.store.get(rid)['status'], 'running')
        self.assertEqual(controller.store.get(rid)['phase'], 'background')
        (self.dir / 'go').write_text('go')
        self.assertTrue(self.until(lambda: 'BACKGROUND FINISHED' in self.deltas(rid)))
        result = controller.queue_action({'action': 'enqueue', 'key': run['key'], 'message': 'follow-up', 'request_id': 'q1'})
        self.assertTrue(result['ok'])
        self.assertTrue(ctrl['finished'].wait(15), 'idle close did not end the provider')
        final = controller.store.get(rid)
        self.assertEqual(final['status'], 'completed', final.get('error'))
        self.assertEqual(final['text'], 'STARTED\n\nBACKGROUND FINISHED\n\necho: follow-up')
        self.assertTrue((self.dir / 'eof').exists(), 'provider was killed instead of reading EOF')
        self.assertEqual(self.deltas(rid), ['STARTED', 'BACKGROUND FINISHED', 'echo: follow-up'])
        self.assert_each_line_journaled_once(rid)
        self.assertEqual([i['state'] for i in controller._queue_items(run['conversation_key'] or run['key'])], ['delivered'])
        self.assertFalse((self.dir / 'store' / 'runs' / rid / 'host').exists())

    def test_provider_that_finished_during_restart_completes_from_its_output(self):
        (self.dir / 'exit-after-background').write_text('1')
        rid = self.start_and_kill_after_first_turn()
        (self.dir / 'go').write_text('go')
        self.assertTrue(self.until(lambda: (self.dir / 'store' / 'runs' / rid / 'host' / 'exit.json').exists()))
        controller = self.new_server()
        self.assertTrue(controller.active[rid]['finished'].wait(15))
        final = controller.store.get(rid)
        self.assertEqual(final['status'], 'completed', final.get('error'))
        self.assertEqual(final['text'], 'STARTED\n\nBACKGROUND FINISHED')
        self.assertEqual(self.deltas(rid), ['STARTED', 'BACKGROUND FINISHED'])
        self.assert_each_line_journaled_once(rid)

    def test_two_restarts_in_a_row_converge(self):
        rid = self.start_and_kill_after_first_turn()
        second = self.old_server(False)  # reattaches, then is killed as well
        self.assertTrue(self.until(lambda: sum(e['event'] == 'reattached' for e in self.journal(rid)) == 1))
        self.kill(second)
        controller = self.new_server()
        self.assertTrue(self.until(lambda: sum(e['event'] == 'reattached' for e in self.journal(rid)) == 2))
        (self.dir / 'go').write_text('go')
        self.assertTrue(self.until(lambda: 'BACKGROUND FINISHED' in self.deltas(rid)))
        self.assertTrue(controller.active[rid]['finished'].wait(15))
        final = controller.store.get(rid)
        self.assertEqual(final['status'], 'completed', final.get('error'))
        self.assertEqual(self.deltas(rid), ['STARTED', 'BACKGROUND FINISHED'])
        self.assert_each_line_journaled_once(rid)

    def test_direct_child_from_an_older_service_is_still_recovered_not_reattached(self):
        store = broker.RunStore(self.dir / 'store')
        run = store.create({'provider': 'claude', 'key': 'claude-old', 'host_id': 'mac'})
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        store.update(run['id'], status='running', process_pid=proc.pid, process_identity=broker.process_identity(proc.pid))
        controller = self.new_server()
        self.assertNotIn(run['id'], controller.active)
        recovered = controller.store.get(run['id'])
        self.assertEqual(recovered['status'], 'interrupted')
        self.assertTrue(recovered['recovery_blocked'])


if __name__ == '__main__':
    unittest.main()
