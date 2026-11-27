"""Child agent activity comes from transcript evidence, not broker runs."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import agent_activity as aa


def write(path, records):
    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return str(path)


def assistant(reason, ts='2026-11-22T10:00:00Z'):
    return {'type': 'assistant', 'timestamp': ts, 'message': {'stop_reason': reason, 'content': [{'type': 'text', 'text': 'x'}]}}


def tool_result(ts='2026-11-22T10:00:01Z'):
    return {'type': 'user', 'timestamp': ts, 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't'}]}}


def notification(agent, status, ts='2026-11-22T10:05:00Z'):
    text = f'<task-notification>\n<task-id>{agent}</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n<status>{status}</status>\n<summary>s</summary>\n</task-notification>'
    return {'type': 'queue-operation', 'operation': 'enqueue', 'timestamp': ts, 'content': text}


def age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


class LiveLineageTests(unittest.TestCase):
    def test_nested_agent_keeps_parent_when_native_ids_arrive(self):
        agents = {}
        def tool(key, parent=None):
            return {'type': 'assistant', 'parent_tool_use_id': parent,
                    'message': {'content': [{'type': 'tool_use', 'name': 'Agent', 'id': key}]}}
        for event in (tool('outer'), tool('inner', 'outer'),
                      {'type': 'system', 'subtype': 'task_started', 'task_type': 'local_agent',
                       'task_id': 'outer-native', 'tool_use_id': 'outer'},
                      {'type': 'system', 'subtype': 'task_started', 'task_type': 'local_agent',
                       'task_id': 'inner-native', 'tool_use_id': 'inner'}):
            aa.observe_claude(agents, 'root', event)
        self.assertEqual(set(agents), {'outer-native', 'inner-native'})
        self.assertEqual(agents['inner-native']['parent_id'], 'outer-native')


class ClaudeChildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        aa._TAILS.clear(); aa._PARENTS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def case(self, child_records, parent_records, child_age=3600, live=None):
        child = write(self.root / 'agent-a1.jsonl', child_records)
        parent = write(self.root / 'root.jsonl', parent_records)
        age(child, child_age)
        meta = {'source': 'claude', 'is_child': True, 'agent_id': 'a1', 'parent_key': 'claude-root',
                'root_session_id': 'root', 'node': 'mac', 'src_sig': [[child, 0, 0]]}
        index = {'claude-root': {'source': 'claude', 'src_sig': [[parent, 0, 0]]}}
        return aa.child_activity(meta, index, live=live if live is not None else {})

    def test_end_turn_is_finished(self):
        self.assertEqual(self.case([tool_result(), assistant('end_turn')], [])['activity'], 'finished')

    def test_parent_notification_statuses(self):
        open_child = [assistant('tool_use'), tool_result()]
        for status, expected in (('completed', 'finished'), ('failed', 'failed'), ('killed', 'stopped'), ('stopped', 'stopped')):
            aa._TAILS.clear(); aa._PARENTS.clear()
            out = self.case(open_child, [notification('a1', status)])
            self.assertEqual(out['activity'], expected, status)
            self.assertIn(status, out['activity_evidence'])

    def test_notification_for_another_agent_is_ignored(self):
        self.assertEqual(self.case([assistant('tool_use')], [notification('b2', 'completed')])['activity'], 'unknown')

    def test_async_launch_alone_is_not_completion_and_fresh_write_is_running(self):
        launched = {'type': 'user', 'timestamp': '2026-11-22T10:00:00Z', 'toolUseResult': {'agentId': 'a1', 'status': 'async_launched', 'isAsync': True},
                    'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_1'}]}}
        out = self.case([assistant('tool_use'), tool_result()], [launched], child_age=5)
        self.assertEqual(out['activity'], 'running')

    def test_synchronous_result_is_finished(self):
        done = {'type': 'user', 'toolUseResult': {'agentId': 'a1', 'status': 'completed'}, 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_1'}]}}
        self.assertEqual(self.case([assistant('tool_use')], [done])['activity'], 'finished')

    def test_root_process_decides_quiet_children(self):
        live = {'fetched_at': time.time(), 'nodes': [{'name': 'mac', 'ok': True}],
                'sessions': [{'tool': 'claude', 'alive': True, 'session_id': 'root', 'node': 'mac'}]}
        self.assertEqual(self.case([assistant('tool_use')], [], live=live)['activity'], 'running')
        aa._TAILS.clear()
        live['sessions'] = []
        self.assertEqual(self.case([assistant('tool_use')], [], live=live)['activity'], 'stopped')

    def test_remote_root_process_decides_when_its_host_probe_is_current(self):
        def case(live):
            aa._TAILS.clear()
            child = write(self.root / 'agent-a1.jsonl', [assistant('tool_use')])
            age(child, 3600)
            meta = {'source': 'claude', 'is_child': True, 'agent_id': 'a1', 'root_session_id': 'root', 'node': 'sc', 'src_sig': [[child, 0, 0]]}
            return aa.child_activity(meta, {}, live=live)['activity']
        row = {'tool': 'claude', 'alive': True, 'session_id': 'root', 'node': 'sc'}
        live = {'nodes': [{'name': 'mac', 'ok': True}, {'name': 'sc', 'ok': True}], 'sessions': [row]}
        self.assertEqual(case(live), 'running')
        self.assertEqual(case(dict(live, sessions=[])), 'stopped')
        self.assertEqual(case(dict(live, nodes=[{'name': 'sc', 'ok': True, 'stale_since': 1}])), 'unknown')
        self.assertEqual(case(dict(live, nodes=[{'name': 'mac', 'ok': True}])), 'unknown')

    def test_parent_scan_is_incremental(self):
        parent = self.root / 'p.jsonl'
        write(parent, [notification('a1', 'completed')])
        self.assertEqual(aa.parent_reports(str(parent))['a1'][0], 'completed')
        with open(parent, 'a') as fh:
            fh.write(json.dumps(notification('a2', 'failed')) + '\n')
            fh.write('{"type": "queue-operation", "partial')  # still being written
        reports = aa.parent_reports(str(parent))
        self.assertEqual(reports['a2'][0], 'failed')
        self.assertEqual(aa._PARENTS[str(parent)]['offset'], parent.stat().st_size - len('{"type": "queue-operation", "partial'))


class CodexChildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        aa._TAILS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def case(self, events, child_age, live=None):
        path = write(Path(self.tmp.name) / 'rollout.jsonl', [{'type': 'session_meta', 'payload': {}}] +
                     [{'type': 'event_msg', 'timestamp': '2026-11-22T10:00:00Z', 'payload': e} for e in events])
        age(path, child_age)
        meta = {'source': 'codex', 'is_child': True, 'node': 'mac', 'src_sig': [[path, 0, 0]]}
        return aa.child_activity(meta, {}, live=live if live is not None else {})['activity']

    def test_lifecycle(self):
        self.assertEqual(self.case([{'type': 'task_started'}, {'type': 'task_complete'}], 9999), 'finished')
        aa._TAILS.clear()
        self.assertEqual(self.case([{'type': 'task_started'}, {'type': 'task_complete', 'error': {'m': 1}}], 9999), 'failed')
        aa._TAILS.clear()
        self.assertEqual(self.case([{'type': 'task_started'}, {'type': 'turn_aborted'}], 9999), 'stopped')
        aa._TAILS.clear()
        self.assertEqual(self.case([{'type': 'task_complete'}, {'type': 'task_started'}], 10), 'running')
        aa._TAILS.clear()
        self.assertEqual(self.case([{'type': 'task_started'}], 9999), 'unknown')

    def test_no_codex_process_means_stopped(self):
        live = {'fetched_at': time.time(), 'nodes': [{'name': 'mac', 'ok': True}], 'sessions': []}
        self.assertEqual(self.case([{'type': 'task_started'}], 9999, live=live), 'stopped')

    def test_subagent_model_is_from_its_latest_turn_without_usage(self):
        path = write(Path(self.tmp.name) / 'rollout.jsonl', [
            {'type': 'turn_context', 'payload': {'model': 'gpt-old-child'}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-new-child'}},
        ])
        self.assertEqual(aa.child_model({'source': 'codex', 'src_sig': [[path]], 'model_main': 'gpt-old-child'}),
                         'gpt-new-child')

    def test_claude_model_ignores_synthetic_messages_and_parent_model(self):
        path = write(Path(self.tmp.name) / 'claude.jsonl', [
            {'type': 'assistant', 'message': {'model': 'claude-child'}},
            {'type': 'assistant', 'message': {'model': '<synthetic>'}},
        ])
        self.assertEqual(aa.child_model({'source': 'claude', 'src_sig': [[path]], 'model_main': 'claude-old'}),
                         'claude-child')
        self.assertEqual(aa.child_model({'source': 'claude', 'model_main': '<synthetic>'}), '')


if __name__ == '__main__':
    unittest.main()
