"""Resume line inputs: context size, cache TTL, last model and effort, per transcript."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import conversation_settings
import indexer


def claude_turn(ts, model='claude-opus-5-5', mid='m', inp=2, read=0, write=0, out=10, ttl='5m', side=False, text='ok'):
    usage = {'input_tokens': inp, 'cache_read_input_tokens': read, 'cache_creation_input_tokens': write,
             'output_tokens': out}
    if ttl:
        usage['cache_creation'] = {'ephemeral_1h_input_tokens': write if ttl == '1h' else 0,
                                   'ephemeral_5m_input_tokens': write if ttl == '5m' else 0}
    return {'type': 'assistant', 'timestamp': ts, 'isSidechain': side,
            'message': {'id': mid, 'model': model, 'usage': usage, 'stop_reason': 'end_turn',
                        'content': [{'type': 'text', 'text': text}]}}


def user(ts, text='hi'):
    return {'type': 'user', 'timestamp': ts, 'message': {'content': text}}


def api_request(effort, source='sdk'):
    return {'type': 'api-request', 'querySource': source,
            'params': {'model': 'claude-opus-5-5', 'output_config': {'effort': effort}}}


class ResumeCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, rows):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.writelines(json.dumps(row) + '\n' for row in rows)
        return path

    def test_claude_last_turn_with_5_minute_cache(self):
        path = self.write('p/a.jsonl', [
            user('2026-11-20T10:00:00Z'), api_request('high'),
            claude_turn('2026-11-20T10:00:05Z', mid='m1', inp=3, read=0, write=50000, out=200),
            user('2026-11-20T10:01:00Z'), api_request('medium'),
            claude_turn('2026-11-20T10:01:09Z', mid='m2', inp=4, read=50000, write=1200, out=300),
            # A subagent's request and a sidechain turn never change the main context.
            api_request('low', 'agent:custom:engineer'),
            claude_turn('2026-11-20T10:02:00Z', mid='s1', model='claude-haiku-5-5', inp=9, read=999999, side=True),
        ])
        resume = indexer.parse_claude(path)['resume']
        self.assertEqual(resume, {'effort': 'medium', 'model': 'claude-opus-5-5', 'at': '2026-11-20T10:01:09Z',
                                  'compacted': False, 'cache_ttl_s': 300, 'context_tokens': 4 + 50000 + 1200 + 300})

    def test_claude_one_hour_cache_persists_through_read_only_turns(self):
        path = self.write('p/b.jsonl', [
            user('2026-11-20T10:00:00Z'),
            claude_turn('2026-11-20T10:00:05Z', mid='m1', write=80000, ttl='1h'),
            user('2026-11-20T10:30:00Z'),
            claude_turn('2026-11-20T10:30:05Z', mid='m2', read=80000, write=0, out=20, ttl='1h'),
        ])
        resume = indexer.parse_claude(path)['resume']
        self.assertEqual(resume['cache_ttl_s'], 3600)
        self.assertEqual(resume['context_tokens'], 2 + 80000 + 20)

    def test_claude_compaction_shrinks_context_until_next_turn(self):
        boundary = {'type': 'system', 'subtype': 'compact_boundary', 'timestamp': '2026-11-20T11:00:00Z',
                    'compactMetadata': {'trigger': 'auto', 'preTokens': 241686, 'postTokens': 20950}}
        rows = [user('2026-11-20T10:00:00Z'),
                claude_turn('2026-11-20T10:59:00Z', mid='m1', read=238241, write=201, out=1723, ttl='1h'),
                boundary]
        resume = indexer.parse_claude(self.write('p/c.jsonl', rows))['resume']
        self.assertEqual((resume['context_tokens'], resume['compacted'], resume['at']), (20950, True, '2026-11-20T11:00:00Z'))
        self.assertNotIn('cache_ttl_s', resume)  # nothing cached for the compacted prefix yet
        rows += [user('2026-11-20T11:00:10Z'),
                 claude_turn('2026-11-20T11:00:20Z', mid='m2', read=28270, write=44537, out=737, ttl='1h')]
        resume = indexer.parse_claude(self.write('p/d.jsonl', rows))['resume']
        self.assertEqual((resume['context_tokens'], resume['compacted'], resume['cache_ttl_s']),
                         (2 + 28270 + 44537 + 737, False, 3600))

    def test_codex_last_token_count_and_compaction(self):
        def count(ts, inp, cached, out, total):
            return {'type': 'event_msg', 'timestamp': ts, 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': total}, 'model_context_window': 475000,
                'last_token_usage': {'input_tokens': inp, 'cached_input_tokens': cached, 'output_tokens': out}}}}
        head = [{'type': 'session_meta', 'timestamp': '2026-11-15T10:00:00Z', 'payload': {'id': 's1', 'cwd': '/tmp'}},
                {'type': 'turn_context', 'timestamp': '2026-11-15T10:00:01Z', 'payload': {'model': 'gpt-6.1-terra', 'effort': 'high'}},
                {'type': 'event_msg', 'timestamp': '2026-11-15T10:00:01Z', 'payload': {'type': 'user_message', 'message': 'go'}},
                count('2026-11-15T10:00:05Z', 90000, 0, 100, 90000),
                count('2026-11-15T10:01:05Z', 102362, 101888, 202, 192362),
                count('2026-11-15T10:01:06Z', 102362, 101888, 202, 192362),  # repeated event
                {'type': 'event_msg', 'timestamp': '2026-11-15T10:01:07Z', 'payload': {'type': 'agent_message', 'message': 'done'}}]
        resume = indexer.parse_codex_group('s1', [self.write('r1.jsonl', head)], {})['resume']
        self.assertEqual(resume, {'effort': 'high', 'model': 'gpt-6.1-terra', 'at': '2026-11-15T10:01:05Z',
                                  'compacted': False, 'context_tokens': 102564, 'context_window': 475000})
        compacted = head + [{'type': 'compacted', 'timestamp': '2026-11-15T12:00:00Z', 'payload': {'message': ''}}]
        resume = indexer.parse_codex_group('s1', [self.write('r2.jsonl', compacted)], {})['resume']
        self.assertEqual((resume['context_tokens'], resume['compacted']), (None, True))
        # A continuation file with only a new turn_context keeps the earlier size.
        later = [{'type': 'session_meta', 'timestamp': '2026-11-16T10:00:00Z', 'payload': {'id': 's1'}},
                 {'type': 'turn_context', 'timestamp': '2026-11-16T10:00:01Z', 'payload': {'model': 'gpt-6.1-terra', 'effort': 'xhigh'}}]
        chat = indexer.parse_codex_group('s1', [self.write('r1.jsonl', head), self.write('r3.jsonl', later)], {})
        self.assertEqual((chat['resume']['effort'], chat['resume']['context_tokens']), ('xhigh', 102564))
        self.assertEqual(indexer.meta_of(chat)['resume'], chat['resume'])

    def test_claude_defaults_use_the_session_effort(self):
        profile = os.path.join(self.tmp.name, 'home')
        path = self.write('home/projects/p/x.jsonl', [
            user('2026-11-20T10:00:00Z'), api_request('xhigh'),
            claude_turn('2026-11-20T10:00:05Z', model='claude-sonnet-5-5'),
            api_request('low', 'agent:builtin:Explore')])
        meta = {'id': 'x', 'cwd': self.tmp.name, 'files_all': [path]}
        with patch.object(conversation_settings.host_connector, 'resolve_local_account', return_value={'account_id': 'a'}), \
             patch.object(conversation_settings.host_connector, 'local_profile', return_value=('a', profile, {})):
            value = conversation_settings.local_defaults('claude', meta)
        self.assertEqual((value['model'], value['effort']), ('claude-sonnet-5-5', 'xhigh'))
        # A transcript outside the owner profile (same login, other folder) still resumes on its own model.
        with open(os.path.join(profile, 'settings.json'), 'w') as fh:
            json.dump({'model': 'opus[1m]', 'effortLevel': 'low'}, fh)
        outside = {'id': 'y', 'cwd': self.tmp.name, 'files_all': [os.path.join(self.tmp.name, 'elsewhere.jsonl')],
                   'resume': {'model': 'claude-opus-5-5', 'effort': 'max'}}
        with patch.object(conversation_settings.host_connector, 'resolve_local_account', return_value={'account_id': 'a'}), \
             patch.object(conversation_settings.host_connector, 'local_profile', return_value=('a', profile, {})):
            value = conversation_settings.local_defaults('claude', outside)
        self.assertEqual((value['model'], value['effort']), ('claude-opus-5-5', 'max'))


if __name__ == '__main__':
    unittest.main()
