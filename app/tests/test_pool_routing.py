"""Library runs go through the local account pools, keep their session files in the chat account's
config dir, and offer the other provider when every account of one provider is at its limit."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounts
import accounts_guard  # noqa: F401  temp DATA, registry and profile roots
import auth_recovery
import auth_failure
import broker
import clis
import host_connector
import notifications
import pools
import quota_failover

POOL_ENV = {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8317', 'ANTHROPIC_AUTH_TOKEN': 'test-pool-key'}
NOW = 1795514400.0


def snapshot(*states):
    return {'pool': 'ccs', 'title': 'Claude pool (CCS)', 'order': [str(i) for i in range(len(states))], 'serving': None,
            'accounts': {str(i): {'name': f'a{i}', 'state': state, 'until': None,
                                  'windows': [{'kind': '5h', 'label': 'Session (5h)', 'percent': 100 if state == 'full' else 10, 'reset': None}]}
                         for i, state in enumerate(states)}}


class LaunchRoutingTests(unittest.TestCase):
    """broker._launch, the one path every web workbench and Library Code run takes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cwd = root / 'work'
        self.cwd.mkdir()
        self.claude_home, self.codex_home = root / 'claude-home', root / 'codex-home'
        project = self.claude_home / 'projects' / host_connector.claude_project_dir(str(self.cwd))
        project.mkdir(parents=True)
        (project / 'sess-1.jsonl').write_text(json.dumps({'type': 'user', 'sessionId': 'sess-1', 'cwd': str(self.cwd)}) + '\n')
        self.codex_home.mkdir()
        profiles = {'accounts': [{'id': 'claude-a', 'tool': 'claude', 'config_dir': str(self.claude_home)},
                                 {'id': 'codex-a', 'tool': 'codex', 'config_dir': str(self.codex_home)}],
                    'state': {'active': {'claude': 'claude-a', 'codex': 'codex-a'}, 'global_active': {}}}
        for p in (patch.object(host_connector, '_local_accounts', return_value=profiles),
                  patch.object(host_connector, '_base_env', return_value={'PATH': os.environ['PATH'], 'ANTHROPIC_API_KEY': 'stray'}),
                  patch.object(host_connector, 'assert_session_not_running'),
                  patch.object(clis, 'cli', return_value=sys.executable),
                  patch.object(pools, 'ccs_client_env', return_value=dict(POOL_ENV)),
                  patch.object(pools, 'check_up'),
                  patch.object(pools.urllib.request, 'urlopen')):
            p.start()
            self.addCleanup(p.stop)
        self.obj = broker.Broker(broker.RunStore(root / 'runs'))
        self.catalog = {'models': [{'slug': 'gpt-6.2-sol', 'use_responses_lite': True}]}
        pools.urllib.request.urlopen.side_effect = lambda *_, **__: io.BytesIO(json.dumps(self.catalog).encode())

    def launch(self, provider, session_id=''):
        return self.obj._launch({'provider': provider, 'host_id': 'mac', 'cwd': str(self.cwd), 'session_id': session_id,
                                 'account_id': '', 'model': ''})

    def test_claude_resume_goes_through_the_pool_and_keeps_its_config_dir(self):
        launch = self.launch('claude', 'sess-1')
        self.assertEqual(launch['account_pool']['status'], 'pooled')
        self.assertEqual({k: launch['env'][k] for k in POOL_ENV}, POOL_ENV)
        self.assertNotIn('ANTHROPIC_API_KEY', launch['env'])
        self.assertEqual(launch['env']['CLAUDE_CONFIG_DIR'], os.path.realpath(self.claude_home))
        self.assertEqual(launch['argv'][launch['argv'].index('--resume') + 1], 'sess-1')
        self.assertEqual(launch['account_id'], 'claude-a')

    def test_codex_goes_through_codex_lb_by_per_launch_overrides(self):
        launch = self.launch('codex')
        self.assertEqual(launch['account_pool']['status'], 'pooled')
        self.assertEqual(launch['argv'][1:3], ['-c', 'model_provider="codex-lb"'])
        self.assertIn('model_providers.codex-lb.base_url="http://127.0.0.1:2455/backend-api/codex"', launch['argv'])
        self.assertIn('model_providers.codex-lb.requires_openai_auth=false', launch['argv'])
        self.assertEqual(launch['argv'][-3:], ['app-server', '--listen', 'stdio://'])
        override = next(arg for arg in launch['argv'] if arg.startswith('model_catalog_json='))
        catalog_file = Path(json.loads(override.split('=', 1)[1]))
        self.assertEqual(catalog_file.parent, self.obj.store.root)
        self.assertEqual(json.loads(catalog_file.read_text()), self.catalog)
        pools.urllib.request.urlopen.assert_called_once_with(pools.CODEX_LB + '/backend-api/codex/models', timeout=3)
        self.assertEqual(launch['env']['CODEX_HOME'], os.path.realpath(self.codex_home))
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', launch['env'])

    def test_codex_resumes_a_thread_under_the_provider_that_started_it(self):
        day = self.codex_home / 'sessions' / '2026' / '11' / '24'
        day.mkdir(parents=True)
        for sid, provider in (('t-direct', 'openai'), ('t-pool', 'codex-lb')):
            meta = {'type': 'session_meta', 'payload': {'id': sid, 'model_provider': provider, 'cwd': str(self.cwd)}}
            (day / f'rollout-2026-11-24T10-00-00-{sid}.jsonl').write_text(json.dumps(meta) + '\n')
        old = self.launch('codex', 't-direct')
        self.assertEqual(old['account_pool']['status'], 'pooled')
        self.assertIn('model_provider="openai"', old['argv'])
        self.assertIn('openai_base_url="http://127.0.0.1:2455/backend-api/codex"', old['argv'])
        self.assertIn('cli_auth_credentials_store="ephemeral"', old['argv'])
        self.assertNotIn('model_provider="codex-lb"', old['argv'])
        self.assertEqual(self.launch('codex', 't-pool')['account_pool']['status'], 'pooled')

    def test_invalid_codex_catalog_fails_before_resuming(self):
        pools.urllib.request.urlopen.side_effect = lambda *_, **__: io.BytesIO(b'{"models":[]}')
        with self.assertRaisesRegex(ValueError, 'codex-lb model catalog is unavailable'):
            self.launch('codex')
        self.assertFalse((self.obj.store.root / 'codex-lb-models.json').exists())

    def test_unavailable_pool_does_not_use_desktop_credentials(self):
        pools.check_up.side_effect = ConnectionRefusedError('refused')
        with self.assertRaisesRegex(ValueError, 'account pool is unavailable'):
            self.launch('claude', 'sess-1')

    def test_quota_poll_keeps_the_provider_on_the_pool(self):
        pools.poll(str(self.cwd / 'notices.json'), 90, readers=(lambda: snapshot('full', 'cooling'),))
        self.assertEqual(self.launch('claude')['account_pool']['status'], 'pooled')

    def test_setting_turns_the_pools_off(self):
        accounts.set_auto({'pools': False})
        self.addCleanup(accounts.set_auto, {'pools': True})
        launch = self.launch('codex')
        self.assertEqual(launch['account_pool']['status'], 'direct')
        self.assertNotIn('-c', launch['argv'][:2])


class CcsEnvTests(unittest.TestCase):
    def test_ccs_env_is_parsed_from_its_export_lines(self):
        out = "[!] CLIProxy note\nexport ANTHROPIC_AUTH_TOKEN='k-1'\nexport ANTHROPIC_BASE_URL='http://127.0.0.1:8317'\n"
        with patch.object(pools.subprocess, 'run', return_value=Mock(returncode=0, stdout=out)):
            self.assertEqual(pools.ccs_client_env(), {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8317', 'ANTHROPIC_AUTH_TOKEN': 'k-1'})
        with patch.object(pools.subprocess, 'run', return_value=Mock(returncode=0, stdout='nothing\n')):
            with self.assertRaises(ValueError):
                pools.ccs_client_env()


class ProviderSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.obj = broker.Broker(broker.RunStore(self.root / 'runs'))
        self.notices = Path(accounts.DATA) / 'notices.json'
        if self.notices.exists():
            self.notices.unlink()
        self.ctrl = {'stop': False}
        self.choose = patch.object(host_connector, 'select_quota_failover', return_value=None)
        self.choose.start()
        self.addCleanup(self.choose.stop)

    def failed(self, pool_status='pooled', provider='claude'):
        run = self.obj.store.create({'provider': provider, 'host_id': 'mac', 'cwd': str(self.root), 'key': provider + '-s',
                                     'session_id': 's', 'account_id': provider + '-a', 'model': 'm', 'message': 'Finish the table.',
                                     'conversation_key': 'chat-1', 'account_pool': {'pool': 'ccs', 'status': pool_status}})
        failure = {'kind': 'limit_reached', 'raw': "You've hit your usage limit", 'provider': provider}
        self.obj.store.update(run['id'], status='failed', failure=failure)
        return self.obj.store.get(run['id'])

    def test_pooled_quota_failure_skips_the_same_provider_switch_and_offers_codex(self):
        run = self.failed()
        self.assertFalse(quota_failover.eligible(run, {'quota_failure_confirmed': True}))
        offer = quota_failover.provider_switch(run, self.ctrl)
        self.assertEqual((offer['status'], offer['to'], offer['automatic']), ('offered', 'codex', False))
        host_connector.select_quota_failover.assert_not_called()

    def test_the_failed_run_carries_the_offer_from_its_first_failed_state(self):
        run = self.failed()
        self.obj.store.update(run['id'], status='running', failure=None)
        self.obj._record_failure(run['id'], run, self.ctrl, {'kind': 'limit_reached', 'raw': 'limit', 'provider': 'claude'})
        saved = self.obj.store.get(run['id'])
        self.assertEqual((saved['status'], saved['provider_switch']['to']), ('failed', 'codex'))

    def test_direct_run_with_another_account_with_headroom_gets_no_offer(self):
        host_connector.select_quota_failover.return_value = {'account_id': 'claude-b'}
        self.assertIsNone(quota_failover.provider_switch(self.failed('direct'), self.ctrl))
        host_connector.select_quota_failover.return_value = None
        self.assertEqual(quota_failover.provider_switch(self.failed('direct', 'codex'), self.ctrl)['to'], 'claude')

    def test_rate_limits_and_stopped_runs_get_no_offer(self):
        run = self.failed()
        self.assertIsNone(quota_failover.provider_switch(dict(run, failure={'kind': 'rate_limited'}), self.ctrl))
        self.assertIsNone(quota_failover.provider_switch(run, {'stop': True}))

    def test_one_click_mode_announces_with_a_continue_action_and_does_not_switch(self):
        run = self.failed()
        with patch.object(auth_recovery, 'recover') as recover:
            self.assertIsNone(quota_failover.offer_provider_switch(self.obj, run['id'], self.ctrl))
            self.assertIsNone(quota_failover.offer_provider_switch(self.obj, run['id'], self.ctrl))
        recover.assert_not_called()
        record = json.loads(self.notices.read_text())['notices']['provider-switch:' + run['id']]
        self.assertEqual((record['kind'], record['run_id'], record['chat_key'], record['provider']),
                         ('provider_switch', run['id'], 'claude-s', 'claude'))
        self.assertIn('Continue this chat in Codex with one click', record['message'])
        self.assertTrue(self.obj.store.get(run['id'])['provider_switch']['announced'])
        options, actions = notifications._recovery(record, [], {})
        self.assertEqual(options, ['Continue in Codex'])
        self.assertEqual(actions['actions']['Continue in Codex'], {'action': 'handoff', 'provider': 'codex'})

    def test_automatic_mode_continues_in_the_other_provider_once(self):
        accounts.set_auto({'cross_provider': 'automatic'})
        self.addCleanup(accounts.set_auto, {'cross_provider': 'one_click'})
        run = self.failed()
        self.obj.store.update(run['id'], provider_switch=quota_failover.provider_switch(run, self.ctrl))
        successor = {'id': 'next'}
        with patch.object(auth_recovery, 'recover', return_value={'run': successor}) as recover:
            self.assertEqual(quota_failover.offer_provider_switch(self.obj, run['id'], self.ctrl), successor)
            self.assertIsNone(quota_failover.offer_provider_switch(self.obj, run['id'], self.ctrl))
        recover.assert_called_once_with(self.obj, {'id': run['id'], 'action': 'handoff', 'provider': 'codex'})
        marker = self.obj.store.get(run['id'])['provider_switch']
        self.assertEqual((marker['status'], marker['successor_run_id']), ('continued', 'next'))
        self.assertIn('Continuing this chat in Codex', json.loads(self.notices.read_text())['notices']['provider-switch:' + run['id']]['message'])


class ProviderSwitchHandoffTests(unittest.TestCase):
    """The one-click action reuses the recovery handoff: same chat, context and the pending message."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        chats = root / 'chats'
        chats.mkdir()
        index = root / 'index.json'
        meta = {'id': 's', 'source': 'claude', 'cwd': str(root), 'account_id': 'claude-a', 'host_id': 'mac', 'conversation_key': 'chat-1'}
        limit = "You've hit your weekly limit"
        chat = dict(meta, turns=[{'role': 'user', 'text': 'Draft the intro'}, {'role': 'assistant', 'text': 'Intro drafted'},
                                 {'role': 'user', 'text': 'Now the table'}, {'role': 'assistant', 'text': limit, 'error': True}])
        index.write_text(json.dumps({'claude-s': meta}))
        (chats / 'claude-s.json').write_text(json.dumps(chat))
        for p in (patch.object(broker.stream, 'INDEX', str(index)), patch.object(broker.stream, 'CHATS', str(chats)),
                  patch('live.mac_sessions', return_value=([], []))):
            p.start()
            self.addCleanup(p.stop)
        self.obj = broker.Broker(broker.RunStore(root / 'execution'))
        self.run = self.obj.store.create(dict(provider='claude', cwd=str(root), host_id='mac', session_id='s', key='claude-s',
                                              account_id='claude-a', message='Now the table', prompt='Now the table',
                                              conversation_key='chat-1', attachments=[]))
        self.obj.store.update(self.run['id'], status='failed', failure=auth_failure.classify('claude', limit, 'claude-a'))
        self.specs = []
        self.obj.start = lambda spec: self.specs.append(spec) or self.obj.store.create(spec)

    def test_continue_in_codex_keeps_the_chat_and_carries_context_and_message(self):
        auth_recovery.recover(self.obj, {'id': self.run['id'], 'action': 'handoff', 'provider': 'codex'})
        spec = self.specs[-1]
        self.assertEqual((spec['provider'], spec['session_id'], spec['conversation_key']), ('codex', '', 'chat-1'))
        self.assertEqual(spec['provider_switch_from'], {'run_id': self.run['id'], 'provider': 'claude', 'account_id': 'claude-a'})
        self.assertIn('Intro drafted', spec['prompt'])
        self.assertIn('Now the table', spec['prompt'])
        self.assertNotIn('weekly limit', spec['prompt'])


if __name__ == '__main__':
    unittest.main()
