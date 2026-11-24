"""Queued follow-ups, automatic approvals and open decisions, and auto-compaction.

Fake provider processes only; no real Claude or Codex session is started."""
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import attention_watch
import broker

CLAUDE = '''import json,sys,time,pathlib
LOG = pathlib.Path(%(log)r)
GO = pathlib.Path(%(go)r)
def send(x): print(json.dumps(x), flush=True)
def read():
    m = json.loads(sys.stdin.readline())
    with LOG.open('a') as fh: fh.write(json.dumps(m) + '\\n')
    return m
def text(m): return m['message']['content'][0]['text']
def replay(m): send({'type':'user','isReplay':True,'session_id':'s','message':m['message']})
def wait():
    while not GO.exists(): time.sleep(.02)
    GO.unlink()
assert read()['request']['subtype']=='initialize'
send({'type':'control_response','response':{'request_id':'initialize','subtype':'success'}})
replay(read())
'''

CODEX = '''import json,sys,time,pathlib
LOG = pathlib.Path(%(log)r)
GO = pathlib.Path(%(go)r)
def get():
    m = json.loads(sys.stdin.readline())
    with LOG.open('a') as fh: fh.write(json.dumps(m) + '\\n')
    return m
def send(x): print(json.dumps(x), flush=True)
def wait():
    while not GO.exists(): time.sleep(.02)
    GO.unlink()
assert get()['method']=='initialize'
send({'id':1,'result':{}})
assert get()['method']=='initialized'
probe=get()
assert probe['method']=='permissionProfile/list'
send({'id':probe['id'],'error':{'code':-32601,'message':'Method not found'}})
assert get()['method']=='thread/start'
send({'id':2,'result':{'thread':{'id':'native'}}})
assert get()['method']=='turn/start'
send({'id':3,'result':{'turn':{'id':'t1'}}})
send({'method':'turn/started','params':{'threadId':'native','turn':{'id':'t1'}}})
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = broker.RunStore(self.root / 'execution')
        self.config = self.root / 'config.json'
        self.config.write_text('{}')
        self.log = self.root / 'stdin.jsonl'
        self.go = self.root / 'go'
        self.controller = broker.Broker(self.store)
        self.controller.config_path = self.config
        self.patches = [patch.object(broker, 'IDLE_GRACE', .3),
                        patch.object(broker.stream, 'INDEX', str(self.root / 'index-guard.json')),
                        patch.object(broker.stream, 'CHATS', str(self.root / 'chats'))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for rid, ctrl in list(self.controller.active.items()):
            if self.store.get(rid)['status'] not in broker.TERMINAL:
                try:
                    self.controller.control(rid, {'action': 'interrupt'})
                except ValueError:
                    pass
            ctrl['finished'].wait(8)
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def start(self, template, body, protocol='claude-stream', key='chat-1'):
        code = template % {'log': str(self.log), 'go': str(self.go)} + body
        spec = {'provider': 'codex' if protocol == 'app-server' else 'claude', 'cwd': self.tmp.name, 'host_id': 'mac',
                'session_id': '', 'prompt': 'hello', 'message': 'hello', 'key': key, 'conversation_key': key}
        launch = {'argv': [sys.executable, '-c', code], 'cwd': self.tmp.name, 'env': os.environ.copy(), 'protocol': protocol}
        self.persist = patch.object(self.controller, '_persist')
        self.persist.start()
        self.addCleanup(self.persist.stop)
        with patch.object(self.controller, '_launch', return_value=launch):
            run = self.controller.start(spec)
        return run['id'], self.controller.active[run['id']]

    def events(self, rid, name=None):
        return [e for e in self.store.events(rid) if name is None or e['event'] == name]

    def wait_for(self, predicate, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(.02)
        return False

    def sent(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def sent_texts(self):
        return [m['message']['content'][0]['text'] for m in self.sent() if m.get('type') == 'user']

    def enqueue(self, text, **extra):
        return self.controller.queue_action(dict({'action': 'enqueue', 'key': 'chat-1', 'message': text}, **extra))


class QueueTests(Base):
    def test_claude_queue_delivers_in_order_at_turn_end_never_mid_turn(self):
        rid, ctrl = self.start(CLAUDE, '''
send({'type':'assistant','session_id':'s','message':{'content':[{'type':'tool_use','id':'t1','name':'Bash','input':{}}]}})
wait()
send({'type':'result','session_id':'s','result':'first'})
for n in range(2):
    m = read(); replay(m)
    send({'type':'result','session_id':'s','result':'reply to ' + text(m)})
for line in sys.stdin: pass
''')
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'provider_event')))
        first = self.enqueue('one', request_id='r1')['item']
        self.assertEqual(self.enqueue('one again', request_id='r1')['item']['id'], first['id'], 'request_id is idempotent')
        self.enqueue('two')
        time.sleep(.3)
        self.assertEqual(self.sent_texts(), ['hello'], 'nothing is written mid turn')
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        self.assertEqual(self.sent_texts(), ['hello', 'one', 'two'])
        view = self.controller.queue_view('chat-1', all_items=True)
        self.assertEqual([(i['text'], i['state']) for i in view['items']], [('one', 'delivered'), ('two', 'delivered')])
        delivered = [e['data']['change']['item']['text'] for e in self.events(rid, 'queue') if e['data']['change']['state'] == 'delivered']
        self.assertEqual(delivered, ['one', 'two'])
        self.assertEqual(self.store.get(rid)['status'], 'completed')
        self.assertIn('reply to two', self.store.get(rid)['text'])

    def test_edit_move_cancel_and_promote_to_steer(self):
        rid, ctrl = self.start(CLAUDE, '''
send({'type':'assistant','session_id':'s','message':{'content':[{'type':'tool_use','id':'t1','name':'Bash','input':{}}]}})
replay(read())  # the promoted steer, read at the tool step
wait()
send({'type':'result','session_id':'s','result':'first'})
for n in range(2):
    m = read(); replay(m)
    send({'type':'result','session_id':'s','result':'ok'})
for line in sys.stdin: pass
''')
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'provider_event')))
        a, b, c, d = (self.enqueue(t)['item'] for t in ('a', 'b', 'c', 'd'))
        edited = self.controller.queue_action({'action': 'edit', 'key': 'chat-1', 'item_id': b['id'], 'message': 'b2', 'version': b['version']})
        with self.assertRaises(broker.QueueError) as stale:
            self.controller.queue_action({'action': 'edit', 'key': 'chat-1', 'item_id': b['id'], 'message': 'x', 'version': b['version']})
        self.assertEqual(stale.exception.code, 'queue_conflict')
        self.assertEqual(edited['item']['text'], 'b2')
        self.controller.queue_action({'action': 'move', 'key': 'chat-1', 'item_id': c['id'], 'index': 0})
        cancelled = self.controller.queue_action({'action': 'cancel', 'key': 'chat-1', 'item_id': a['id']})
        self.assertEqual((cancelled['item']['state'], cancelled['item']['text']), ('cancelled', 'a'))
        steered = self.controller.queue_action({'action': 'steer', 'key': 'chat-1', 'item_id': d['id']})
        self.assertEqual(steered['item']['state'], 'steered')
        self.assertEqual([i['text'] for i in steered['queue']['items']], ['c', 'b2'])
        with self.assertRaises(broker.QueueError) as gone:
            self.controller.queue_action({'action': 'cancel', 'key': 'chat-1', 'item_id': d['id']})
        self.assertEqual(gone.exception.code, 'queue_item_resolved')
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'steered')))
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        self.assertEqual(self.sent_texts(), ['hello', 'd', 'c', 'b2'])
        self.assertEqual([e['data']['message'] for e in self.events(rid, 'steered')], ['d'])

    def test_process_exit_keeps_queue_and_reports_unread_item(self):
        rid, ctrl = self.start(CLAUDE, '''
wait()
send({'type':'result','session_id':'s','result':'done, exiting without reading more'})
''')
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'provider_event')))
        self.enqueue('lost?')
        self.enqueue('kept')
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        view = self.controller.queue_view('chat-1')
        self.assertEqual([(i['text'], i['state']) for i in view['items']], [('lost?', 'failed'), ('kept', 'queued')])
        self.assertTrue(view['held'])
        self.assertTrue(view['items'][0]['error'])
        with self.assertRaises(broker.QueueError) as none:
            self.enqueue('after exit')
        self.assertEqual(none.exception.code, 'no_active_run')
        retried = self.controller.queue_action({'action': 'edit', 'key': 'chat-1', 'item_id': view['items'][0]['id'], 'message': 'lost? retry'})
        self.assertEqual(retried['item']['state'], 'queued')

    def test_server_restart_with_non_empty_queue_and_deliver(self):
        qkey = 'chat-1'
        controller = self.controller
        controller._queue_save(qkey, [
            {'id': 'q_a', 'key': qkey, 'text': 'in flight', 'attachments': [], 'state': 'delivering', 'version': 1},
            {'id': 'q_b', 'key': qkey, 'text': 'waiting', 'attachments': [], 'state': 'queued', 'version': 1}])
        restarted = broker.Broker(self.store)
        view = restarted.queue_view(qkey)
        self.assertEqual([i['state'] for i in view['items']], ['failed', 'queued'])
        self.assertIn('restarted', view['items'][0]['error'])
        self.assertTrue(view['held'])
        with patch.object(broker, 'instance', return_value=restarted), \
                patch.object(broker, 'create_run', return_value={'ok': True, 'run': {'id': 'run-x'}}) as create:
            result = broker.queue_post(None, {}, {'action': 'deliver', 'key': qkey, 'model': 'm'})
        self.assertEqual(create.call_args[0][2]['message'], 'waiting')
        self.assertEqual(create.call_args[0][2]['request_id'], 'queue-q_b')
        self.assertEqual((result['item']['state'], result['item']['run_id']), ('delivered', 'run-x'))
        self.assertEqual([i['text'] for i in result['queue']['items']], ['in flight'])

    def test_two_clients_enqueue_at_once(self):
        rid, ctrl = self.start(CLAUDE, '''
wait()
send({'type':'result','session_id':'s','result':'first'})
for line in sys.stdin:
    m = json.loads(line)
    with LOG.open('a') as fh: fh.write(line)
    replay(m); send({'type':'result','session_id':'s','result':'ok'})
''')
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'provider_event')))
        threads = [threading.Thread(target=self.enqueue, args=(f'm{n}',), kwargs={'client_id': f'c{n % 2}'}) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        items = self.controller.queue_view('chat-1')['items']
        self.assertEqual(sorted(i['text'] for i in items), sorted(f'm{n}' for n in range(20)))
        self.assertEqual(len({i['id'] for i in items}), 20)
        self.go.touch()
        self.assertTrue(self.wait_for(lambda: len(self.sent_texts()) == 21, 15))
        self.assertEqual(self.sent_texts()[1:], [i['text'] for i in items])

    def test_enqueue_between_turns_delivers_at_once(self):
        rid, ctrl = self.start(CLAUDE, '''
send({'type':'result','session_id':'s','result':'first'})
for line in sys.stdin:
    m = json.loads(line)
    with LOG.open('a') as fh: fh.write(line)
    replay(m); send({'type':'result','session_id':'s','result':'ok'})
''')
        self.assertTrue(self.wait_for(lambda: any(e['data'].get('phase') == 'idle' for e in self.events(rid, 'state'))))
        self.assertEqual(self.enqueue('now')['item']['state'], 'delivering')
        self.assertTrue(ctrl['finished'].wait(8))
        self.assertEqual(self.sent_texts(), ['hello', 'now'])

    def test_codex_queue_starts_next_turn_and_promotes_with_native_steer(self):
        rid, ctrl = self.start(CODEX, '''
wait()
m = get(); assert m['method'] == 'turn/steer' and m['params']['expectedTurnId'] == 't1', m
send({'id': m['id'], 'result': {'turnId': 't1'}})
send({'method':'turn/completed','params':{'threadId':'native','turn':{'id':'t1','status':'completed'}}})
m = get(); assert m['method'] == 'turn/start', m
send({'id': m['id'], 'result': {'turn': {'id': 't2'}}})
send({'method':'turn/started','params':{'threadId':'native','turn':{'id':'t2'}}})
send({'method':'turn/completed','params':{'threadId':'native','turn':{'id':'t2','status':'completed'}}})
time.sleep(3)
''', protocol='app-server')
        self.assertTrue(self.wait_for(lambda: ctrl.get('turn_id') == 't1'))
        promoted = self.enqueue('steer me')['item']
        self.enqueue('next turn')
        self.controller.queue_action({'action': 'steer', 'key': 'chat-1', 'item_id': promoted['id']})
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        texts = [m['params']['input'][0]['text'] for m in self.sent() if m.get('method') in ('turn/steer', 'turn/start')]
        self.assertEqual(texts, ['hello', 'steer me', 'next turn'])
        states = {i['text']: i['state'] for i in self.controller.queue_view('chat-1', all_items=True)['items']}
        self.assertEqual(states, {'steer me': 'steered', 'next turn': 'delivered'})
        self.assertEqual(self.store.get(rid)['status'], 'completed', self.store.get(rid).get('error'))


class DecisionTests(Base):
    def claude(self, rid, ctrl, request_id, tool, data):
        self.controller._claude_control(rid, self.store.get(rid), ctrl, {'type': 'control_request', 'request_id': request_id,
                                        'request': {'subtype': 'can_use_tool', 'tool_name': tool, 'input': data}})

    def test_default_policy_never_pauses_and_lists_open_decisions(self):
        run = self.store.create({'provider': 'claude', 'session_id': 's', 'cwd': '/w', 'key': 'k'})
        ctrl, sent = {'protocol': 'claude-stream'}, []
        with patch.object(self.controller, '_send', side_effect=lambda c, d: sent.append(d)):
            self.claude(run['id'], ctrl, 'a1', 'Bash', {'command': 'make test'})
            self.claude(run['id'], ctrl, 'a2', 'ExitPlanMode', {})
            self.claude(run['id'], ctrl, 'q1', 'AskUserQuestion', {'questions': [{'question': 'Which dataset?', 'options': [{'label': 'A'}]}]})
            codex = {'protocol': 'app-server'}
            codex_run = self.store.get(run['id'])
            st = broker.stream._State('s')
            self.controller._codex_rpc(run['id'], codex_run, st, codex, {'id': 7, 'method': 'item/tool/requestUserInput',
                                       'params': {'questions': [{'id': 'q', 'header': 'h', 'question': 'Ship it?'}]}})
            self.controller._codex_rpc(run['id'], codex_run, st, codex, {'id': 8, 'method': 'item/permissions/requestApproval',
                                       'params': {'reason': 'needs network', 'permissions': {}}})
        self.assertEqual(ctrl.get('pending_requests') or {}, {})
        self.assertEqual(codex.get('pending_requests') or {}, {})
        self.assertEqual([d['response']['response']['behavior'] for d in sent[:3]], ['allow', 'allow', 'deny'])
        self.assertEqual(sent[2]['response']['response']['message'], broker.DEFER_ANSWER)
        self.assertEqual(sent[3], {'id': 7, 'result': {'answers': {'q': {'answers': [broker.DEFER_ANSWER]}}}})
        self.assertIn('error', sent[4], 'sandbox expansion is never granted automatically')
        report = self.controller.decisions(run['id'])
        self.assertEqual([(i['kind'], i['resolution'], i['open']) for i in report['items']],
                         [('approval', 'auto_accepted', False), ('approval', 'auto_accepted', False),
                          ('question', 'deferred', True), ('question', 'deferred', True), ('request', 'declined', True)])
        self.assertEqual(report['items'][0]['summary'], 'make test')
        self.assertEqual(report['open_count'], 3)
        self.assertEqual(self.events(run['id'], 'human_required'), [])

    def test_workspace_rule_asks_and_emits_human_required(self):
        self.config.write_text(json.dumps({'workspace_rules': [{'path': '/w/strict', 'auto_approve_tools': False, 'agent_questions': 'ask'}]}))
        run = self.store.create({'provider': 'claude', 'session_id': 's', 'cwd': '/w/strict/sub', 'key': 'k'})
        ctrl, sent = {'protocol': 'claude-stream'}, []
        with patch.object(self.controller, '_send', side_effect=lambda c, d: sent.append(d)):
            self.claude(run['id'], ctrl, 'a1', 'Bash', {'command': 'rm -r build'})
            self.claude(run['id'], ctrl, 'q1', 'AskUserQuestion', {'questions': [{'question': 'Which?'}]})
        self.assertEqual(sent, [])
        self.assertEqual(set(ctrl['pending_requests']), {'a1', 'q1'})
        required = [e['data'] for e in self.events(run['id'], 'human_required')]
        self.assertEqual([(r['kind'], r['reason']) for r in required], [('approval', 'auto_approve_off'), ('question', 'questions_set_to_ask')])
        payload = self.events(run['id'], 'approval')[0]['data']
        self.assertFalse(attention_watch.supported_approval({'kind': 'approval', 'payload': payload}),
                         'the watcher must not override the workspace rule')
        self.assertEqual(self.controller.decisions(run['id'])['open_count'], 2)
        self.assertEqual(self.controller.policy('/w/other')['auto_approve_tools'], True)


class CompactionTests(Base):
    BODY = '''
send({'type':'assistant','session_id':'s','parent_tool_use_id':None,'message':{'model':'m','content':[{'type':'text','text':'x'}],
      'usage':{'input_tokens':10,'cache_creation_input_tokens':290,'cache_read_input_tokens':300}}})
wait()
send({'type':'result','session_id':'s','result':'first','usage':{'output_tokens':5},'modelUsage':{'m':{'contextWindow':1000}}})
for line in sys.stdin:
    m = json.loads(line)
    with LOG.open('a') as fh: fh.write(json.dumps(m) + '\\n')
    replay(m)
    if text(m) == '/compact':
        send({'type':'system','subtype':'compact_boundary','session_id':'s','compact_metadata':{'trigger':'manual','pre_tokens':600,'post_tokens':100}})
        send({'type':'result','session_id':'s','usage':{'output_tokens':0},'modelUsage':{'m':{'contextWindow':1000}}})
    else:
        send({'type':'result','session_id':'s','result':'after','modelUsage':{'m':{'contextWindow':1000}}})
'''

    def test_claude_compacts_once_above_half_then_delivers_queue(self):
        rid, ctrl = self.start(CLAUDE, self.BODY)
        self.assertTrue(self.wait_for(lambda: self.events(rid, 'provider_event')))
        self.enqueue('queued after compaction')
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        self.assertEqual(self.sent_texts(), ['hello', '/compact', 'queued after compaction'])
        auto = self.events(rid, 'auto_compact')
        self.assertEqual(len(auto), 1)
        self.assertEqual((auto[0]['data']['context_tokens'], auto[0]['data']['context_window']), (600, 1000))
        self.assertEqual([e['data'].get('trigger') for e in self.events(rid, 'compacted')], ['auto'])
        final = self.store.get(rid)
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['context']['tokens'], 100)

    def test_setting_turns_compaction_off(self):
        self.config.write_text(json.dumps({'auto_compact': {'enabled': False}}))
        rid, ctrl = self.start(CLAUDE, self.BODY)
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        self.assertEqual(self.sent_texts(), ['hello'])
        self.assertEqual(self.events(rid, 'auto_compact'), [])
        self.assertEqual(self.store.get(rid)['context']['ratio'], .6)

    def test_pending_steer_postpones_compaction(self):
        run = self.store.create({'provider': 'claude', 'cwd': '/w', 'key': 'k'})
        ctrl = {'protocol': 'claude-stream', 'context': {'tokens': 900, 'window': 1000},
                'claude_inputs': [{'wire': 'steer', 'results': 1}]}
        with patch.object(self.controller, '_send') as send:
            self.controller._after_turn(run['id'], run, ctrl)
        send.assert_not_called()

    def test_codex_compacts_through_app_server_before_queue(self):
        rid, ctrl = self.start(CODEX, '''
send({'method':'thread/tokenUsage/updated','params':{'threadId':'native','turnId':'t1','tokenUsage':{'last':{'totalTokens':700,'inputTokens':650,'cachedInputTokens':0,'outputTokens':50,'reasoningOutputTokens':0},'total':{'totalTokens':700,'inputTokens':650,'cachedInputTokens':0,'outputTokens':50,'reasoningOutputTokens':0},'modelContextWindow':1000}}})
wait()
send({'method':'turn/completed','params':{'threadId':'native','turn':{'id':'t1','status':'completed'}}})
m = get(); assert m['method'] == 'thread/compact/start' and m['params'] == {'threadId': 'native'}, m
send({'id': m['id'], 'result': {}})
send({'method':'thread/compacted','params':{'threadId':'native','turnId':'t1'}})
m = get(); assert m['method'] == 'turn/start', m
send({'id': m['id'], 'result': {'turn': {'id': 't2'}}})
send({'method':'turn/started','params':{'threadId':'native','turn':{'id':'t2'}}})
send({'method':'turn/completed','params':{'threadId':'native','turn':{'id':'t2','status':'completed'}}})
time.sleep(3)
''', protocol='app-server')
        self.assertTrue(self.wait_for(lambda: ctrl.get('turn_id') == 't1'))
        self.enqueue('after codex compaction')
        self.go.touch()
        self.assertTrue(ctrl['finished'].wait(8))
        methods = [m.get('method') for m in self.sent() if m.get('method') in ('thread/compact/start', 'turn/start')]
        self.assertEqual(methods, ['turn/start', 'thread/compact/start', 'turn/start'])
        self.assertEqual([e['data'].get('trigger') for e in self.events(rid, 'compacted')], ['auto'])
        self.assertEqual(self.store.get(rid)['status'], 'completed', self.store.get(rid).get('error'))

    def test_idle_compaction_is_opt_in_and_runs_once_per_finished_run(self):
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)).isoformat()
        run = self.store.create({'provider': 'claude', 'key': 'claude-a', 'cwd': self.tmp.name, 'host_id': 'mac'})
        self.store.update(run['id'], status='completed', finished_at=old, context={'tokens': 700, 'window': 1000})
        index = self.root / 'index.json'
        index.write_text(json.dumps({'claude-a': {'source': 'claude', 'id': 'sess', 'cwd': self.tmp.name}}))
        with patch.object(broker.stream, 'INDEX', str(index)), \
                patch.object(self.controller, 'start', return_value={'id': 'compact-run'}) as start:
            self.assertEqual(self.controller.idle_compactions(), [], 'off by default')
            self.config.write_text(json.dumps({'auto_compact': {'idle_minutes': 60}}))
            self.assertEqual(self.controller.idle_compactions(), [], 'not idle long enough')
            self.config.write_text(json.dumps({'auto_compact': {'idle_minutes': 20}}))
            self.assertEqual(self.controller.idle_compactions(), ['compact-run'])
        spec = start.call_args[0][0]
        self.assertEqual((spec['operation'], spec['message'], spec['request_id']), ('compact', '/compact', 'idle-compact-' + run['id']))


if __name__ == '__main__':
    unittest.main()
