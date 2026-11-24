import sys
import tempfile
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import commands


class CommandTests(unittest.TestCase):
    def test_skills_metadata_only_and_provider_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'example').mkdir()
            (root / 'example' / 'SKILL.md').write_text('---\nname: example\ndescription: A useful skill\n---\nPRIVATE BODY MUST NOT LEAK')
            with patch.object(commands, 'skill_roots', return_value=[root]):
                for provider, expected in [('codex', '$example'), ('claude', '/example')]:
                    result = commands.command_catalog(provider)
                    skill = next(x for x in result['commands'] if x['kind'] == 'skill')
                    self.assertEqual(skill['insert'], expected)
                    self.assertNotIn('PRIVATE', str(result))

    def test_remote_never_advertises_local_skills(self):
        with patch.object(commands, 'skill_roots', side_effect=AssertionError('local scan')):
            result = commands.command_catalog('codex', host_id='sc')
        self.assertEqual([x['name'] for x in result['commands']], ['compact'])
        self.assertIn('remote', result['notice'].lower())

    def test_compact_requires_native_session_without_handoff(self):
        with tempfile.TemporaryDirectory() as cwd:
            idx = {'codex-old': {'id': 'old', 'source': 'codex', 'cwd': cwd}}
            spec = broker.prepare_run({'key': 'codex-old', 'operation': 'compact'}, idx, lambda _: {})
            self.assertEqual(spec['operation'], 'compact')
            self.assertEqual(spec['prompt'], '/compact')
            for extra in [{'handoff': True}, {'attachments': ['a']}, {'message': 'discard me'}]:
                with self.assertRaises(ValueError):
                    broker.prepare_run(dict(key='codex-old', operation='compact', **extra), idx, lambda _: {})
            with self.assertRaises(ValueError):
                broker.prepare_run({'operation': 'compact', 'new': {'cwd': cwd}}, {}, lambda _: {})

    def test_codex_compact_is_rpc_not_prompt_and_requires_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = broker.RunStore(tmp)
            obj = broker.Broker(store)
            run = store.create({'provider': 'codex', 'operation': 'compact', 'session_id': 'old', 'key': 'codex-old', 'cwd': tmp, 'host_id': 'mac', 'prompt': '/compact'})
            st = broker.stream._State('old')
            ctrl = {'pending_rpc': {}}
            with patch.object(obj, '_send') as send:
                obj._codex_rpc(run['id'], run, st, ctrl, {'id': 2, 'result': {'thread': {'id': 'old'}}})
                for handle in ctrl.get('lockfiles', []):
                    self.addCleanup(handle.close)
                self.assertEqual(send.call_args.args[1], {'id': 5, 'method': 'thread/goal/get', 'params': {'threadId': 'old'}})
                obj._codex_rpc(run['id'], run, st, ctrl, {'id': 5, 'result': {'goal': None}})
                self.assertEqual(send.call_args.args[1], {'id': 3, 'method': 'thread/compact/start', 'params': {'threadId': 'old'}})
                self.assertFalse(any(call.args[1].get('method') == 'turn/start' for call in send.call_args_list))
            self.assertIsNone(obj._codex_rpc(run['id'], run, st, ctrl, {'id': 3, 'result': {}}))
            self.assertIsNone(obj._codex_rpc(run['id'], run, st, ctrl, {'method': 'item/completed', 'params': {'item': {'type': 'contextCompaction', 'id': 'i'}}}))
            self.assertTrue(ctrl['compaction_confirmed'])
            self.assertEqual(obj._codex_rpc(run['id'], run, st, ctrl, {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}), 'completed')

    def test_worker_requires_provider_compaction_evidence(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), tempfile.TemporaryDirectory() as tmp:
                store = broker.RunStore(tmp)
                obj = broker.Broker(store)
                spec = {'provider': 'claude', 'operation': 'compact', 'session_id': 'old',
                        'key': 'claude-old', 'cwd': tmp, 'host_id': 'mac', 'message': '/compact', 'prompt': '/compact'}
                events = [{'type': 'system', 'session_id': 'old'}]
                if confirmed:
                    events.append({'type': 'system', 'subtype': 'compact_boundary', 'session_id': 'old'})
                events.append({'type': 'result', 'session_id': 'old', 'result': 'ok'})
                code = "import json,sys; sys.stdin.readline(); print(json.dumps({'type':'control_response','response':{'request_id':'initialize','subtype':'success'}}),flush=True); sys.stdin.readline(); events=" + repr(events) + "; [print(json.dumps(e),flush=True) for e in events]"
                launch = {'argv': [sys.executable, '-c', code], 'cwd': tmp, 'env': os.environ.copy(), 'protocol': 'claude-stream'}
                with patch.object(obj, '_launch', return_value=launch), patch.object(obj, '_persist') as persist:
                    run = obj.start(spec)
                    for _ in range(300):
                        final = store.get(run['id'])
                        if final['status'] in broker.TERMINAL:
                            break
                        time.sleep(.01)
                    self.assertEqual(final['status'], 'completed' if confirmed else 'failed')
                    persist.assert_not_called()
                    if confirmed:
                        self.assertEqual(final['text'], 'Context compacted.')
                    else:
                        self.assertIn('did not confirm', final['error'])
                    for ctrl in list(obj.active.values()):
                        self.assertTrue(ctrl['finished'].wait(3))

    def test_claude_compact_uses_native_slash_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = broker.Broker(broker.RunStore(tmp))
            message = obj._claude_input({'operation': 'compact', 'prompt': '/compact'})
            self.assertEqual(message['message']['content'], [{'type': 'text', 'text': '/compact'}])

if __name__ == '__main__': unittest.main()

class RemoteCatalogTests(unittest.TestCase):
    def test_local_claude_adds_runtime_cli_commands_once(self):
        native = [{'name': 'compact', 'kind': 'command', 'insert': '/compact', 'native': True},
                  {'name': 'review', 'kind': 'command', 'insert': '/review', 'native': True}]
        with patch.object(commands, 'skill_roots', return_value=[]), patch.object(commands, 'native_claude_commands', return_value=native) as probe:
            result = commands.list_commands(None, {'provider': 'claude', 'cwd': '/tmp'}, None)
            self.assertEqual([c['name'] for c in result['commands']], ['compact', 'review'])
            commands.list_commands(None, {'provider': 'codex'}, None)
        probe.assert_called_once_with('/tmp')

    def test_shipped_native_commands_are_cli_builtins_without_model_names(self):
        for path in ('workbench/src/chat/nativeCommands.json', 'vscode-library/media/native-commands.json'):
            text = (Path(__file__).resolve().parents[1] / path).read_text()
            self.assertNotRegex(text.lower(), r'opus|sonnet|haiku|fable')
            self.assertTrue(all(c['kind'] == 'command' for c in json.loads(text)))

    def test_remote_uses_verified_host_catalog(self):
        from unittest.mock import patch
        import commands
        expected=[{'name':'compact','kind':'command','insert':'/compact'},{'name':'cluster-skill','kind':'skill','insert':'/cluster-skill'}]
        with patch('hosts.get_host',return_value={'state':'online','commands':{'claude':expected}}):
            result=commands.list_commands(None,{'provider':'claude','host':'sc'},None)
        self.assertEqual(result['scope'],'remote')
        self.assertEqual(result['commands'],expected)
        self.assertNotIn('notice',result)
