import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import host_connector


class QuotaFailoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = broker.RunStore(self.root / 'runs')
        self.obj = broker.Broker(self.store)
        self.path = self.root / 'native.jsonl'
        self.failure = {'kind': 'limit_reached', 'raw': "You've hit your usage limit", 'provider': 'claude'}
        self.run = self.store.create({'provider': 'claude', 'host_id': 'mac', 'cwd': self.tmp.name,
            'key': 'claude-old', 'session_id': 'old', 'account_id': 'personal', 'model': 'sonnet',
            'conversation_key': 'logical', 'message': 'Run the command and finish the report.',
            'prompt': 'Run the command and finish the report.'})
        self.store.update(self.run['id'], status='failed', failure=self.failure)
        self.ctrl = {'proc': Mock(poll=Mock(return_value=1)), 'quota_failure_confirmed': True,
                     'stop': False, 'background': {}, 'pending_requests': {}, 'claude_inputs': []}
        self.launch = {'transport': 'local', 'env': {}, 'protocol': 'claude-stream'}
        self.events = [
            {'type': 'user', 'sessionId': 'old', 'cwd': self.tmp.name,
             'message': {'content': [{'type': 'text', 'text': self.run['message']}]}},
            {'type': 'assistant', 'sessionId': 'old', 'message': {'content': [
                {'type': 'tool_use', 'id': 'write', 'name': 'Bash', 'input': {'command': 'create-report'}}]}},
            {'type': 'user', 'sessionId': 'old', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'write', 'content': 'Report created successfully.'}]}},
            {'type': 'assistant', 'sessionId': 'old', 'isApiErrorMessage': True,
             'message': {'content': [{'type': 'text', 'text': self.failure['raw']}]}}
        ]
        self.write_native()
        self.native = patch.object(broker.stream, '_claude_transcript', return_value=str(self.path))
        self.native.start(); self.addCleanup(self.native.stop)
        self.selection = patch.object(host_connector, 'select_quota_failover', return_value={
            'account_id': 'corporate', 'reason': 'quota_exhausted', 'percent': 20}, create=True)
        self.choose = self.selection.start(); self.addCleanup(self.selection.stop)
        shared = patch.object(host_connector, 'shared_session_store', side_effect=ValueError('not shared'))
        self.shared = shared.start(); self.addCleanup(shared.stop)
        self.launched = []
        def start(spec):
            self.assertIsNotNone(self.ctrl['proc'].poll(), 'prior provider must be reaped')
            self.launched.append(spec)
            return self.store.create(spec)
        self.obj.start = start

    def write_native(self):
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in self.events))

    def continue_run(self):
        module = importlib.import_module('quota_failover')
        return module.continue_run(self.obj, self.run['id'], self.launch, self.ctrl)

    def test_completed_command_is_evidence_not_resubmitted_work(self):
        result = self.continue_run()
        self.assertIsNotNone(result)
        spec = self.launched[0]
        self.assertNotEqual(spec['message'], self.run['message'])
        self.assertIn('Do not repeat completed actions', spec['message'])
        self.assertIn('Report created successfully.', spec['prompt'])
        self.assertIn('create-report', spec['prompt'])
        self.assertEqual(spec['conversation_key'], 'logical')
        self.assertEqual(spec['source_key'], 'claude-old')
        self.assertEqual(spec['session_id'], '')
        self.assertEqual(spec['model'], 'sonnet')
        self.assertEqual(spec['account_id'], 'corporate')
        source = self.store.get(self.run['id'])
        self.assertEqual(source['quota_failover']['successor_run_id'], result['id'])
        self.assertEqual(source['quota_failover']['status'], 'continued')
        self.assertTrue(any(e['event'] == 'account_switch' for e in self.store.events(source['id'])))

    def test_snapshot_preserves_tool_and_message_chronology(self):
        self.events.insert(3, {'type': 'assistant', 'sessionId': 'old',
                              'message': {'content': 'Now validate the created report.'}})
        self.write_native()
        self.continue_run()
        prompt = self.launched[0]['prompt']
        evidence = prompt[prompt.index('[Recorded tool evidence'):]
        self.assertLess(evidence.index('create-report'), evidence.index('Report created successfully.'))
        self.assertLess(evidence.index('Report created successfully.'), evidence.index('Now validate'))

    def test_tool_activity_after_quota_failure_blocks_handoff(self):
        self.events.extend(self.events[1:3])
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_duplicate_or_orphan_tool_id_blocks_handoff(self):
        original = list(self.events)
        for event in (self.events[1], {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'unknown', 'content': 'unknown'}]}}):
            self.events = original[:3] + [event] + original[3:]
            self.write_native()
            self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_first_terminal_failure_publishes_switching_atomically(self):
        self.obj._record_failure(self.run['id'], self.run, self.ctrl, self.failure)
        actual = self.store.get(self.run['id'])
        self.assertEqual(actual['quota_failover']['status'], 'switching')
        self.assertEqual(actual['status'], 'failed')

    def test_launched_successor_is_retained_if_notification_fails(self):
        emit = self.store.emit
        def fail_notification(rid, event, data):
            if event == 'account_switch' and data.get('status') == 'continued':
                raise OSError('notification unavailable')
            return emit(rid, event, data)
        with patch.object(self.store, 'emit', side_effect=fail_notification):
            result = self.continue_run()
        self.assertIsNotNone(result)
        self.assertEqual(self.store.get(self.run['id'])['quota_failover']['status'], 'continued')
        self.assertEqual(self.continue_run()['id'], result['id'])
        self.assertEqual(len(self.launched), 1)

    def test_restart_resolves_pending_switch_without_replaying(self):
        self.store.update(self.run['id'], quota_failover={'status': 'switching', 'from_account_id': 'personal'})
        broker.Broker(self.store)
        marker = self.store.get(self.run['id'])['quota_failover']
        self.assertEqual(marker['status'], 'blocked')
        self.assertIn('restart', marker['reason'].lower())

    def test_worker_reaps_provider_before_starting_linked_continuation(self):
        import time
        code = """import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({'type':'control_response','response':{'request_id':'initialize','subtype':'success'}}),flush=True)
json.loads(sys.stdin.readline())
print(json.dumps({'type':'assistant','session_id':'old','isApiErrorMessage':True,'message':{'content':[{'type':'text','text':"You've hit your usage limit"}]}}),flush=True)
"""
        launch = dict(self.launch, argv=[sys.executable, '-c', code], cwd=self.tmp.name, env=os.environ.copy())
        # The source worker uses real broker startup/cleanup; intercept only the
        # successor launch to prove it happens after the old Popen is reaped.
        original_start = broker.Broker.start.__get__(self.obj)
        def successor(spec):
            source = self.store.get(spec['quota_failover_from']['run_id'])
            self.assertNotIn(source['id'], self.obj.active)
            self.launched.append(spec)
            return self.store.create(spec)
        self.obj.start = successor
        with patch.object(self.obj, '_launch', return_value=launch), patch.object(self.obj, '_persist'):
            source = original_start({k:v for k,v in self.run.items() if k not in ('id','status','created_at','updated_at','last_seq','text')})
            control = self.obj.active.get(source['id'])
            self.assertIsNotNone(control)
            self.assertTrue(control['finished'].wait(8))
            self.assertIsNotNone(control['proc'].returncode)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.store.get(source['id'])['quota_failover']['status'], 'continued')

    def test_completed_synchronous_agent_is_valid_context(self):
        self.events[1]['message']['content'][0].update(name='Agent', input={'prompt': 'Review the report'})
        self.write_native()
        self.assertIsNotNone(self.continue_run())
        self.assertIn('Review the report', self.launched[0]['prompt'])

    def test_native_shared_store_keeps_same_session_and_only_continues(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        result = self.continue_run()
        self.assertIsNotNone(result)
        spec = self.launched[0]
        self.assertEqual((spec['key'], spec['session_id'], spec['conversation_key']), ('claude-old', 'old', 'logical'))
        self.assertEqual(spec['shared_session_from'], 'personal')
        self.assertFalse(spec['handoff'])
        self.assertEqual(spec['prompt'], spec['message'])
        self.assertTrue(spec['prompt'].startswith('[Automatic account continuation]'))
        self.assertNotIn(self.run['message'], spec['prompt'])
        self.assertEqual(spec['quota_failover_from']['mode'], 'native_resume')
        self.assertNotIn('prompt_sha256', spec['quota_failover_from'])
        self.assertNotIn('source_key', spec)
        self.assertEqual(self.store.get(self.run['id'])['quota_failover']['mode'], 'native_resume')

    def test_native_resume_accepts_large_compacted_meta_and_image_history(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        self.events[0]['message']['content'].append({'type': 'image', 'source': {'type': 'base64', 'data': 'image'}})
        self.events.insert(1, {'type': 'user', 'isMeta': True, 'message': {'content': 'Internal saved context'}})
        self.events.insert(2, {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'Compacted native context'}})
        self.events.insert(3, {'type': 'assistant', 'message': {'content': 'x' * (9 * 1024 * 1024)}})
        self.write_native()
        self.assertIsNotNone(self.continue_run())
        self.assertLess(len(self.launched[0]['prompt']), 1000)

    def test_native_resume_still_requires_matching_closed_checkpoint(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        del self.events[2]
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_native_resume_rejects_missing_checkpoint(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        self.path.unlink()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_native_rejects_newer_attachment_only_turn(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        self.events.append({'type': 'user', 'message': {'content': [{'type': 'image', 'source': {'data': 'new'}}]}})
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_native_rejects_saved_unfinished_background_task(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        self.events.insert(1, {'type': 'system', 'subtype': 'task_started', 'is_backgrounded': True, 'task_id': 'job'})
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_native_chain_does_not_revisit_an_attempted_account(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        first = self.continue_run()
        self.assertIsNotNone(first)
        self.store.update(first['id'], status='failed', failure=self.failure)
        self.events += [{'type': 'user', 'sessionId': 'old', 'message': {'content': first['prompt']}}, self.events[-1]]
        self.write_native()
        module = importlib.import_module('quota_failover')
        self.assertIsNone(module.continue_run(self.obj, first['id'], self.launch, self.ctrl))
        self.assertEqual(len(self.launched), 1)

    def test_native_launch_uses_target_profile_and_rechecks_busy_state(self):
        self.shared.side_effect = None
        self.shared.return_value = '/shared/projects'
        self.continue_run()
        spec = self.launched[0]
        source = self.root/'source'; target = self.root/'target'
        for home in (source, target): home.mkdir()
        from host_connector import claude_project_dir
        project = source/'projects'/claude_project_dir(self.tmp.name)
        project.mkdir(parents=True)
        (project/'old.jsonl').write_bytes(self.path.read_bytes())
        (target/'projects').symlink_to(source/'projects')
        profiles = {'accounts': [{'id': 'personal', 'tool': 'claude', 'config_dir': str(source)},
                                 {'id': 'corporate', 'tool': 'claude', 'config_dir': str(target)}],
                    'state': {'active': {'claude': 'personal'}, 'global_active': {}}}
        import clis
        with patch('accounts._auto_cfg', return_value={'pools': False}), \
             patch.object(host_connector, '_local_accounts', return_value=profiles), \
             patch.object(host_connector, '_base_env', return_value={'PATH': os.environ['PATH']}), \
             patch.object(host_connector, 'assert_session_not_running') as idle, \
             patch.object(clis, 'cli', return_value=sys.executable):
            launch = self.obj._launch(spec)
            self.assertEqual(launch['env']['CLAUDE_CONFIG_DIR'], os.path.realpath(target))
            self.assertIn('--resume', launch['argv'])
            self.assertEqual(launch['argv'][launch['argv'].index('--resume')+1], 'old')
            self.assertEqual({c.args[0] for c in idle.call_args_list}, {os.path.realpath(source), os.path.realpath(target)})
            idle.side_effect = ValueError('native session busy')
            with self.assertRaisesRegex(ValueError, 'busy'):
                self.obj._launch(spec)

    def test_separate_store_keeps_linked_handoff_fallback(self):
        self.assertIsNotNone(self.continue_run())
        self.assertTrue(self.launched[0]['handoff'])
        self.assertEqual(self.launched[0]['quota_failover_from']['mode'], 'linked_handoff')

    def test_rate_limit_and_authentication_failures_do_not_switch(self):
        for kind in ('rate_limited', 'auth_expired', 'not_logged_in', 'other'):
            self.store.update(self.run['id'], failure=dict(self.failure, kind=kind))
            self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)
        self.choose.assert_not_called()

    def test_unconfirmed_error_text_does_not_switch(self):
        self.ctrl['quota_failure_confirmed'] = False
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_owned_process_must_have_exited(self):
        self.ctrl['proc'].poll.return_value = None
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_unmatched_tool_call_blocks_continuation(self):
        del self.events[2]
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)
        self.assertIn('tool', self.store.get(self.run['id'])['quota_failover']['reason'].lower())

    def test_pending_work_blocks_continuation(self):
        for field, value in [('background', {'task': 'running'}), ('pending_requests', {'approval': {}}),
                             ('claude_inputs', [{'message': 'new steering'}]), ('pending_rpc', {'1': {'kind': 'steer'}})]:
            self.ctrl[field] = value
            self.assertIsNone(self.continue_run(), field)
            self.ctrl[field] = {} if isinstance(value, dict) else []
        self.assertFalse(self.launched)

    def test_missing_or_incomplete_transcript_blocks_continuation(self):
        self.path.unlink()
        self.assertIsNone(self.continue_run())
        self.path.write_text(json.dumps(self.events[0]) + '\n{"type":')
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_newer_user_turn_blocks_old_failure(self):
        self.events.append({'type': 'user', 'sessionId': 'old', 'message': {'content': 'New task'}})
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_truncated_context_is_not_launched(self):
        self.events.insert(1, {'type': 'assistant', 'sessionId': 'old',
                              'message': {'content': 'x' * 181000}})
        self.write_native()
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)

    def test_no_verified_headroom_preserves_failed_run(self):
        self.choose.return_value = None
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)
        self.assertEqual(self.store.get(self.run['id'])['status'], 'failed')

    def test_attempted_accounts_are_carried_and_repeat_is_idempotent(self):
        self.store.update(self.run['id'], quota_failover_attempted_accounts=['earlier'])
        first = self.continue_run()
        again = self.continue_run()
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.launched[0]['quota_failover_attempted_accounts'], ['earlier', 'personal', 'corporate'])
        self.assertEqual(self.choose.call_args.args[3], ['earlier', 'personal'])

    def test_remote_nonclaude_and_stopped_runs_do_not_switch(self):
        for changes in ({'host_id': 'sc'}, {'provider': 'codex'}, {'operation': 'compact'}):
            original = self.store.get(self.run['id'])
            self.store.update(self.run['id'], **changes)
            self.assertIsNone(self.continue_run())
            self.store.update(self.run['id'], **{k: original.get(k) for k in changes})
        self.ctrl['stop'] = True
        self.assertIsNone(self.continue_run())
        self.assertFalse(self.launched)


if __name__ == '__main__':
    unittest.main()
