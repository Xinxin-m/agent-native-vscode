"""Fixture-only launch routing: never executes providers or changes credentials."""
import copy
import tempfile
import unittest
from unittest.mock import patch
import accounts
import accounts_guard  # noqa: F401
import host_connector as c


class RoutingTests(unittest.TestCase):
    def test_failover_requires_distinct_verified_fresh_identity_and_skips_attempts(self):
        d = self.data()
        d['cache']['claude-default']['email'] = 'one@example.invalid'
        d['cache']['claude-alt']['email'] = 'two@example.invalid'
        with patch.object(c, '_local_accounts', return_value=d), patch.object(accounts, '_auto_cfg', return_value={'enabled': True, 'threshold': 90}), patch.object(c, '_profile_directory', side_effect=lambda data, p: p['config_dir']), patch.object(c.time, 'time', return_value=1100):
            self.assertEqual(c.select_quota_failover('claude', 'sonnet', 'claude-default')['account_id'], 'claude-alt')
            self.assertIsNone(c.select_quota_failover('claude', 'sonnet', 'claude-default', ['claude-alt']))
            d['cache']['claude-alt']['email'] = 'ONE@example.invalid'
            self.assertIsNone(c.select_quota_failover('claude', 'sonnet', 'claude-default'))

    def test_confirmed_exhaustion_can_use_capacity_above_proactive_threshold(self):
        d = self.data()
        d['cache']['claude-default']['email'] = 'one@example.invalid'
        d['cache']['claude-alt']['email'] = 'two@example.invalid'
        d['cache']['claude-alt']['windows'][0]['percent'] = 95
        with patch.object(c, '_local_accounts', return_value=d), patch.object(accounts, '_auto_cfg', return_value={'enabled': True, 'threshold': 90}), patch.object(c, '_profile_directory', side_effect=lambda data, p: p['config_dir']), patch.object(c.time, 'time', return_value=1100):
            self.assertEqual(c.select_quota_failover('claude', 'sonnet', 'claude-default')['account_id'], 'claude-alt')
            d['cache']['claude-alt']['windows'][0]['percent'] = 100
            self.assertIsNone(c.select_quota_failover('claude', 'sonnet', 'claude-default'))
            d['cache']['claude-alt']['email'] = 'two@example.invalid'
            d['cache']['claude-alt']['fetched_at'] = 1
            self.assertIsNone(c.select_quota_failover('claude', 'sonnet', 'claude-default'))

    def data(self):
        return {'accounts': [{'id': 'claude-default', 'tool': 'claude', 'config_dir': '/default'},
                             {'id': 'claude-alt', 'tool': 'claude', 'config_dir': '/alt'}],
                'state': {'active': {'claude': 'claude-default'}, 'global_active': {}},
                'cache': {aid: {'logged_in': True, 'fetched_at': 1000,
                               'windows': [{'kind': '5h', 'percent': pct}, {'kind': '7d_opus', 'percent': 99}]}
                          for aid, pct in [('claude-default', 94), ('claude-alt', 20)]}}

    def resolve(self, data=None, **kwargs):
        return c.resolve_account(data or self.data(), 'claude', model='sonnet', now=1100, **kwargs)

    def test_applicable_model_and_shared_windows(self):
        d = self.data()
        d['cache']['claude-default']['windows'][0]['percent'] = 10
        self.assertEqual(self.resolve(d)['account_id'], 'claude-default')
        r = self.resolve()
        self.assertEqual(r['account_id'], 'claude-alt')
        self.assertEqual(r['candidates'][1]['percent'], 20)

    def test_stale_missing_and_invalid_usage_are_not_zero(self):
        for mutation in ({'fetched_at': 1}, {'windows': []}, {'windows': [{'kind': '5h', 'percent': None}]}, {'cached_at': 1}, {'error': 'offline'}):
            d = self.data()
            d['cache']['claude-alt'].update(mutation)
            result = self.resolve(d)
            self.assertEqual(result['account_id'], 'claude-default')
            self.assertIsNone(result['candidates'][1]['percent'])

    def test_explicit_and_session_bindings_are_pinned(self):
        self.assertEqual(self.resolve(account_id='claude-default')['reason'], 'explicit_account')
        self.assertEqual(self.resolve(session_account_id='claude-default')['reason'], 'session_binding')
        with self.assertRaises(ValueError):
            self.resolve(account_id='claude-alt', session_account_id='claude-default')

    def test_unknown_does_not_trigger_switch_and_input_is_unchanged(self):
        d = self.data()
        d['cache']['claude-default']['windows'] = []
        before = copy.deepcopy(d)
        self.assertEqual(self.resolve(d)['account_id'], 'claude-default')
        self.assertEqual(d, before)

    def test_disabled_or_ineligible_account_is_not_auto_selected(self):
        d = self.data()
        d['accounts'][1]['models'] = ['opus']
        self.assertEqual(self.resolve(d)['account_id'], 'claude-default')
        with self.assertRaises(ValueError):
            self.resolve(d, account_id='claude-alt')

    def test_pick_never_switches_credentials_or_saves(self):
        with patch.object(accounts, 'load', return_value=self.data()), patch.object(accounts, '_auto_cfg', return_value={'enabled': True}), patch.object(accounts, 'switch') as switch, patch.object(accounts, 'save') as save:
            accounts.pick('claude', model='sonnet')
        switch.assert_not_called()
        save.assert_not_called()

    def test_session_infers_unique_profile_without_rerouting(self):
        with tempfile.TemporaryDirectory() as td:
            d = self.data()
            for a in d['accounts']:
                a['config_dir'] = td
            with patch.object(c, '_local_accounts', return_value=d), patch.object(c, '_session_exists', side_effect=lambda tool, path, sid, cwd: path == td), self.assertRaises(ValueError):
                c.resolve_local_account('claude', model='sonnet', session_id='existing', cwd=td)

    def test_remote_unknown_usage_retains_default_and_records_reason(self):
        import hosts
        host = {'id': 'remote', 'transport': 'ssh', 'can_execute': True, 'machine_id': 'm',
                'execution_alias': 'worker', 'connector_path': '/connector.py',
                'accounts': [{'id': 'codex-default', 'tool': 'codex'}, {'id': 'codex-alt', 'tool': 'codex'}]}
        with patch.object(hosts, 'get_host', return_value=host):
            launch = c.build_cli_launch('remote', 'codex', '/research', model='gpt-6')
        self.assertEqual(launch['account_id'], 'codex-default')
        self.assertEqual(launch['account_selection']['reason'], 'usage_unavailable')

    def test_rate_limit_freshness_uses_observation_not_file_modification(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sessions/2026/01/01/rollout-test.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'timestamp': '2026-01-01T00:00:00Z', 'payload': {'rate_limits': {'primary': {'used_percent': 4}}}}) + '\n')
            _, observed = accounts._newest_rate_limits(td)
        self.assertEqual(observed, 1767225600)

    def test_codex_quota_without_proven_account_identity_is_unknown(self):
        auth = {'tokens': {'access_token': 'fixture', 'account_id': 'current'}}
        rate = {'primary': {'used_percent': 2, 'window_minutes': 300}}
        with patch.object(accounts, '_codex_auth', return_value=auth), patch.object(accounts, '_codex_live_usage', side_effect=OSError), patch.object(accounts, '_newest_rate_limits', return_value=(rate, 1000)):
            rec = accounts._fetch_codex({'state': {'global_active': {}}}, {'id': 'codex-default', 'config_dir': '/fixture'})
        self.assertIn('quota account attribution unknown', rec['error'])
        self.assertFalse(rec['windows'])

    def test_unique_session_binding_wins_over_exhausted_quota(self):
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path
            d = self.data()
            for index, profile in enumerate(d['accounts']):
                directory = Path(td) / str(index)
                directory.mkdir()
                profile['config_dir'] = str(directory)
            with patch.object(c, '_local_accounts', return_value=d), patch.object(c, '_session_exists', side_effect=lambda tool, path, sid, cwd: path.endswith('/0')):
                decision = c.resolve_local_account('claude', model='sonnet', session_id='existing', cwd=td)
            self.assertEqual(decision['account_id'], 'claude-default')
            self.assertEqual(decision['reason'], 'session_binding')

    def test_known_zero_is_valid_and_expired_window_is_unknown(self):
        d = self.data()
        d['cache']['claude-alt']['windows'][0]['percent'] = 0
        self.assertEqual(self.resolve(d)['account_id'], 'claude-alt')
        d['cache']['claude-alt']['windows'][0]['resets_at'] = '1970-01-01T00:01:00Z'
        self.assertIsNone(self.resolve(d)['candidates'][1]['percent'])

    def test_fractional_timezone_reset_is_known_but_naive_is_unknown(self):
        d = self.data()
        d['cache']['claude-alt']['windows'][0]['resets_at'] = '1970-01-01T00:30:00.123+00:00'
        self.assertEqual(self.resolve(d)['account_id'], 'claude-alt')
        d['cache']['claude-alt']['windows'][0]['resets_at'] = '1970-01-01T00:30:00'
        self.assertIsNone(self.resolve(d)['candidates'][1]['percent'])

    def test_background_refresh_does_not_select_or_switch(self):
        with patch.object(accounts, '_last_bg', 0), patch.object(accounts, '_config', return_value={}), patch.object(accounts, 'refresh') as refresh, patch.object(accounts, 'pick') as pick, patch.object(accounts, 'switch') as switch:
            accounts.background()
        refresh.assert_called_once()
        pick.assert_not_called()
        switch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

class SharedCodexOwnershipTests(unittest.TestCase):
    def test_shared_transcripts_match_native_creator_to_profile_auth(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shared=root/'shared';shared.mkdir()
            session=shared/'2026/01/01/rollout-test-existing.jsonl';session.parent.mkdir(parents=True)
            session.write_text(json.dumps({'type':'session_meta','payload':{'id':'existing','cwd':tmp,'creator_account_id':'native-owner'}})+'\n')
            profiles=[]
            for name,identity in [('codex-default','native-owner'),('codex-alt','other-owner')]:
                home=root/name;home.mkdir();(home/'sessions').symlink_to(shared,target_is_directory=True)
                (home/'auth.json').write_text(json.dumps({'tokens':{'account_id':identity}}));profiles.append({'id':name,'tool':'codex','config_dir':str(home)})
            data={'accounts':profiles,'state':{'active':{'codex':'codex-alt'},'global_active':{}},'cache':{}}
            with patch.object(c,'_local_accounts',return_value=data):
                result=c.resolve_local_account('codex',session_id='existing',cwd=__import__('os').path.realpath(tmp))
                self.assertEqual(result['account_id'],'codex-default');self.assertEqual(result['reason'],'session_binding')
                with self.assertRaises(ValueError):c.resolve_local_account('codex',account_id='codex-alt',session_id='existing',cwd=__import__('os').path.realpath(tmp))
                (root/'codex-alt/auth.json').write_text(json.dumps({'tokens':{'account_id':'native-owner'}}))
                # Both profiles now carry the creator's login: one account, the active profile.
                self.assertEqual(c.resolve_local_account('codex',session_id='existing',cwd=__import__('os').path.realpath(tmp))['account_id'],'codex-alt')
                self.assertEqual(c.resolve_local_account('codex',account_id='codex-alt',session_id='existing',cwd=__import__('os').path.realpath(tmp))['account_id'],'codex-alt')

    def test_creator_signed_in_nowhere_resumes_where_the_rollout_is_readable(self):
        """INPUT-12: the creating login was replaced; the transcript is still in every CODEX_HOME."""
        import json, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cwd=os.path.realpath(tmp);shared=root/'shared'
            session=shared/'2026/01/01/rollout-test-existing.jsonl';session.parent.mkdir(parents=True)
            session.write_text(json.dumps({'type':'session_meta','payload':{'id':'existing','cwd':tmp,'creator_account_id':'old-login'}})+'\n')
            profiles=[]
            for name,identity in [('codex-default','new-login'),('codex-personal','other-login'),('codex-local','')]:
                home=root/name;home.mkdir();(home/'sessions').symlink_to(shared,target_is_directory=True)
                if identity:(home/'auth.json').write_text(json.dumps({'tokens':{'account_id':identity}}))
                profiles.append({'id':name,'tool':'codex','config_dir':str(home)})
            profiles.append({'id':'codex-empty','tool':'codex','config_dir':str(root)})  # cannot read the rollout
            data={'accounts':profiles,'state':{'active':{'codex':'codex-default'},'global_active':{}},'cache':{}}
            with patch.object(c,'_local_accounts',return_value=data):
                resolve=lambda **kw:c.resolve_local_account('codex',session_id='existing',cwd=cwd,**kw)
                result=resolve()
                self.assertEqual(result['account_id'],'codex-default');self.assertEqual(result['ownership'],'creator_not_signed_in')
                # A stale index binding and the explicit "Continue on" choice both resume.
                self.assertEqual(resolve(account_id='codex-personal')['account_id'],'codex-personal')
                with self.assertRaises(c.SessionAccountMismatch) as caught:resolve(account_id='codex-empty')
                self.assertEqual(sorted(caught.exception.candidates),['codex-default','codex-local','codex-personal'])
                with patch.object(c,'_session_exists',return_value=False):
                    with self.assertRaises(c.AmbiguousSessionAccount) as missing:resolve()
                    self.assertEqual(missing.exception.candidates,[])
                # When the creator signs in again, ownership pins to that profile.
                (root/'codex-personal/auth.json').write_text(json.dumps({'tokens':{'account_id':'old-login'}}))
                self.assertEqual(resolve()['account_id'],'codex-personal');self.assertNotIn('ownership',resolve())
                with self.assertRaises(c.SessionAccountMismatch) as pinned:resolve(account_id='codex-default')
                self.assertEqual(pinned.exception.candidates,['codex-personal'])
                import clis, sys
                with patch.object(clis,'cli',return_value=sys.executable):  # argument construction only
                    launch=c.local_launch('codex',cwd,None,'existing',protocol='exec')
                self.assertEqual(launch['account_id'],'codex-personal');self.assertEqual(launch['argv'][1:4],['exec','resume','existing'])


class SharedLoginTests(unittest.TestCase):
    """Profiles that share one transcript store resolve by the provider's own login."""
    def setUp(self):
        import os
        from pathlib import Path
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.cwd=os.path.realpath(self.tmp.name)
        shared=root/'projects'/c.claude_project_dir(self.cwd);shared.mkdir(parents=True)
        (shared/'existing.jsonl').write_text('{}\n')
        self.homes={}
        for name in ('claude-default','claude-personal','claude-blank'):
            home=root/name;home.mkdir();(home/'projects').symlink_to(root/'projects',target_is_directory=True)
            self.homes[name]=home
        self.login('claude-default','user-1','org-1');self.login('claude-personal','user-1','org-1')
        (self.homes['claude-blank']/'.claude.json').write_text('{}')
        self.data={'accounts':[{'id':n,'tool':'claude','config_dir':str(h)} for n,h in self.homes.items()],
                   'state':{'active':{'claude':'claude-personal'},'global_active':{}},'cache':{}}

    def tearDown(self):
        self.tmp.cleanup()

    def login(self,name,user,org):
        import json
        (self.homes[name]/'.claude.json').write_text(json.dumps({'oauthAccount':{'accountUuid':user,'organizationUuid':org}}))

    def resolve(self,**kw):
        with patch.object(c,'_local_accounts',return_value=self.data):
            return c.resolve_local_account('claude',session_id='existing',cwd=self.cwd,**kw)

    def test_same_login_picks_active_profile_and_ignores_signed_out_profile(self):
        result=self.resolve()
        self.assertEqual(result['account_id'],'claude-personal');self.assertEqual(result['reason'],'session_binding')

    def test_same_login_falls_back_to_default_when_active_profile_is_not_a_candidate(self):
        self.data['state']['active']['claude']='claude-blank'
        self.assertEqual(self.resolve()['account_id'],'claude-default')

    def test_different_logins_continue_on_the_signed_in_active_profile(self):
        self.login('claude-personal','user-2','org-1')
        result=self.resolve()
        self.assertEqual(result['account_id'],'claude-personal');self.assertEqual(result['ownership'],'unattributed_active')

    def test_different_logins_stay_ambiguous_when_active_cannot_resume(self):
        self.login('claude-personal','user-2','org-1')
        for active in ('claude-blank','claude-elsewhere'):
            self.data['state']['active']['claude']=active
            with self.assertRaises(c.AmbiguousSessionAccount) as caught:self.resolve()
            self.assertEqual(sorted(caught.exception.candidates),['claude-blank','claude-default','claude-personal'])

    def test_explicit_account_is_still_checked_against_the_session(self):
        self.assertEqual(self.resolve(account_id='claude-default')['account_id'],'claude-default')
        self.data['accounts'].append({'id':'claude-other','tool':'claude','config_dir':self.tmp.name+'/missing'})
        with self.assertRaises(c.SessionAccountMismatch) as caught:self.resolve(account_id='claude-other')
        self.assertEqual(sorted(caught.exception.candidates),['claude-blank','claude-default','claude-personal'])

    def test_no_known_login_is_ambiguous(self):
        for name in self.homes:(self.homes[name]/'.claude.json').write_text('{}')
        with self.assertRaises(c.AmbiguousSessionAccount):self.resolve()
