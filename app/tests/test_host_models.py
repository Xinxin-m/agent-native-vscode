import unittest
from unittest.mock import patch
import host_models

class HostModelTests(unittest.TestCase):
    def setUp(self):
        host_models._CACHE.clear()
        host_models._LOCKS.clear()
        self.host = {'transport':'ssh','can_execute':True,'machine_id':'machine','job_id':'9','capabilities':['models'], 'accounts':[{'id':'acct','tool':'codex','models':['allowlist-is-not-discovery']}]}
        self.response = {'ok':True,'host_id':'cluster','machine_id':'machine','job_id':'9','provider':'codex','account_id':'acct','cwd':'/work','source':'host-cli','observed_at':1,'models':[{'id':'actual-model','name':'Actual','efforts':['high']}]}

    def discover(self):
        return host_models.discover('cluster','codex','acct','/work')

    def test_verified_remote_models_only(self):
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',return_value=self.response):
            self.assertEqual(self.discover()['models'][0]['id'],'actual-model')

    def test_success_cache_and_failed_requests(self):
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',return_value=self.response) as read:
            self.discover(); self.discover()
            self.assertEqual(read.call_count,1)
        host_models._CACHE.clear()
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',side_effect=ValueError('unavailable')) as read:
            for _ in range(2):
                with self.assertRaises(ValueError): self.discover()
            self.assertEqual(read.call_count,2)

    def test_over_128_sequential_contexts_evicts_and_releases_locks(self):
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector') as read:
            for i in range(140):
                cwd = '/work/' + str(i)
                read.return_value = dict(self.response, cwd=cwd)
                host_models.discover('cluster','codex','acct',cwd)
            self.assertEqual(len(host_models._CACHE),128)
            self.assertEqual(len(host_models._LOCKS),0)

    def test_concurrent_same_context_deduplicates(self):
        import concurrent.futures, threading
        started, release = threading.Event(), threading.Event()
        def remote(_):
            started.set(); self.assertTrue(release.wait(2)); return self.response
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',side_effect=remote) as read:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.discover); self.assertTrue(started.wait(2))
                second = pool.submit(self.discover); release.set()
                self.assertEqual(first.result()['models'], second.result()['models'])
            self.assertEqual(read.call_count,1)
            self.assertFalse(host_models._LOCKS)

    def test_binding_change_invalidates_and_disabled_rejects(self):
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',return_value=self.response) as read:
            self.discover()
            self.host['execution_alias']='changed'
            self.discover()
            self.assertEqual(read.call_count,2)
            self.host['accounts'][0]['disabled']=True
            with self.assertRaises(ValueError): self.discover()
            self.assertEqual(read.call_count,2)

    def test_bad_post_and_workspace_rejected(self):
        self.assertEqual(host_models.post_discover(None,{},[])[1],400)
        request = dict(host_id='cluster',machine_id='machine',job_id='9',tool='codex',account_id='acct',cwd='/work')
        with self.assertRaises(ValueError):
            host_models.validate_response(dict(self.response,cwd='/other'),request)

    def test_busy_slots_and_signed_out(self):
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models,'_read_connector') as read:
            self.assertTrue(host_models._REMOTE_SLOTS.acquire(False))
            self.assertTrue(host_models._REMOTE_SLOTS.acquire(False))
            try:
                with self.assertRaisesRegex(ValueError,'busy'): self.discover()
                read.assert_not_called()
            finally:
                host_models._REMOTE_SLOTS.release(); host_models._REMOTE_SLOTS.release()
            self.host['accounts'][0]['logged_in']=False
            with self.assertRaisesRegex(ValueError,'signed out'): self.discover()
            read.assert_not_called()

    def test_host_identity_mismatch_rejected(self):
        self.response['job_id'] = '10'
        with patch.object(host_models.hosts,'get_host',return_value=self.host), patch.object(host_models.host_connector,'ssh_command',return_value=['fake']), patch.object(host_models,'_read_connector',return_value=self.response):
            with self.assertRaisesRegex(ValueError,'identity'):
                self.discover()

    def test_stale_and_old_connector_fail_closed(self):
        for changes in ({'can_execute':False},{'capabilities':[]}):
            with self.subTest(changes=changes), patch.object(host_models.hosts,'get_host',return_value=dict(self.host,**changes)), patch.object(host_models,'_read_connector') as read:
                with self.assertRaises(ValueError): self.discover()
                read.assert_not_called()

    def test_wrong_account_and_local_fallback_rejected(self):
        for changes in ({'accounts':[]},{'transport':'local'}):
            with self.subTest(changes=changes), patch.object(host_models.hosts,'get_host',return_value=dict(self.host,**changes)), patch.object(host_models,'_read_connector') as read:
                with self.assertRaises(ValueError): self.discover()
                read.assert_not_called()

    def test_empty_or_malformed_catalog_rejected(self):
        request = dict(host_id='cluster',machine_id='machine',job_id='9',tool='codex',account_id='acct',cwd='/work')
        for models in ([],[{'id':'bad model','name':'bad','efforts':[]}], None):
            with self.subTest(models=models), self.assertRaises(ValueError):
                host_models.validate_response(dict(self.response,models=models),request)

class ProtocolFixtureTests(unittest.TestCase):
    def test_oversized_claude_catalog_rejected(self):
        import os, sys, subprocess, host_connector, clis
        original = subprocess.Popen
        script = "import sys,json; json.loads(sys.stdin.readline()); print(json.dumps({'type':'control_response','response':{'request_id':'library-models','response':{'models':[{'value':'model'}]*101}}}),flush=True)"
        def fake(argv, **kwargs): return original([sys.executable,'-u','-c',script], **kwargs)
        with patch.object(clis,'cli',return_value=sys.executable), patch.object(host_connector,'local_profile',return_value=('acct','/tmp',dict(os.environ))), patch.object(host_connector.subprocess,'Popen',side_effect=fake):
            with self.assertRaisesRegex(ValueError,'100 model limit'):
                host_connector.discover_models({'tool':'claude','account_id':'acct','cwd':'/tmp'},{'host_id':'cluster','machine_id':'machine','job_id':'9'})

    def test_metadata_only_provider_protocols(self):
        import os, sys, subprocess, host_connector, clis
        real_popen = subprocess.Popen
        for provider in ('claude', 'codex'):
            script = """import sys,json
r=json.loads(sys.stdin.readline())
if r.get('type')=='control_request':
 assert r['request']=={'subtype':'initialize'}
 print(json.dumps({'type':'control_response','response':{'request_id':'library-models','response':{'models':[{'value':'model-a','displayName':'Model A','supportsEffort':False,'supportedEffortLevels':['high']}], 'commands':[{'name':'compact','description':'Compact','builtin':True},{'name':'../invalid'},{'name':'workflow-launch-x'}]}}}),flush=True)
else:
 assert r['method']=='initialize'
 print(json.dumps({'id':1,'result':{}}),flush=True)
 assert json.loads(sys.stdin.readline())['method']=='initialized'
 assert json.loads(sys.stdin.readline())['method']=='model/list'
 print(json.dumps({'id':2,'result':{'data':[{'id':'model-b','displayName':'Model B','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}}),flush=True)
"""
            def fake_spawn(argv, **kwargs):
                return real_popen([sys.executable, '-u', '-c', script], **kwargs)
            with self.subTest(provider=provider), patch.object(clis,'cli',return_value=sys.executable), patch.object(host_connector,'local_profile',return_value=('acct','/tmp',dict(os.environ))), patch.object(host_connector.subprocess,'Popen',side_effect=fake_spawn):
                result=host_connector.discover_models({'tool':provider,'account_id':'acct','cwd':'/tmp'},{'host_id':'cluster','machine_id':'machine','job_id':'9'})
                self.assertEqual(result['models'][0]['id'], 'model-a' if provider=='claude' else 'model-b')
                self.assertEqual(result['models'][0]['efforts'], [] if provider=='claude' else ['high'])
                if provider == 'claude':
                    self.assertEqual([c['name'] for c in result['commands']],['compact'])
                    self.assertEqual(result['commands'][0]['insert'],'/compact')

if __name__ == '__main__': unittest.main()
