"""Account-scoped usage fixtures; no native login or provider requests."""
import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import accounts
import accounts_guard  # noqa: F401


class AccountUsageTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(accounts, '_login_activity', return_value=None)
        p.start();self.addCleanup(p.stop)
    def data(self):
        return {'state': {'global_active': {}}, 'accounts': [
            {'id': 'codex-personal', 'tool': 'codex', 'config_dir': '/fixture/a b'}]}

    def fetch(self, usage):
        auth = {'tokens': {'access_token': 'fixture-token', 'account_id': 'fixture-id'}}
        with patch.object(accounts, '_codex_auth', return_value=auth), \
                patch.object(accounts, '_codex_live_usage', return_value=usage) as live, \
                patch.object(accounts, '_newest_rate_limits') as rollout:
            record = accounts._fetch_codex(self.data(), self.data()['accounts'][0])
        live.assert_called_once_with('fixture-token', 'fixture-id')
        rollout.assert_not_called()
        return record

    def test_live_edu_window_classified_by_duration_and_balance_unknown(self):
        rec = self.fetch({'plan_type': 'edu_pro', 'rate_limit': {
            'primary_window': {'used_percent': 4, 'limit_window_seconds': 604800, 'reset_at': 2000000000}},
            'credits': {'has_credits': True, 'balance': None, 'unlimited': False}})
        self.assertEqual(rec['plan'], 'Edu Pro')
        self.assertEqual(rec['windows'][0]['kind'], '7d')
        self.assertIsNone(rec['credits']['balance'])
        self.assertTrue(rec['quota_attributed'])
        self.assertEqual(rec['error'], '')

    def test_workspace_credit_allowance_is_measured_quota(self):
        rec = self.fetch({'plan_type': 'business', 'rate_limit': None,
                         'spend_control': {'individual_limit': {
                             'unit': 'credit', 'source': 'workspace_spend_controls',
                             'limit': '100', 'used': '15', 'remaining': '85',
                             'used_percent': 15, 'reset_at': 2000000000}}})
        self.assertEqual(rec['windows'][0]['percent'], 15)
        self.assertEqual(rec['windows'][0]['kind'], 'credits')
        self.assertEqual(rec['spend_control']['remaining'], '85')

    def test_unknown_model_scopes_prevent_partial_quota_routing(self):
        rec = self.fetch({'additional_rate_limits': [{'new_model_scope': {}}]})
        self.assertIn('model-specific limits', rec['error'])

    def test_duplicate_add_does_not_reuse_existing_login(self):
        with patch.object(accounts, 'load', return_value=self.data()), \
                patch.object(accounts.os, 'makedirs'), patch.object(accounts, 'save') as save:
            with self.assertRaises(ValueError):
                accounts.add('codex', 'personal')
        save.assert_not_called()

    def test_usage_request_checks_exact_account_binding(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.StringIO(json.dumps({'account_id': 'wrong'})))
        response.__exit__ = Mock(return_value=False)
        with patch.object(accounts.urllib.request, 'urlopen', return_value=response) as get:
            with self.assertRaises(ValueError):
                accounts._codex_live_usage('fixture', 'expected')
        request = get.call_args.args[0]
        self.assertEqual(request.get_header('Chatgpt-account-id'), 'expected')
        self.assertEqual(request.full_url, 'https://chatgpt.com/backend-api/wham/usage')

    def test_unattributed_fallback_never_supplies_plan_credit_or_quota(self):
        auth = {'tokens': {'access_token': 'fixture', 'account_id': 'a'}}
        rate = {'plan_type': 'business', 'credits': {'balance': 99},
                'primary': {'used_percent': 0, 'window_minutes': 300}}
        with patch.object(accounts, '_codex_auth', return_value=auth), \
                patch.object(accounts, '_codex_live_usage', side_effect=OSError), \
                patch.object(accounts, '_newest_rate_limits', return_value=(rate, 1000)):
            rec = accounts._fetch_codex(self.data(), self.data()['accounts'][0])
        self.assertEqual(rec['windows'], [])
        self.assertIsNone(rec['credits'])
        self.assertNotEqual(rec['plan'], 'Business')

    def test_login_opens_only_explicit_isolated_profile_and_quotes_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(accounts, 'DATA', tmp), \
                patch.object(accounts, 'load', return_value=self.data()), \
                patch.object(accounts, '_cli', return_value='/bin/codex'), \
                patch.object(accounts.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertTrue(accounts.start_login('codex-personal')['login_started'])
            self.assertEqual(run.call_args.args[0][:4], ["/usr/bin/open", "-g", "-a", "Terminal"])
            self.assertIn("env 'CODEX_HOME=/fixture/a b' /bin/codex login", Path(run.call_args.args[0][-1]).read_text())

    def test_login_rejects_missing_profile_but_allows_global_renewal(self):
        d = self.data()
        d['state']['global_active']['codex'] = 'codex-personal'
        with patch.object(accounts, 'load', return_value=d), patch.object(accounts.subprocess, 'run') as run, patch.object(accounts, '_codex_browser_login', return_value={'ok':True}) as native:
            with self.assertRaises(ValueError):
                accounts.start_login('missing')
            self.assertTrue(accounts.start_login('codex-personal')['ok'])
            native.assert_called_once()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
