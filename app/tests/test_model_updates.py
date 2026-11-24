import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_updates
import server


class Sources(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / 'profiles' / 'a').mkdir(parents=True)
        self.tiers = self.dir / 'models.json'
        self.tiers.write_text(json.dumps({'tiers': {'top': {'claude': 'claude-fable-5-1'}, 'judgment': {'claude': 'claude-opus-5-5'}}}))
        config = self.dir / 'config.json'
        config.write_text(json.dumps({'models_file': str(self.tiers)}))
        self.home = self.dir / '.claude.json'
        self.write(self.home, [{'value': 'claude-fable-5-2[1m]', 'label': 'Fable'}], oauthAccount={'emailAddress': 'secret'})
        self.write(self.dir / 'profiles' / 'a' / '.claude.json', [{'value': 'claude-opus-5-4'}, {'value': 'claude-fable-5-2[1m]'}])
        server._MODEL_SOURCES.clear()
        for p in (patch.object(server, 'CLAUDE_MODEL_CACHES', (str(self.home), str(self.dir / 'profiles' / '*' / '.claude.json'))),
                  patch.dict(server.FILES, {'config': str(config)})):
            p.start()
            self.addCleanup(p.stop)

    def write(self, path, cache, **extra):
        path.write_text(json.dumps({'additionalModelOptionsCache': cache, **extra}))
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


class ModelSourceTests(Sources):
    def test_merge_is_deduplicated_newest_first_with_1m_variant(self):
        exact = [m for m in server.claude_models() if m['id'].startswith('claude-')]
        self.assertEqual(exact, [
            {'id': 'claude-fable-5-2', 'name': 'Fable 5.2'},
            {'id': 'claude-fable-5-2[1m]', 'name': 'Fable 5.2 (1M context)'},
            {'id': 'claude-fable-5-1', 'name': 'Fable 5.1'},
            {'id': 'claude-opus-5-5', 'name': 'Opus 5.5'},
            {'id': 'claude-opus-5-4', 'name': 'Opus 5.4'},
        ])
        self.assertEqual(server.claude_models()[len(exact):], server.CLAUDE_MODELS)

    def test_new_cache_entry_appears_and_changes_state_signature(self):
        before = server._state_signature()
        self.write(self.home, [{'value': 'claude-fable-5-3[1m]'}])
        ids = [m['id'] for m in server.claude_models()]
        self.assertEqual(ids[:2], ['claude-fable-5-3', 'claude-fable-5-3[1m]'])
        self.assertNotEqual(before, server._state_signature())

    def test_only_the_model_key_is_kept(self):
        server.claude_models()
        self.assertNotIn('secret', json.dumps(server._MODEL_SOURCES[str(self.home)]))

    def test_unchanged_files_are_not_reparsed(self):
        server.claude_models()
        with patch.object(server.json, 'load', side_effect=AssertionError('re-read')):
            server.claude_models()


class RefreshEndpointTests(Sources):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.versions = {'claude': '2.1.328', 'codex': '0.167.0'}

        def run(argv, timeout):
            self.calls.append(argv[1:])
            tool = os.path.basename(argv[0])
            if argv[1:] == ['--version']:
                return 0, self.versions[tool] + (' (Claude Code)' if tool == 'claude' else '')
            if argv[1] == 'view':
                return 0, {'@anthropic-ai/claude-code@latest': '2.1.330', '@openai/codex@latest': '0.167.0'}[argv[2]]
            if argv[1:] == ['update']:
                self.versions[tool] = '2.1.330'
                return 0, 'updated'
            raise AssertionError(argv)
        for p in (patch.object(model_updates, '_run', side_effect=run),
                  patch.object(model_updates.clis, 'cli', side_effect=lambda n: '/bin/' + n),
                  patch.object(model_updates, 'claude_channel', return_value='latest'),
                  patch.object(server, 'codex_models', return_value=[{'id': '', 'name': 'Session default'}, {'id': 'gpt-x', 'name': 'X'}])):
            p.start()
            self.addCleanup(p.stop)

    def post(self, body):
        handler = model_updates.ROUTES[('POST', '/api/models/refresh')]
        return handler(type('H', (), {'__module__': 'server'})(), {}, body)

    def test_check_reports_versions_new_models_and_stale_tiers_without_installing(self):
        res = self.post({'known': {'claude': ['claude-fable-5-1', 'claude-opus-5-5'], 'codex': ['gpt-x']}})
        self.assertNotIn(['update'], self.calls)
        self.assertEqual((res['claude']['version_before'], res['claude']['latest'], res['claude']['update_available']), ('2.1.328', '2.1.330', True))
        self.assertFalse(res['codex']['update_available'])
        self.assertEqual([m['id'] for m in res['claude']['new_models']][:2], ['claude-fable-5-2', 'claude-fable-5-2[1m]'])
        self.assertEqual(res['codex']['new_models'], [])
        self.assertIn('claude-fable-5-2 is newer than claude-fable-5-1', res['tiers_stale'])
        self.assertTrue(res['checked_at'])

    def test_install_updates_only_the_cli_that_is_behind(self):
        with patch.object(model_updates, 'active_runs', return_value=[]):
            res = self.post({'install': True})
        self.assertEqual(self.calls.count(['update']), 1)
        self.assertEqual((res['claude']['version_after'], res['claude']['install']['ok']), ('2.1.330', True))
        self.assertNotIn('install', res['codex'])

    def test_install_is_skipped_while_the_broker_has_active_runs(self):
        with patch.object(model_updates, 'active_runs', return_value=[{'id': 'r', 'status': 'running'}]):
            res = self.post({'install': True})
        self.assertNotIn(['update'], self.calls)
        self.assertIn('1 active run', res['install_skipped'])
        self.assertEqual(res['claude']['version_after'], '2.1.328')

    def test_route_is_registered_as_a_plugin(self):
        self.assertIn('model_updates', server.PLUGIN_MODULES)


class ActiveRunsTests(unittest.TestCase):
    def test_terminal_runs_are_not_active(self):
        store = types.SimpleNamespace(list=lambda: [{'status': 'completed'}, {'status': 'running'}, {'status': 'remote_idle'}])
        broker = types.SimpleNamespace(TERMINAL={'completed', 'failed', 'interrupted'}, instance=lambda: types.SimpleNamespace(store=store))
        with patch.dict(sys.modules, {'broker': broker}):
            self.assertEqual(len(model_updates.active_runs()), 2)


if __name__ == '__main__':
    unittest.main()
