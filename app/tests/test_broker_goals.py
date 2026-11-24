"""Synthetic Codex goal protocol/lifetime checks. Never launches a provider."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker


class CodexGoalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = broker.RunStore(self.tmp.name)
        self.owner = broker.Broker(self.store)
        self.run = self.store.create({'provider': 'codex', 'session_id': 'root',
                                      'cwd': self.tmp.name, 'message': 'finish work', 'prompt': 'finish work'})
        self.rid = self.run['id']
        self.ctrl = {'protocol': 'app-server', 'turn_open': True, 'background': {},
                     'pending_rpc': {}, 'control_lock': threading.RLock()}
        self.st = broker.stream._State('root')
        self.sent = []
        send = patch.object(self.owner, '_send', side_effect=lambda ctrl, data: self.sent.append(data))
        send.start()
        self.addCleanup(send.stop)
        self.addCleanup(lambda: self.owner._cancel_idle(self.ctrl))

    def goal(self, status='active', thread='root'):
        return {'threadId': thread, 'objective': 'finish work', 'status': status,
                'tokenBudget': None, 'tokensUsed': 0, 'timeUsedSeconds': 0,
                'createdAt': 1, 'updatedAt': 2}

    def event(self, ev):
        return self.owner._codex_rpc(self.rid, self.run, self.st, self.ctrl, ev)

    def bind(self):
        with patch.object(self.owner, '_bind'):
            self.event({'id': 2, 'result': {'thread': {'id': 'root'}}})

    def test_goal_validation_and_capability(self):
        spec = broker.prepare_run({'operation': 'goal', 'provider': 'codex', 'message': 'finish work',
                                   'new': {'cwd': self.tmp.name}}, {}, lambda key: {})
        self.assertEqual(spec['operation'], 'goal')
        self.assertTrue(broker.CAPABILITIES['codex']['goal'])
        for body in ({'provider': 'claude'}, {'attachments': ['x']}, {'message': ''}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                broker.prepare_run(dict({'operation': 'goal', 'provider': 'codex', 'message': 'finish work',
                                         'new': {'cwd': self.tmp.name}}, **body), {}, lambda key: {})

    def test_goal_receipt_authorizes_one_initial_turn_and_no_continuation_prompt(self):
        self.run['operation'] = 'goal'
        self.bind()
        self.assertEqual(self.sent, [{'id': 4, 'method': 'thread/goal/set', 'params': {
            'threadId': 'root', 'objective': 'finish work', 'status': 'active', 'origin': 'user'}}])
        self.event({'id': 4, 'result': {'goal': self.goal()}})
        self.assertEqual([x['method'] for x in self.sent], ['thread/goal/set', 'turn/start'])
        self.event({'id': 4, 'result': {'goal': self.goal()}})
        self.event({'method': 'turn/completed', 'params': {
            'threadId': 'root', 'turn': {'status': 'completed'}}})
        self.assertEqual([x['method'] for x in self.sent], ['thread/goal/set', 'turn/start'])
        self.assertEqual(self.store.get(self.rid)['goal']['status'], 'active')

    def test_goal_mismatch_or_error_never_sends_objective(self):
        for receipt in ({'id': 4, 'result': {'goal': self.goal(thread='child')}},
                        {'id': 4, 'result': {'goal': dict(self.goal(), objective='different')}},
                        {'id': 4, 'error': {'message': 'denied'}}):
            with self.subTest(receipt=receipt):
                self.sent.clear()
                self.ctrl.clear()
                self.ctrl.update(protocol='app-server', control_lock=threading.RLock())
                self.run['operation'] = 'goal'
                self.bind()
                with self.assertRaises(ValueError):
                    self.event(receipt)
                self.assertEqual([x['method'] for x in self.sent], ['thread/goal/set'])

    def test_resume_reconciles_goal_before_starting_input(self):
        self.bind()
        self.assertEqual(self.sent, [{'id': 5, 'method': 'thread/goal/get', 'params': {'threadId': 'root'}}])
        self.event({'id': 5, 'result': {'goal': self.goal()}})
        self.assertEqual([x['method'] for x in self.sent], ['thread/goal/get', 'turn/start'])
        self.assertEqual(self.store.get(self.rid)['goal'], self.goal())

    def test_child_and_unscoped_goal_events_cannot_change_root(self):
        self.event({'method': 'thread/goal/updated', 'params': {'threadId': 'root', 'goal': self.goal()}})
        for params in ({'threadId': 'child', 'goal': self.goal(thread='child')},
                       {'goal': self.goal()}, {'threadId': 'root', 'goal': self.goal(thread='child')}):
            self.event({'method': 'thread/goal/updated', 'params': params})
        self.event({'method': 'thread/goal/cleared', 'params': {'threadId': 'child'}})
        self.assertEqual(self.store.get(self.rid)['goal'], self.goal())
        self.assertEqual(len([e for e in self.store.events(self.rid) if e['event'] == 'goal']), 1)

    def test_resume_response_cannot_overwrite_newer_goal_notification(self):
        self.bind()
        self.event({'method': 'thread/goal/updated', 'params': {'threadId': 'root', 'goal': self.goal('complete')}})
        self.event({'id': 5, 'result': {'goal': self.goal()}})
        self.assertEqual(self.store.get(self.rid)['goal']['status'], 'complete')

    def test_only_active_goal_holds_process_past_idle_grace(self):
        for status in ('active', 'paused', 'blocked', 'usageLimited', 'budgetLimited', 'complete', None):
            with self.subTest(status=status):
                self.ctrl['turn_open'] = False
                method = 'thread/goal/updated' if status else 'thread/goal/cleared'
                self.event({'method': method, 'params': {'threadId': 'root', 'goal': self.goal(status)}})
                stopped = threading.Event()
                with patch.object(broker, 'IDLE_GRACE', .02), patch.object(self.owner, '_signal', side_effect=lambda *a: stopped.set()) as kill:
                    self.owner._settle(self.rid, self.ctrl)
                    self.assertEqual(stopped.wait(.1 if status == 'active' else 2), status != 'active')
                    self.assertEqual(kill.call_count, 0 if status == 'active' else 1)
                self.ctrl.pop('closing', None)

    def test_late_active_goal_invalidates_pending_idle_close(self):
        self.ctrl['turn_open'] = False
        with patch.object(broker, 'IDLE_GRACE', .1), patch.object(self.owner, '_signal') as kill:
            self.owner._settle(self.rid, self.ctrl)
            timer = self.ctrl['idle_timer']
            self.event({'method': 'thread/goal/updated', 'params': {'threadId': 'root', 'goal': self.goal()}})
            with patch.object(threading, 'current_thread', return_value=timer):
                self.owner._close_idle(self.rid, self.ctrl)
            kill.assert_not_called()

    def test_terminal_goal_keeps_unfinished_turn_and_children(self):
        self.event({'method': 'thread/goal/updated', 'params': {'threadId': 'root', 'goal': self.goal('complete')}})
        with patch.object(broker, 'IDLE_GRACE', .02), patch.object(self.owner, '_signal') as kill:
            self.owner._settle(self.rid, self.ctrl)
            time.sleep(.06)
            self.ctrl['turn_open'] = False
            self.ctrl['background'] = {'child': 'subagent'}
            self.owner._settle(self.rid, self.ctrl)
            time.sleep(.06)
            kill.assert_not_called()

    def run_synthetic_process(self, after_turn, abort_before_receipt=False):
        code = """import json,sys,time,select
def send(x): print(json.dumps(x),flush=True)
def read(): return json.loads(sys.stdin.readline())
assert read()['method']=='initialize'
send({'id':1,'result':{}})
assert read()['method']=='initialized'
probe=read()
assert probe['method']=='permissionProfile/list'
send({'id':probe['id'],'error':{'code':-32601,'message':'Method not found'}})
assert read()['method']=='thread/start'
send({'id':2,'result':{'thread':{'id':'root'}}})
request=read()
assert request['method']=='thread/goal/set'
if ABORT_BEFORE_RECEIPT: sys.exit(0)
goal={'threadId':'root','objective':'finish work','status':'active'}
send({'id':4,'result':{'goal':goal}})
assert read()['method']=='turn/start'
send({'method':'turn/started','params':{'threadId':'root','turn':{'id':'one'}}})
send({'method':'turn/completed','params':{'threadId':'root','turn':{'id':'one','status':'completed'}}})
""" + after_turn
        code = code.replace("ABORT_BEFORE_RECEIPT", repr(abort_before_receipt))
        spec = {'operation': 'goal', 'provider': 'codex', 'cwd': self.tmp.name, 'host_id': 'mac',
                'session_id': '', 'message': 'finish work', 'prompt': 'finish work', 'key': ''}
        launch = {'argv': [sys.executable, '-c', code], 'cwd': self.tmp.name,
                  'env': os.environ.copy(), 'protocol': 'app-server'}
        with patch.object(self.owner, '_launch', return_value=launch), patch.object(self.owner, '_persist'), \
             patch.object(broker.stream, 'INDEX', os.path.join(self.tmp.name, 'index.json')), \
             patch.object(broker.stream, 'CHATS', os.path.join(self.tmp.name, 'chats')), \
             patch.object(broker, 'IDLE_GRACE', .03):
            # The unit send spy must not replace transport in this process check.
            with patch.object(self.owner, '_send', broker.Broker._send.__get__(self.owner)):
                run = self.owner.start(spec)
                ctrl = self.owner.active[run['id']]
                try:
                    self.assertTrue(ctrl['finished'].wait(6), 'synthetic process did not settle')
                finally:
                    if not ctrl['finished'].is_set():
                        self.owner.control(run['id'], {'action': 'interrupt'})
                        ctrl['finished'].wait(3)
        return self.store.get(run['id']), self.store.events(run['id'])

    def test_real_process_survives_idle_and_native_continuation_without_client_prompt(self):
        result, events = self.run_synthetic_process("""
time.sleep(.25)
send({'method':'thread/goal/cleared','params':{'threadId':'child'}})
time.sleep(.1)
assert not select.select([sys.stdin],[],[],0)[0], 'broker invented a continuation prompt'
send({'method':'turn/started','params':{'threadId':'root','turn':{'id':'two'}}})
send({'method':'item/completed','params':{'threadId':'root','item':{'type':'agentMessage','text':'goal done'}}})
send({'method':'turn/completed','params':{'threadId':'root','turn':{'id':'two','status':'completed'}}})
goal['status']='complete'
send({'method':'thread/goal/updated','params':{'threadId':'root','goal':goal}})
for line in sys.stdin: pass
""")
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['goal']['status'], 'complete')
        self.assertEqual(result['text'], 'goal done')
        self.assertEqual([e['data']['status'] for e in events if e['event'] == 'goal'], ['active', 'complete'])

    def test_unexpected_provider_exit_does_not_report_active_goal_completed(self):
        result, events = self.run_synthetic_process("")
        self.assertEqual(result['status'], 'failed')
        self.assertIn('native goal was still active', result['error'])
        self.assertEqual(result['goal']['status'], 'active')

    def test_compact_error_keeps_native_goal_continuation_turn_open(self):
        self.event({'method': 'thread/goal/updated', 'params': {'threadId': 'root', 'goal': self.goal()}})
        self.ctrl.update(auto_compacting=True, compact_spent=True,
                         pending_rpc={'101': {'kind': 'auto_compact'}}, turn_id=None, turn_open=False)
        self.event({'method': 'turn/started', 'params': {
            'threadId': 'root', 'turn': {'id': 'native-continuation'}}})
        with patch.object(self.owner, '_deliver_next') as deliver, \
             patch.object(self.owner, 'policy', return_value={'auto_compact': False}):
            self.event({'id': 101, 'error': {'message': 'thread has an active turn'}})
            self.assertTrue(self.ctrl['turn_open'])
            self.assertEqual(self.ctrl['turn_id'], 'native-continuation')
            deliver.assert_not_called()
            self.event({'method': 'turn/completed', 'params': {
                'threadId': 'root', 'turn': {'id': 'native-continuation', 'status': 'completed'}}})
            deliver.assert_called_once()

    def test_provider_exit_before_goal_receipt_reports_ambiguous_failure(self):
        result, events = self.run_synthetic_process("", abort_before_receipt=True)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('before confirming native goal state', result['error'])
        self.assertNotIn('goal', result)
        self.assertFalse(any(e['event'] == 'goal' for e in events))


if __name__ == '__main__':
    unittest.main()
