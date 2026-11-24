import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import broker


class RunStoreDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = broker.RunStore(self.tmp.name)
        self.run = self.store.create({'provider': 'codex', 'key': 'codex-native',
                                     'session_id': 'native', 'prompt': 'first prompt',
                                     'nested': {'value': [1]}})
        self.rid = self.run['id']

    def test_reads_use_memory_and_nested_values_are_private(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('disk read')), patch.object(Path, 'stat', side_effect=AssertionError('stat')):
            value = self.store.get(self.rid)
            value['nested']['value'].append(2)
            self.assertEqual(self.store.list()[0]['nested'], {'value': [1]})
            self.assertEqual(self.store.find_request(None), None)

    def test_snapshot_batching_keeps_journal_durable_and_replays_after_restart(self):
        self.store.update(self.rid, status='running')
        with patch.object(broker.stream, '_jsave', wraps=broker.stream._jsave) as save:
            for _ in range(30):
                self.store.emit(self.rid, 'delta', {'text': 'x'})
            self.assertLessEqual(save.call_count, 1)
        self.assertEqual(self.store.get(self.rid)['text'], 'x' * 30)
        reopened = broker.RunStore(self.tmp.name)
        self.assertEqual(reopened.get(self.rid)['text'], 'x' * 30)
        self.store.update(self.rid, status='completed')
        persisted = json.loads((self.store._dir(self.rid) / 'run.json').read_text())
        self.assertEqual(persisted['text'], 'x' * 30)
        self.assertEqual(persisted['last_seq'], 31)

    def test_initial_tail_is_bounded_and_can_page_older_events(self):
        for i in range(2010):
            self.store.emit(self.rid, 'tool', {'number': i})
        with patch.object(broker, 'instance') as current:
            current.return_value.store = self.store
            result = broker.get_run(None, {'id': self.rid, 'after': '0'}, {})
            self.assertEqual(len(result['events']), 2000)
            first = result['events'][0]['seq']
            self.assertEqual(result['events_truncated_before'], first)
            older = broker.get_run(None, {'id': self.rid, 'before': str(first)}, {})
            self.assertEqual(older['events'][-1]['seq'], first - 1)
            self.assertIsNone(older.get('events_truncated_before'))

    def test_restart_indexes_journal_once_and_later_cursor_reads_only_tail(self):
        for i in range(10):
            self.store.emit(self.rid, 'tool', {'number': i})
        reopened = broker.RunStore(self.tmp.name)
        reopened.events(self.rid, after=5)
        with patch.object(broker.json, 'loads', wraps=broker.json.loads) as loads:
            self.assertEqual([e['seq'] for e in reopened.events(self.rid, after=10)], [11])
            self.assertLessEqual(loads.call_count, 1)
        reopened.emit(self.rid, 'tool', {'number': 10})
        self.assertEqual([e['seq'] for e in reopened.events(self.rid, after=11)], [12])

    def test_summary_filter_retains_identity_but_omits_large_text(self):
        self.store.update(self.rid, text='large answer')
        self.store.create({'provider': 'claude', 'key': 'claude-other'})
        with patch.object(broker, 'instance') as current:
            current.return_value.store = self.store
            rows = broker.list_runs(None, {'key': 'codex-native'}, {})['runs']
        self.assertEqual([r['id'] for r in rows], [self.rid])
        self.assertNotIn('text', rows[0])
        self.assertNotIn('prompt', rows[0])
        self.assertEqual(self.store.get(self.rid)['text'], 'large answer')

    def test_waiters_are_run_specific_and_no_change_is_missed(self):
        other = self.store.create({'provider': 'claude'})['id']
        seq = self.store.get(self.rid)['last_seq']
        outcome = []
        ready = threading.Event()
        def wait():
            ready.set()
            outcome.append(self.store.wait_for_change(self.rid, seq, timeout=.1))
        thread = threading.Thread(target=wait); thread.start(); ready.wait(1)
        self.store.emit(other, 'delta', {'text': 'unrelated'})
        thread.join(1)
        self.assertEqual(outcome, [False])
        before = self.store.revision
        self.store.emit(self.rid, 'delta', {'text': 'owned'})
        self.assertTrue(self.store.wait_for_change(self.rid, seq, timeout=0))
        self.assertNotEqual(self.store.revision, before)

    def test_initial_tail_and_snapshot_are_atomic_against_completion(self):
        original = self.store.events
        attempts = threading.Event(); finished = threading.Event(); workers = []
        def complete():
            attempts.set()
            self.store.emit(self.rid, 'delta', {'text': 'final answer'})
            self.store.update(self.rid, status='completed')
            finished.set()
        def interleave(*args, **kwargs):
            rows = original(*args, **kwargs)
            worker = threading.Thread(target=complete); workers.append(worker); worker.start()
            self.assertTrue(attempts.wait(1))
            self.assertFalse(finished.wait(.02), 'completion escaped the response snapshot lock')
            return rows
        try:
            with patch.object(broker, 'instance') as current, patch.object(self.store, 'events', side_effect=interleave):
                current.return_value.store = self.store
                response = broker.get_run(None, {'id': self.rid}, {})
            self.assertEqual(response['run']['last_seq'], response['events'][-1]['seq'])
            self.assertEqual(response['run']['status'], 'queued')
        finally:
            for worker in workers: worker.join(2)
        self.assertTrue(finished.is_set())

    def test_partial_crash_tail_does_not_swallow_the_next_receipt(self):
        path = self.store._dir(self.rid) / 'events.jsonl'
        with path.open('ab') as handle:
            handle.write(b'{"seq":2,"event":')
        self.store.emit(self.rid, 'tool', {'name': 'preserved'})
        rows = self.store.events(self.rid)
        self.assertEqual([r['seq'] for r in rows], [1, 2])
        self.assertEqual(rows[-1]['data']['name'], 'preserved')

    def test_only_duplicate_provider_journal_is_trimmed(self):
        text = 'evidence' * 20000
        event = {'provider': 'claude', 'event': {'type': 'assistant', 'message': text}}
        self.store.emit(self.rid, 'provider_event', event)
        self.store.emit(self.rid, 'tool_result', {'content': text})
        self.store.emit(self.rid, 'delta', {'text': text})
        rows = self.store.events(self.rid)
        self.assertTrue(rows[1]['data']['journal_truncated'])
        self.assertLess(len(json.dumps(rows[1])), 66000)
        self.assertEqual(rows[2]['data']['content'], text)
        self.assertEqual(self.store.get(self.rid)['text'], text)
        self.assertEqual(event['event']['message'], text)

    def test_startup_steer_cannot_precede_initial_prompt(self):
        controller = broker.Broker(self.store)
        self.store.update(self.rid, status='running')
        ctrl = {'protocol': 'app-server', 'pending_rpc': {}, 'rpc_seq': 100}
        controller.active[self.rid] = ctrl
        with patch.object(controller, '_send') as send:
            with self.assertRaisesRegex(ValueError, 'Codex is still starting'):
                controller.control(self.rid, {'action': 'steer', 'message': 'too early'})
            send.assert_not_called()
            controller._codex_begin(self.store.get(self.rid), ctrl, 'native')
            self.assertTrue(ctrl['begun'])
            self.assertEqual(send.call_args.args[1]['params']['input'][0]['text'], 'first prompt')


if __name__ == '__main__':
    unittest.main()
