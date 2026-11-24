"""Synthetic sign-in children only. No real credentials, browser, or provider IO."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import accounts
import accounts_guard  # noqa: F401
OPEN_BROWSER = accounts._open_login_browser


class AuthAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.data = {'accounts': [], 'pending_accounts': [], 'cache': {},
                     'state': {'active': {}, 'global_active': {}}}
        self.patches = [patch.object(accounts, 'HOME', str(self.home)), patch.object(accounts, 'DATA', str(self.base / 'data')),
                        patch.object(accounts, 'ACCOUNTS_FILE', str(self.base / 'accounts.json')),
                        patch.object(accounts, 'CONFIG_FILE', str(self.base / 'config.json')),
                        patch.object(accounts, 'BACKUPS', str(self.base / 'backups')), patch.object(accounts, 'load', return_value=self.data),
                        patch.object(accounts, 'save'), patch.object(accounts, '_login_activity', return_value=None),
                        patch.object(accounts, '_open_login_browser', return_value=True),
                        patch.object(accounts, '_pending_renewals', {}), patch.object(accounts, '_login_sessions', {})]
        self.patches.append(patch.object(accounts, '_login_starting', set()))
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        started = set(threading.enumerate())
        self.addCleanup(self.join_login_threads, started)

    @staticmethod
    def join_login_threads(started):
        # Sign-in threads keep running after done is set (they verify the account);
        # they must finish while the patches above are still active.
        for thread in set(threading.enumerate()) - started:
            thread.join(5)

    def account(self, tool, default=False, active=True):
        aid = tool + ('-default' if default else '-fixture')
        directory = self.home / ('.' + tool if default else tool + '-profile')
        directory.mkdir(exist_ok=True)
        a = {'id': aid, 'tool': tool, 'config_dir': str(directory), 'label': 'Fixture'}
        self.data['accounts'].append(a)
        self.data['state']['global_active'][tool] = aid if active else tool + '-other'
        self.data['state']['active'][tool] = aid
        return a

    def fake(self, tool, body):
        p = self.base / ('fake-' + tool)
        p.write_text('#!/usr/bin/python3\nimport os,sys,json,time\nfrom pathlib import Path\n' + body)
        p.chmod(0o700)
        return str(p)

    def test_stashed_default_claude_login_preserves_global_and_old_stash(self):
        a = self.account('claude', default=True, active=False)
        old = self.base / 'old-stash'
        old.mkdir()
        (old / 'claude-credentials.json').write_text('old fixture')
        a['stash'] = str(old)
        before = copy.deepcopy(self.data['state'])
        executable = self.fake('claude', '''p=Path(os.environ['CLAUDE_CONFIG_DIR'])
print('https://claude.com/cai/oauth/authorize?state=fixture',flush=True)
code=input()
assert code=='fixture-code'
(p/'.credentials.json').write_text(json.dumps({'claudeAiOauth':{'accessToken':'synthetic-token','refreshToken':'synthetic-refresh'}}))
(p/'account.json').write_text(json.dumps({'emailAddress':'fixture@example.invalid'}))
''')
        with patch.object(accounts, '_cli', return_value=executable), patch.object(accounts, '_kc_write') as keychain:
            login = accounts.start_login(a['id'])
            result = accounts.submit_login_code(a['id'], login['login_id'], 'fixture-code')
        self.assertTrue(result['ok'])
        keychain.assert_not_called()
        self.assertEqual(self.data['state'], before)
        self.assertEqual((old / 'claude-credentials.json').read_text(), 'old fixture')
        self.assertNotEqual(a['stash'], str(old))
        self.assertTrue(Path(a['login_config_dir'], 'projects').is_symlink())
        self.assertEqual(Path(a['stash'], 'claude-credentials.json').stat().st_mode & 0o777, 0o600)

    def test_isolated_login_links_missing_projects_without_replacing_existing(self):
        a = self.account('claude', active=False)
        projects = self.home / '.claude' / 'projects'
        projects.mkdir(parents=True)
        with patch.object(accounts, '_claude_browser_login', return_value={'ok': True}):
            accounts._start_login(a['id'])
            link = Path(a['config_dir'], 'projects')
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), projects.resolve())
            accounts._start_login(a['id'])
            self.assertTrue(link.is_symlink())
            link.unlink()
            link.mkdir()
            (link / 'existing').write_text('keep')
            accounts._start_login(a['id'])
            self.assertEqual((link / 'existing').read_text(), 'keep')

    def test_login_does_not_link_active_or_alias_profile(self):
        a = self.account('claude', active=True)
        projects = self.home / '.claude' / 'projects'
        projects.mkdir(parents=True)
        with patch.object(accounts, '_claude_browser_login', return_value={'ok': True}):
            accounts._start_login(a['id'])
            self.assertFalse(Path(a['config_dir'], 'projects').exists())
            self.data['state']['global_active']['claude'] = 'claude-other'
            a['config_dir'] = str(self.home / '.claude')
            accounts._start_login(a['id'])
            self.assertFalse(projects.is_symlink())

    def test_missing_profile_path_never_creates_link_in_working_directory(self):
        a = self.account('claude', active=False)
        (self.home / '.claude' / 'projects').mkdir(parents=True)
        a['config_dir'] = ''
        with tempfile.TemporaryDirectory() as unrelated:
            old = os.getcwd()
            os.chdir(unrelated)
            try:
                with patch.object(accounts, '_claude_browser_login', return_value={'ok': True}):
                    accounts._start_login(a['id'])
                self.assertFalse(os.path.lexists('projects'))
            finally:
                os.chdir(old)

    def test_codex_uses_native_oauth_rpc_without_terminal_or_provider_turn(self):
        a = self.account('codex', active=False)
        marker = self.base / 'complete'
        operations = self.base / 'operations.jsonl'
        executable = self.fake('codex', '''
for line in sys.stdin:
 m=json.loads(line)
 with open(''' + repr(str(operations)) + ''','a') as f:f.write(json.dumps(m)+'\\n')
 if m['method']=='initialize':print(json.dumps({'id':m['id'],'result':{}}),flush=True)
 if m['method']=='account/login/start':
  print(json.dumps({'id':m['id'],'result':{'type':'chatgpt','loginId':'fixture-native','authUrl':'https://auth.openai.com/oauth/authorize?state=fixture'}}),flush=True)
  while not Path(''' + repr(str(marker)) + ''').exists():time.sleep(.01)
  print(json.dumps({'method':'account/login/completed','params':{'loginId':'fixture-native','success':True}}),flush=True)
''')
        with patch.object(accounts, '_cli', return_value=executable), patch.object(accounts, '_terminal_login') as terminal:
            login = accounts.start_login(a['id'])
            self.assertTrue(login['browser_opened'])
            self.assertFalse(login['code_required'])
            again = accounts.start_login(a['id'])
            self.assertEqual(again['login_id'], login['login_id'])
            self.assertEqual(again['authorization_url'], login['authorization_url'])
            marker.touch()
            self.assertTrue(accounts._login_sessions[login['login_id']]['done'].wait(3))
            terminal.assert_not_called()
            self.assertEqual([json.loads(line)['method'] for line in operations.read_text().splitlines()],
                             ['initialize', 'initialized', 'account/login/start'])

    def test_busy_renewal_is_queued_without_touching_chat_or_credentials(self):
        a = self.account('claude')
        self.data['cache'][a['id']] = {'logged_in': True}
        with patch.object(accounts, '_login_activity', return_value='Busy'), patch.object(accounts, '_claude_browser_login') as launch, patch.object(accounts, 'switch') as switch:
            with self.assertRaisesRegex(ValueError, 'Renew when idle'):
                accounts.start_login(a['id'])
            result = accounts.start_login(a['id'], when_idle=True)
            self.assertTrue(result['renewal_pending'])
            accounts._run_pending_renewals()
            launch.assert_not_called()
            switch.assert_not_called()
        with patch.object(accounts, '_claude_browser_login', return_value={'login_started': True}) as launch:
            accounts._run_pending_renewals()
            launch.assert_called_once()
        self.assertNotIn(a['id'], accounts._pending_renewals)

    def test_default_selection_changes_only_future_launches(self):
        a = self.account('codex', active=False)
        self.data['cache'][a['id']] = {'logged_in': True, 'email': 'fixture@example.invalid'}
        before = copy.deepcopy(self.data['state']['global_active'])
        with patch.object(accounts, '_switch_codex') as move:
            accounts.set_default('codex', a['id'])
        self.assertEqual(self.data['state']['global_active'], before)
        self.assertEqual(self.data['state']['active']['codex'], a['id'])
        move.assert_not_called()

    def test_verify_rejects_unfinished_or_failed_auth_even_with_old_valid_identity(self):
        a = self.account('codex')
        self.data['cache'][a['id']] = {'logged_in': True, 'email': 'old@example.invalid'}
        session = {'id': a['id'], 'done': threading.Event(), 'result': None, 'error': None}
        accounts._login_sessions['fixture'] = session
        with patch.object(accounts, '_fetch_codex') as fetch:
            with self.assertRaisesRegex(ValueError, 'in progress'):
                accounts.verify(a['id'])
            session['error'] = 'Synthetic authorization failed'
            session['done'].set()
            with self.assertRaisesRegex(ValueError, 'Synthetic authorization failed'):
                accounts.verify(a['id'])
            fetch.assert_not_called()
        row = accounts.payload(self.data)['accounts'][0]
        self.assertEqual(row['login_error'], 'Synthetic authorization failed')

    def test_simultaneous_signin_clicks_share_one_starting_reservation(self):
        a = self.account('claude')
        entered, finish = threading.Event(), threading.Event()
        def login(_data, _account):
            entered.set();finish.wait(2)
            return {'ok': True, 'login_started': True}
        with patch.object(accounts, '_claude_browser_login', side_effect=login) as launch:
            thread = threading.Thread(target=lambda: accounts.start_login(a['id']))
            thread.start()
            self.assertTrue(entered.wait(1))
            result = accounts.start_login(a['id'])
            self.assertTrue(result['login_pending'])
            finish.set();thread.join(2)
            self.assertFalse(thread.is_alive())
            launch.assert_called_once()

    def test_owned_auth_child_is_reaped_if_it_ignores_terminate(self):
        proc = Mock()
        proc.wait.side_effect = [accounts.subprocess.TimeoutExpired('owned login', 3),
                                 accounts.subprocess.TimeoutExpired('owned login', 3), 0]
        accounts._reap_login_process(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 3)

    def test_login_expiry_uses_native_login_deadline_not_access_token(self):
        a = self.account('claude')
        with patch.object(accounts.subprocess, 'run', return_value=Mock(stdout=json.dumps({'loginExpiresAt': '2030-01-02T03:04:05Z', 'expiresAt': 1}))) as run:
            self.assertEqual(accounts._claude_login_expiry(self.data, a), '2030-01-02T03:04:05Z')
        self.assertEqual(run.call_args.args[0][-3:], ['auth', 'status', '--json'])

    def test_profile_native_status_survives_unavailable_credential_lookup(self):
        a = self.account('claude', active=False)
        Path(a['config_dir'], 'account.json').write_text(json.dumps({'emailAddress': 'fixture@example.invalid'}))
        cli = self.fake('claude', "print(json.dumps({'loggedIn': True, 'email': 'fixture@example.invalid', 'configDirectory': os.environ['CLAUDE_CONFIG_DIR'], 'loginExpiresAt': '2030-01-02T03:04:05Z'}))\n")
        with patch.object(accounts, '_cli', return_value=cli), patch.object(accounts, '_claude_creds', return_value=(None, 'not logged in')):
            result = accounts._fetch_claude(self.data, a)
        self.assertTrue(result['logged_in'])
        self.assertEqual(result['email'], 'fixture@example.invalid')
        self.assertEqual(result['windows'], [])
        self.assertEqual(result['loginExpiresAt'], '2030-01-02T03:04:05Z')

    def test_profile_native_status_rejects_different_identity_or_directory(self):
        a = self.account('claude', active=False)
        Path(a['config_dir'], 'account.json').write_text(json.dumps({'emailAddress': 'expected@example.invalid'}))
        for email, directory, code in [('other@example.invalid', a['config_dir'], 0), ('expected@example.invalid', '/other', 0), ('expected@example.invalid', a['config_dir'], 1)]:
            status = {'loggedIn': True, 'email': email, 'configDirectory': directory}
            with patch.object(accounts.subprocess, 'run', return_value=Mock(stdout=json.dumps(status), returncode=code)), patch.object(accounts, '_claude_creds', return_value=(None, 'not logged in')):
                self.assertFalse(accounts._fetch_claude(self.data, a)['logged_in'])

    def test_warning_dismissal_keys_advance_only_at_thresholds(self):
        a = self.account('claude')
        expiry = 2000000000
        record = {'loginExpiresAt': accounts._expiry_iso(expiry)}
        expected = [(90000, None), (86000, '24h'), (43000, '12h'), (3000, '1h'), (-1, 'expired')]
        keys = []
        for remain, threshold in expected:
            warnings = accounts.account_warnings(a, record, now=expiry-remain)
            if threshold is None:
                self.assertEqual(warnings, [])
            else:
                self.assertEqual(warnings[0]['threshold'], threshold)
                self.assertTrue(warnings[0]['renew_requires_idle'])
                keys.append(warnings[0]['key'])
        self.assertEqual(len(set(keys)), 4)

    def test_codex_access_expiry_is_not_a_login_expiry_warning(self):
        a = self.account('codex')
        self.assertEqual(accounts.account_warnings(a, {'accessTokenExpiresAt': '2000-01-01T00:00:00Z'}), [])

    def test_browser_open_is_background_default_browser_only(self):
        with patch.object(accounts.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertTrue(OPEN_BROWSER('https://example.invalid/login'))
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-g', 'https://example.invalid/login'])


class RegistryLeakTests(unittest.TestCase):
    def test_login_thread_finishing_after_a_test_never_writes_the_real_registry(self):
        sentinel = Path(tempfile.mkdtemp()) / 'real-accounts.json'
        self.addCleanup(lambda: sentinel.unlink(missing_ok=True))

        class Inner(AuthAccountsTests):
            def runTest(self):
                self.test_codex_uses_native_oauth_rpc_without_terminal_or_provider_turn()

        def slow_fetch(_data, _account):
            time.sleep(.3)
            return {'logged_in': False, 'error': 'not logged in', 'windows': []}

        with patch.object(accounts, 'ACCOUNTS_FILE', str(sentinel)), patch.object(accounts, '_fetch_codex', side_effect=slow_fetch):
            outcome = unittest.TextTestRunner(stream=open(os.devnull, 'w')).run(Inner())
            time.sleep(.6)
        self.assertTrue(outcome.wasSuccessful())
        self.assertFalse(sentinel.exists())


if __name__ == '__main__':
    unittest.main()
