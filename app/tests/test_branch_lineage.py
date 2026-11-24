"""Pure prepare_run lineage regression: no provider or service imports."""
import ast
import os
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'broker.py'

class BranchPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        function = next(node for node in ast.parse(SOURCE.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'prepare_run')
        namespace = {'os': os, 'CAPABILITIES': {'claude': {}, 'codex': {}}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        cls.prepare = staticmethod(namespace['prepare_run'])

    def scenario(self, cutoff=1):
        chats = {
            'claude-root': {'turns': [
                {'role': 'user', 'text': 'ROOT_PREFIX'},
                {'role': 'assistant', 'text': 'ROOT_ANSWER'},
                {'role': 'user', 'text': 'ROOT_FUTURE_AFTER_FORK'}]},
            'claude-child': {'source_key': 'claude-root', 'source_turn': cutoff, 'turns': [
                {'role': 'user', 'text': 'CHILD_PREFIX'},
                {'role': 'assistant', 'text': 'CHILD_LATER'}]},
        }
        return chats

    def run_handoff(self, chats, key='claude-child', through=0, evidence=None):
        meta = {'key': key, 'id': key, 'source': 'claude', 'cwd': os.getcwd(),
                'node': 'mac', 'conversation_key': 'existing-logical'}
        for name in ('source_key', 'source_turn'):
            if name in chats[key]:
                meta[name] = chats[key][name]
        body = {'key': key, 'provider': 'claude', 'handoff': True, 'message': 'EDITED_REQUEST'}
        if through is not None:
            body['through_turn'] = through
        return self.prepare(body, {key: meta}, chats.get, evidence)

    def test_nested_fork_excludes_later_ancestor_and_child_turns(self):
        result = self.run_handoff(self.scenario())
        self.assertIn('ROOT_ANSWER', result['prompt'])
        self.assertIn('CHILD_PREFIX', result['prompt'])
        self.assertNotIn('ROOT_FUTURE_AFTER_FORK', result['prompt'])
        self.assertNotIn('CHILD_LATER', result['prompt'])
        self.assertEqual((result['source_key'], result['source_turn'], result['source_keys'],
                          result['conversation_key']), ('claude-child', 0, ['claude-root', 'claude-child'], ''))

    def test_first_message_edit_keeps_inherited_prefix_without_child_turns(self):
        result = self.run_handoff(self.scenario(), through=-1)
        self.assertEqual(result['source_turn'], -1)
        self.assertIn('ROOT_PREFIX', result['prompt'])
        self.assertNotIn('CHILD_PREFIX', result['prompt'])

    def test_negative_one_ancestor_boundary_keeps_grandparent_prefix(self):
        chats = self.scenario(-1)
        chats['claude-root'].update(source_key='claude-grand', source_turn=0)
        chats['claude-grand'] = {'turns': [
            {'role': 'user', 'text': 'GRAND_PREFIX'}, {'role': 'assistant', 'text': 'GRAND_FUTURE'}]}
        result = self.run_handoff(chats)
        self.assertIn('GRAND_PREFIX', result['prompt'])
        for text in ('GRAND_FUTURE', 'ROOT_PREFIX', 'ROOT_FUTURE_AFTER_FORK'):
            self.assertNotIn(text, result['prompt'])

    def test_root_normal_handoff_preserves_full_history_and_evidence(self):
        result = self.run_handoff(self.scenario(), key='claude-root', through=None,
                                  evidence=lambda key: 'FULL_SOURCE_EVIDENCE')
        self.assertIn('ROOT_FUTURE_AFTER_FORK', result['prompt'])
        self.assertIn('FULL_SOURCE_EVIDENCE', result['prompt'])
        self.assertEqual((result['conversation_key'], result['source_turn']), ('existing-logical', 2))

    def test_linked_normal_handoff_keeps_full_child_and_bounded_ancestors(self):
        result = self.run_handoff(self.scenario(), through=None,
                                  evidence=lambda key: key + '_EVIDENCE')
        self.assertIn('CHILD_LATER', result['prompt'])
        self.assertIn('claude-child_EVIDENCE', result['prompt'])
        self.assertNotIn('ROOT_FUTURE_AFTER_FORK', result['prompt'])
        self.assertNotIn('claude-root_EVIDENCE', result['prompt'])

    def test_ancestor_journal_is_omitted_even_when_text_boundary_is_last(self):
        chats = self.scenario(2)
        calls = []
        result = self.run_handoff(chats, through=None,
                                  evidence=lambda key: calls.append(key) or 'UNBOUNDED_JOURNAL')
        self.assertEqual(calls, ['claude-child'])
        self.assertIn('Ancestor tool evidence omitted', result['prompt'])

    def test_explicit_full_source_prefix_retains_source_journal_semantics(self):
        result = self.run_handoff(self.scenario(), key='claude-root', through=2,
                                  evidence=lambda key: 'SOURCE_EVIDENCE')
        self.assertIn('SOURCE_EVIDENCE', result['prompt'])

    def test_bad_ancestor_boundaries_fail_instead_of_guessing(self):
        for cutoff in (None, '1', 1.0, True, -2, 99):
            with self.subTest(cutoff=cutoff), self.assertRaisesRegex(ValueError, 'source_turn'):
                self.run_handoff(self.scenario(cutoff))
        chats = self.scenario()
        del chats['claude-child']['source_turn']
        with self.assertRaisesRegex(ValueError, 'source_turn'):
            self.run_handoff(chats)

    def test_invalid_request_boundaries_are_not_coerced(self):
        for through in ('0', 0.5, True, -2, 99):
            with self.subTest(through=through), self.assertRaisesRegex(ValueError, 'source turn'):
                self.run_handoff(self.scenario(), through=through)

    def test_missing_ancestor_has_explicit_error(self):
        chats = self.scenario()
        del chats['claude-root']
        with self.assertRaisesRegex(ValueError, 'missing source conversation'):
            self.run_handoff(chats)

    def test_source_metadata_falls_back_to_index_when_cleaned_chat_has_no_lineage(self):
        chats = self.scenario()
        child = chats['claude-child']
        loader = lambda key: {'turns': child['turns']} if key == 'claude-child' else chats.get(key)
        meta = {'key': 'claude-child', 'id': 'child', 'source': 'claude', 'cwd': os.getcwd(),
                'node': 'mac', 'source_key': 'claude-root', 'source_turn': 1}
        result = self.prepare({'key': 'claude-child', 'message': 'EDIT', 'handoff': True,
                               'through_turn': 0}, {'claude-child': meta}, loader)
        self.assertNotIn('ROOT_FUTURE_AFTER_FORK', result['prompt'])

    def test_normal_native_resume_does_not_reconstruct_lineage(self):
        meta = {'key': 'claude-child', 'id': 'child', 'source': 'claude', 'cwd': os.getcwd(),
                'node': 'mac', 'source_key': 'missing', 'source_turn': None}
        result = self.prepare({'key': 'claude-child', 'message': 'CONTINUE'},
                              {'claude-child': meta}, lambda key: self.fail('Unexpected chat read'))
        self.assertEqual((result['session_id'], result['prompt']), ('child', 'CONTINUE'))


if __name__ == '__main__':
    unittest.main()
