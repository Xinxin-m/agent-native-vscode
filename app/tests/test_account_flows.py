import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import accounts
import accounts_guard  # noqa: F401


class AccountFlowTests(unittest.TestCase):
    """Add, rename, sign out and sign in again, on a temp HOME and fake CLIs."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        data = str(self.base / 'data')
        for p in [patch.dict(os.environ, {'HOME': str(self.home), 'CLAUDE_CONFIG_DIR': '', 'CODEX_HOME': ''}), patch.object(accounts, 'HOME', str(self.home)),
                  patch.object(accounts, 'DATA', data), patch.object(accounts, 'ACCOUNTS_FILE', str(self.base / 'accounts.json')),
                  patch.object(accounts, '_login_sessions', {}), patch.object(accounts, '_login_starting', set()),
                  patch.object(accounts, '_pending_renewals', {})]:
            p.start()
            self.addCleanup(p.stop)

    def seed(self, tool, aid, label, signed_in, pending=False):
        directory = self.home / ('.' + tool + '-profiles') / aid
        directory.mkdir(parents=True)
        d = accounts.load()
        a = {'id': aid, 'tool': tool, 'label': label, 'config_dir': str(directory)}
        d['pending_accounts' if pending else 'accounts'].append(a)
        d['cache'][aid] = {'logged_in': signed_in, 'email': 'x@example.test' if not pending else '', 'windows': []}
        accounts.save(d)
        return a

    def stub(self, tool, log):
        path = self.base / ('fake-' + tool)
        path.write_text('#!/usr/bin/python3\nimport os,sys,json\n'
                        f'open({str(log)!r},"a").write(json.dumps([sys.argv[1:],os.environ.get("CLAUDE_CONFIG_DIR") or None,os.environ.get("CODEX_HOME") or None])+"\\n")\n')
        path.chmod(0o700)
        return str(path)

    def test_add_without_label_names_accounts_in_order_and_stays_pending(self):
        first = accounts.add('codex')
        second = accounts.add('codex')
        self.assertEqual((first['id'], second['id']), ('codex-account-2', 'codex-account-3'))
        self.assertTrue(Path(first['config_dir']).is_dir())
        self.assertTrue(first['config_dir'].startswith(str(self.home)))
        p = accounts.payload()
        self.assertEqual(p['accounts'], [])
        row = next(a for a in p['pending_accounts'] if a['id'] == 'codex-account-2')
        self.assertEqual((row['label'], row['logged_in'], row['previously_used']), ('Account 2', False, False))
        self.assertEqual(accounts.add('claude', '  Easy Labs ')['id'], 'claude-easy-labs')
        with self.assertRaises(ValueError):
            accounts.add('claude', 'Easy Labs')

    def test_rename_persists_validates_and_keeps_id(self):
        a = self.seed('claude', 'claude-x', 'old', True)
        result = accounts.rename('claude-x', '  Easy Labs ')
        self.assertTrue(result['ok'])
        self.assertEqual(next(r for r in result['accounts'] if r['id'] == 'claude-x')['label'], 'Easy Labs')
        self.assertEqual(accounts._acct(accounts.load(), 'claude-x')['config_dir'], a['config_dir'])
        for bad in ('', '   ', None, 'x' * (accounts.LABEL_MAX + 1)):
            with self.assertRaises(ValueError):
                accounts.rename('claude-x', bad)
        with self.assertRaises(ValueError):
            accounts.rename('claude-nope', 'a')
        _, status = accounts._h_rename(None, None, {'id': 'claude-x', 'label': ''})
        self.assertEqual(status, 400)

    def test_rename_of_pending_email_label_keeps_the_identity_check(self):
        self.seed('claude', 'claude-w', 'work@example.test', False, pending=True)
        accounts.rename('claude-w', 'Work')
        d = accounts.load()
        self.assertEqual(accounts._pending_email(d, accounts._acct(d, 'claude-w')), 'work@example.test')

    def sign_out(self, tool):
        log = self.base / 'calls.jsonl'
        a = self.seed(tool, tool + '-x', 'X', True)
        other = self.seed(tool, tool + '-y', 'Y', True)
        with patch.object(accounts, '_cli', lambda name: self.stub(name, log)), \
                patch.object(accounts, '_login_activity', return_value=None), \
                patch.object(accounts, '_fetch_' + tool, return_value={'logged_in': False, 'error': 'not logged in', 'windows': [],
                                                                       'email': '', 'plan': ''}):
            result = accounts.logout(a['id'])
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return a, other, result, calls

    def test_claude_sign_out_runs_provider_logout_for_that_profile_only(self):
        a, other, result, calls = self.sign_out('claude')
        self.assertEqual(calls, [[['auth', 'logout'], a['config_dir'], None]])
        row = next(r for r in result['pending_accounts'] if r['id'] == a['id'])
        self.assertEqual((row['logged_in'], row['email'], row['previously_used']), (False, 'x@example.test', True))
        self.assertTrue(accounts.load()['cache'][other['id']]['logged_in'])

    def test_codex_sign_out_uses_that_codex_home(self):
        a, _other, result, calls = self.sign_out('codex')
        self.assertEqual(calls, [[['logout'], None, a['config_dir']]])
        self.assertTrue(result['ok'])

    def test_sign_out_refuses_when_signed_out_busy_or_still_signed_in(self):
        log = self.base / 'calls.jsonl'
        a = self.seed('claude', 'claude-x', 'X', True)
        gone = self.seed('claude', 'claude-gone', 'G', False)
        with patch.object(accounts, '_cli', lambda name: self.stub(name, log)):
            with self.assertRaises(ValueError):
                accounts.logout(gone['id'])
            with patch.object(accounts, '_login_activity', return_value='Busy'):
                body, status = accounts._h_logout(None, None, {'id': a['id']})
                self.assertEqual((status, body['code']), (409, 'logout_busy'))
                self.assertFalse(log.exists())
            with patch.object(accounts, '_login_activity', return_value=None), \
                    patch.object(accounts, '_fetch_claude', return_value={'logged_in': True, 'windows': []}):
                with self.assertRaises(RuntimeError):
                    accounts.logout(a['id'])
        self.assertTrue(accounts.load()['cache'][a['id']]['logged_in'])

    def test_signed_out_account_signs_in_even_while_chats_run(self):
        a = self.seed('claude', 'claude-x', 'X', False)
        with patch.object(accounts, '_login_activity', return_value='Busy') as guard, \
                patch.object(accounts, '_link_isolated_claude_projects'), \
                patch.object(accounts, '_claude_browser_login', return_value={'login_started': True}) as launch:
            result = accounts._h_login(None, None, {'id': a['id']})
        self.assertTrue(result['login_started'])
        guard.assert_not_called()
        launch.assert_called_once()

    def test_opener_is_background_open(self):
        with patch.object(accounts.subprocess, 'run') as run:
            accounts._open_login_browser('https://example.test/')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-g', 'https://example.test/'])


if __name__ == '__main__':
    unittest.main()
