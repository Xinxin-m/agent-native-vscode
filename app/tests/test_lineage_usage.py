import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import indexer
import usage_history


class TranscriptTests(unittest.TestCase):
    def write(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            for row in rows:
                fh.write(json.dumps(row) + '\n')
        return path

    def test_codex_child_keeps_own_id_and_explicit_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp + '/child.jsonl', [
                {'type': 'session_meta', 'payload': {'id': 'child', 'session_id': 'root', 'parent_thread_id': 'parent', 'creator_account_id': 'acct', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'parent', 'agent_path': '/root/a/b'}}}}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'done'}]}},
            ])
            self.assertEqual(indexer.codex_session_id(path), 'child')
            chat = indexer.parse_codex_group('child', [path], {})
            self.assertEqual(chat['parent_key'], 'codex-parent')
            self.assertEqual(chat['root_key'], 'codex-root')
            self.assertEqual(chat['provider_account_id'], 'acct')

    def test_claude_child_sidechain_transcript_and_tool_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.write(tmp + '/project/root.jsonl', [
                {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'call1', 'name': 'Agent', 'input': {'description': 'Review code', 'subagent_type': 'Explore'}}]}},
                {'type': 'user', 'toolUseResult': {'agentId': 'a123'}, 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'call1'}]}},
            ])
            child = self.write(tmp + '/project/root/subagents/agent-a123.jsonl', [
                {'type': 'user', 'isSidechain': True, 'sessionId': 'root', 'agentId': 'a123', 'message': {'content': 'Review'}},
                {'type': 'assistant', 'isSidechain': True, 'agentId': 'a123', 'message': {'content': 'Looks good'}},
            ])
            parent_chat, child_chat = indexer.parse_claude(root), indexer.parse_claude(child)
            self.assertEqual(parent_chat['spawned_children'][0]['agent_id'], 'a123')
            self.assertEqual(parent_chat['spawned_children'][0]['label'], 'Review code')
            self.assertEqual(child_chat['parent_key'], 'claude-root')
            self.assertEqual(child_chat['n_answer'], 1)
            self.assertNotEqual(child_chat['key'], parent_chat['key'])

    def test_exact_usage_models_and_duplicate_codex_rollouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                {'type': 'session_meta', 'payload': {'id': 'root'}},
                {'type': 'turn_context', 'payload': {'model': 'small'}},
                {'type': 'event_msg', 'timestamp': '2026-11-13T14:00:00Z', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 110}, 'last_token_usage': {'input_tokens': 100, 'output_tokens': 10}}}},
                {'type': 'turn_context', 'payload': {'model': 'large'}},
                {'type': 'event_msg', 'timestamp': '2026-11-13T15:00:00Z', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 1130}, 'last_token_usage': {'input_tokens': 1000, 'output_tokens': 20}}}},
            ]
            a = self.write(tmp + '/a.jsonl', rows)
            b = self.write(tmp + '/b.jsonl', rows)
            chat = indexer.parse_codex_group('root', [a, b], {})
            self.assertEqual(chat['usage']['in'], 1100)
            buckets = {r['model']: r for r in chat['usage_buckets']}
            self.assertEqual(buckets['small']['in'], 100)
            self.assertEqual(buckets['large']['in'], 1000)

    def test_history_preserves_unknown_and_exact_dimensions(self):
        day = datetime.date.today().isoformat()
        index = {'a': {'source': 'claude', 'node': 'mac', 'root_key': 'a', 'usage_buckets': [dict(day=day, hour=3, model='small', **{'in': 100, 'out': 2, 'turns': 1}), dict(day=day, hour=4, model='large', **{'in': 1000, 'out': 9, 'turns': 1})]}, 'b': {'source': 'codex', 'node': 'sc', 'account_id': 'work', 'root_key': 'a', 'is_child': True, 'usage_buckets': [dict(day=day, hour=4, model='gpt', account_id='work', **{'in': 7, 'out': 3, 'turns': 1})]}}
        with patch.object(usage_history, '_jload', side_effect=lambda name, default: index if name == 'index' else default):
            result = usage_history.compute(1)
        models = {r['model']: r for r in result['by_model']}
        self.assertEqual(models['small']['in'], 100)
        self.assertEqual(models['large']['in'], 1000)
        self.assertEqual(result['totals']['in'], 1107)
        self.assertEqual({r['account'] for r in result['by_account']}, {'unknown', 'work'})
        self.assertEqual(result['by_hour'][4]['turns'], 2)

    def test_legacy_model_allocation_remains_unknown(self):
        day = datetime.date.today().isoformat()
        index = {'a': {'source': 'claude', 'models': {'a': 1, 'b': 1}, 'usage_days': {day: {'in': 300, 'out': 30, 'turns': 2}}}}
        with patch.object(usage_history, '_jload', side_effect=lambda name, default: index if name == 'index' else default):
            result = usage_history.compute(1)
        self.assertEqual(result['by_model'][0]['model'], 'unknown')
        self.assertEqual(result['by_model'][0]['in'], 300)


class ProvenanceTests(unittest.TestCase):
    write = TranscriptTests.write

    def test_native_codex_usage_has_account_and_is_not_mirrored(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                {'type': 'session_meta', 'payload': {'id': 'root', 'creator_account_id': 'native-account'}},
                {'type': 'turn_context', 'payload': {'model': 'gpt'}},
                {'type': 'token_usage_record', 'timestamp': '2026-11-13T14:00:00Z', 'payload': {'response_id': 'r1', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10}}},
                {'type': 'event_msg', 'timestamp': '2026-11-13T14:00:01Z', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 110}, 'last_token_usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10}}}},
            ]
            a = self.write(tmp + '/a.jsonl', rows)
            b = self.write(tmp + '/b.jsonl', rows)
            chat = indexer.parse_codex_group('root', [a, b], {})
            self.assertEqual(chat['usage']['in'], 80)
            self.assertEqual(chat['usage']['turns'], 1)
            self.assertEqual(chat['usage_buckets'][0]['account_id'], 'native-account')

    def test_current_binding_does_not_attribute_historical_usage(self):
        day = datetime.date.today().isoformat()
        index = {'a': {'source': 'claude', 'account_id': 'current', 'usage_buckets': [dict(day=day, model='a', **{'in': 5, 'turns': 1})]}}
        with patch.object(usage_history, '_jload', side_effect=lambda name, default: index if name == 'index' else default):
            result = usage_history.compute(1)
        self.assertEqual(result['by_account'][0]['account'], 'unknown')

    def test_explicit_nested_claude_edges_and_unrelated_root(self):
        index = {
            'claude-root': {'id': 'root', 'spawned_children': [{'agent_id': 'a', 'label': 'First'}]},
            'claude-root-agent-a': {'agent_id': 'a', 'parent_key': 'claude-root', 'root_key': 'claude-root', 'spawned_children': [{'agent_id': 'b', 'label': 'Nested'}]},
            'claude-root-agent-b': {'agent_id': 'b', 'parent_key': 'claude-root', 'root_key': 'claude-root'},
            'claude-unrelated': {'id': 'unrelated', 'cwd': '/same'},
        }
        indexer.resolve_lineage(index)
        self.assertEqual(index['claude-root-agent-b']['parent_key'], 'claude-root-agent-a')
        self.assertEqual(index['claude-root-agent-b']['root_key'], 'claude-root')
        self.assertFalse(index['claude-unrelated'].get('is_child'))

    def test_tree_inherits_root_project_without_editing_assignments(self):
        import session_lineage
        index = {'root': {'id': 'root'}, 'child': {'id': 'child', 'parent_key': 'root', 'is_child': True}}
        projects = {'assignments': {'root': 'curated', 'child': 'stale'}}
        with patch.object(session_lineage, '_load', side_effect=lambda name, default: {'index': index, 'projects': projects}.get(name, default)):
            tree = session_lineage.session_tree(None, {'key': 'child'}, None)
        self.assertEqual(tree['root_key'], 'root')
        self.assertEqual({n['project_id'] for n in tree['nodes']}, {'curated'})
        self.assertEqual(projects['assignments']['child'], 'stale')
        self.assertEqual(tree['edges'], [{'parent_key': 'root', 'child_key': 'child'}])

if __name__ == '__main__':
    unittest.main()
