import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import accounts
import accounts_guard  # noqa: F401


class AccountPresentationTests(unittest.TestCase):
    def test_duplicate_requires_matching_provider_identity_and_keeps_routes(self):
        with tempfile.TemporaryDirectory() as root:
            profiles = []
            cache = {}
            for name, org in [('personal', 'one'), ('alias', 'one'), ('other', 'two'), ('unknown', '')]:
                directory = Path(root, name)
                directory.mkdir()
                if org:
                    (directory / '.claude.json').write_text(json.dumps({
                        'oauthAccount': {'accountUuid': 'same-user', 'organizationUuid': org},
                    }))
                aid = 'claude-' + name
                profiles.append({'id': aid, 'tool': 'claude', 'label': 'Stale label', 'config_dir': str(directory)})
                cache[aid] = {'logged_in': True, 'email': 'same@example.test', 'plan': 'Max'}
            data = {'accounts': profiles, 'cache': cache,
                    'state': {'active': {'claude': 'claude-personal'}, 'global_active': {}}}
            with patch.object(accounts, 'DATA', root):
                rows = {a['id']: a for a in accounts.payload(data)['accounts']}
            self.assertEqual(set(rows), set(cache))
            self.assertEqual(rows['claude-alias'].get('duplicate_of'), 'claude-personal')
            for name in ('personal', 'other', 'unknown'):
                self.assertIsNone(rows['claude-' + name].get('duplicate_of'))

    def test_usage_warning_uses_verified_email_instead_of_stale_profile_label(self):
        profile = {'id': 'claude-default', 'tool': 'claude', 'label': 'Corporate'}
        row = {'logged_in': True, 'email': 'personal@example.test',
               'windows': [{'kind': '7d', 'label': 'Weekly', 'percent': 100}]}
        warning, = accounts.account_warnings(profile, row)
        self.assertIn('personal@example.test', warning['message'])
        self.assertNotIn('Corporate', warning['message'])

    def test_pending_corporate_signin_queues_and_resumes_through_existing_guard(self):
        a = {'id': 'claude-work', 'tool': 'claude', 'label': 'Corporate', 'config_dir': '/fixture/work'}
        data = {'accounts': [], 'pending_accounts': [a], 'cache': {a['id']: {'logged_in': True}},
                'state': {'active': {}, 'global_active': {}}}
        with patch.object(accounts, 'load', return_value=data), patch.object(accounts, 'save'), \
                patch.object(accounts, '_login_activity', return_value='Activity is ambiguous') as guard, \
                patch.object(accounts, '_pending_renewals', {}), \
                patch.object(accounts, '_link_isolated_claude_projects'), \
                patch.object(accounts, '_claude_browser_login', return_value={'login_started': True}) as launch:
            blocked, status = accounts._h_login(None, None, {'id': a['id'], 'email_hint': 'company@example.test'})
            self.assertEqual((status, blocked.get('code')), (409, 'login_busy'))
            queued = accounts._h_login(None, None, {
                'id': a['id'], 'when_idle': True, 'email_hint': 'company@example.test',
            })
            self.assertTrue(queued['renewal_pending'])
            self.assertEqual(a.get('expected_email'), 'company@example.test')
            accounts._run_pending_renewals()
            launch.assert_not_called()
            guard.return_value = None
            accounts._run_pending_renewals()
            launch.assert_called_once_with(data, a)
            self.assertNotIn(a['id'], accounts._pending_renewals)

    def test_pending_profile_cannot_promote_the_wrong_browser_identity(self):
        a = {'id': 'claude-work', 'tool': 'claude', 'label': 'company@example.test', 'config_dir': '/fixture/work'}
        data = {'accounts': [], 'pending_accounts': [a], 'cache': {},
                'state': {'active': {}, 'global_active': {}}}
        with tempfile.TemporaryDirectory() as root, patch.object(accounts, 'DATA', root), \
                patch.object(accounts, 'load', return_value=data), patch.object(accounts, 'save'), \
                patch.object(accounts, '_fetch_claude', return_value={
                    'logged_in': True, 'email': 'personal@example.test',
                    'windows': [{'kind': '7d', 'percent': 100}],
                }) as fetch:
            accounts.refresh()
            value = accounts.verify(a['id'])
            self.assertEqual(value['accounts'], [])
            pending, = value['pending_accounts']
            self.assertIn('company@example.test', pending['error'])
            self.assertIn('personal@example.test', pending['error'])
            self.assertFalse(pending['windows'])
            fetch.return_value = {'logged_in': True, 'email': 'Company@example.test'}
            verified = accounts.verify(a['id'])
            self.assertEqual([row['id'] for row in verified['accounts']], [a['id']])

    def test_activity_from_a_different_provider_does_not_block_signin(self):
        from unittest.mock import Mock
        import sys

        provider_only = {'pid': 123, 'tool': 'codex', 'node': 'mac', 'status': '?'}
        live = Mock()
        live.mac_sessions.return_value = ([provider_only], [123])
        with patch.dict(sys.modules, {'live': live}):
            self.assertIsNone(accounts._login_activity({'tool': 'claude', 'id': 'claude-work'}))
            self.assertIsNotNone(accounts._login_activity({'tool': 'codex', 'id': 'codex-work'}))
            live.mac_sessions.return_value = ([provider_only], [456])
            self.assertIsNotNone(accounts._login_activity({'tool': 'claude', 'id': 'claude-work'}))

    def test_existing_matching_usage_notice_does_not_keep_the_previous_profile_label(self):
        a = {'id': 'claude-default', 'tool': 'claude', 'label': 'Corporate', 'config_dir': '/fixture/work'}
        data = {'accounts': [a], 'cache': {a['id']: {
            'logged_in': True, 'email': 'personal@example.test',
            'windows': [{'kind': '7d', 'label': 'Weekly', 'percent': 100, 'resets_at': 4102444800}],
        }}, 'state': {'active': {}, 'global_active': {}}}
        active = {'account_id': a['id'], 'family': a['id'] + ':usage:7d', 'kind': 'usage_high',
                  'at': 4102444800, 'message': 'Corporate is at 100% of its Weekly limit.'}
        archived = dict(active, archived_at=20)
        with patch.object(accounts, 'load', return_value=data), patch.object(
                accounts.notices, 'listing', return_value={'notices': [active, archived]}):
            rows = accounts.ROUTES[("GET", "/api/notices")](None, None, None)['notices']
        self.assertTrue(rows[0]['message'].startswith('personal@example.test is at 100% of its Weekly limit (resets '))
        self.assertEqual(rows[1]['message'], archived['message'])

    def test_claude_receives_expected_email_from_existing_pending_setup(self):
        from urllib.parse import parse_qs, urlsplit

        with tempfile.TemporaryDirectory() as root:
            executable = Path(root, 'fake-claude')
            executable.write_text(
                '#!/usr/bin/python3\nimport sys\nfrom urllib.parse import urlencode\n'
                'assert sys.argv[1:4] == ["auth", "login", "--email"]\n'
                'print("https://claude.com/cai/oauth/authorize?" + urlencode({"email":sys.argv[4]}), flush=True)\n'
                'sys.exit(0 if input() == "fixture-code" else 1)\n'
            )
            executable.chmod(0o700)
            a = {'id': 'claude-work', 'tool': 'claude', 'label': 'company@example.test', 'config_dir': root}
            data = {'accounts': [], 'pending_accounts': [a], 'cache': {},
                    'state': {'active': {}, 'global_active': {}}}
            with patch.object(accounts, 'load', return_value=data), \
                    patch.object(accounts, '_login_activity', return_value=None), \
                    patch.object(accounts, '_cli', return_value=str(executable)), \
                    patch.object(accounts, '_open_login_browser', return_value=False), \
                    patch.object(accounts, '_login_sessions', {}):
                result = accounts._h_login(None, None, {'id': a['id']})
                self.assertEqual(parse_qs(urlsplit(result['authorization_url']).query)['email'], ['company@example.test'])
                self.assertTrue(accounts.submit_login_code(a['id'], result['login_id'], 'fixture-code')['ok'])

    def test_existing_signin_does_not_wait_for_the_usage_refresh_lock(self):
        import threading

        a = {'id': 'claude-work', 'tool': 'claude', 'label': 'company@example.test', 'config_dir': '/fixture/work'}
        data = {'accounts': [], 'pending_accounts': [a], 'cache': {},
                'state': {'active': {}, 'global_active': {}}}
        finished = threading.Event()
        result = []

        def sign_in():
            try:
                result.append(accounts._h_login(None, None, {'id': a['id']}))
            finally:
                finished.set()

        with patch.object(accounts, 'load', return_value=data), patch.object(accounts, 'save') as save, \
                patch.object(accounts, '_login_activity', return_value=None), \
                patch.object(accounts, '_link_isolated_claude_projects'), \
                patch.object(accounts, '_claude_browser_login', return_value={'login_started': True}):
            worker = threading.Thread(target=sign_in)
            try:
                with accounts.LOCK:
                    worker.start()
                    self.assertTrue(finished.wait(1), 'existing sign-in is independent of quota refresh')
            finally:
                worker.join(2)
            self.assertEqual(result, [{'login_started': True}])
            save.assert_not_called()

    def test_new_expected_email_cannot_overwrite_an_identity_saved_after_snapshot(self):
        before = {'id': 'claude-work', 'tool': 'claude', 'label': 'Corporate', 'config_dir': '/fixture/work'}
        after = dict(before, expected_email='correct@example.test')
        data = {'accounts': [], 'cache': {}, 'state': {'active': {}, 'global_active': {}}}
        with patch.object(accounts, 'load', side_effect=[dict(data, pending_accounts=[before]),
                                                        dict(data, pending_accounts=[after])]), \
                patch.object(accounts, 'save') as save, \
                patch.object(accounts, '_claude_browser_login', return_value={'login_started': True}) as launch:
            response = accounts._h_login(None, None, {'id': before['id'], 'email_hint': 'other@example.test'})
        self.assertIsInstance(response, tuple, 'a concurrent expected address rejects the conflicting input')
        payload, status = response
        self.assertEqual(status, 400)
        self.assertIn('correct@example.test', payload['error'])
        save.assert_not_called()
        launch.assert_not_called()

    def test_first_address_wait_is_bounded_when_usage_refresh_owns_the_registry(self):
        import threading

        a = {'id': 'claude-work', 'tool': 'claude', 'label': 'Corporate', 'config_dir': '/fixture/work'}
        data = {'accounts': [], 'pending_accounts': [a], 'cache': {},
                'state': {'active': {}, 'global_active': {}}}
        finished = threading.Event()
        result = []

        def sign_in():
            try:
                result.append(accounts._h_login(None, None, {'id': a['id'], 'email_hint': 'company@example.test'}))
            finally:
                finished.set()

        with patch.object(accounts, 'load', return_value=data), patch.object(accounts, 'save') as save, \
                patch.object(accounts, '_login_activity', return_value=None), \
                patch.object(accounts, '_link_isolated_claude_projects'), patch.object(accounts, '_claude_browser_login') as launch:
            worker = threading.Thread(target=sign_in)
            try:
                with accounts.LOCK:
                    worker.start()
                    self.assertTrue(finished.wait(3), 'user should see a retry without waiting for quota refresh')
            finally:
                worker.join(2)
            payload, status = result[0]
            self.assertEqual(status, 400)
            self.assertIn('Try sign-in again', payload['error'])
            self.assertNotIn('expected_email', a)
            launch.assert_not_called()
            save.assert_not_called()
