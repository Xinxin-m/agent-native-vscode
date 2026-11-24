import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import accounts
import accounts_guard  # noqa: F401

class OnboardingTests(unittest.TestCase):
 def setUp(self):
  p=patch.object(accounts,'_login_activity',return_value=None);p.start();self.addCleanup(p.stop)
 def data(self):
  return {'accounts':[],'pending_accounts':[], 'cache':{},'state':{'active':{},'global_active':{}}}
 def test_new_profile_stays_pending_until_verified(self):
  d=self.data()
  with patch.object(accounts,'load',return_value=d),patch.object(accounts,'save'),patch.object(accounts.os,'makedirs'):
   result=accounts.add('codex','someone@example.test')
  self.assertEqual(d['accounts'],[])
  self.assertEqual(d['pending_accounts'][0]['id'],result['id'])
 def test_payload_does_not_call_unverified_profiles_connected(self):
  d=self.data();d['accounts']=[{'id':'codex-x','tool':'codex','label':'name only','config_dir':'/fixture'}]
  value=accounts.payload(d)
  self.assertEqual(value['accounts'],[]);self.assertEqual(len(value['pending_accounts']),1)
 def test_verify_promotes_only_logged_in_identity(self):
  d=self.data();a={'id':'codex-x','tool':'codex','label':'x','config_dir':'/fixture'};d['pending_accounts']=[a]
  with patch.object(accounts,'load',return_value=d),patch.object(accounts,'save'),patch.object(accounts,'_fetch_codex',return_value={'logged_in':True,'email':'x@example.test','windows':[]}):
   value=accounts.verify(a['id'])
  self.assertEqual(d['accounts'],[a]);self.assertEqual(d['pending_accounts'],[])
  self.assertEqual(value['accounts'][0]['email'],'x@example.test')
 def test_native_login_opens_private_command_without_applescript(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=self.data();d['pending_accounts']=[{'id':'codex-x','tool':'codex','config_dir':'/fixture/a b'}]
   with patch.object(accounts,'DATA',tmp),patch.object(accounts,'load',return_value=d),patch.object(accounts,'_cli',return_value='/bin/codex'),patch.object(accounts.sys,'platform','darwin'),patch.object(accounts.subprocess,'run',return_value=Mock(returncode=0)) as run:
    result=accounts.start_login('codex-x')
    args=run.call_args.args[0];self.assertEqual(args[:4],['/usr/bin/open','-g','-a','Terminal']);self.assertTrue(result['login_started'])
    content=Path(args[4]).read_text();self.assertIn("'CODEX_HOME=/fixture/a b'",content);self.assertNotIn('osascript',content)
    self.assertEqual(Path(args[4]).stat().st_mode&0o777,0o700)

 def test_identity_without_login_never_promotes(self):
  d=self.data();a={'id':'codex-x','tool':'codex','label':'x','config_dir':'/fixture'};d['pending_accounts']=[a]
  with patch.object(accounts,'load',return_value=d),patch.object(accounts,'save'),patch.object(accounts,'_fetch_codex',return_value={'logged_in':False,'email':'x@example.test'}):
   value=accounts.verify(a['id'])
  self.assertEqual(value['accounts'],[]);self.assertEqual(d['pending_accounts'],[a])
 def test_launcher_timeout_returns_actionable_command(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=self.data();d['pending_accounts']=[{'id':'codex-x','tool':'codex','config_dir':'/fixture'}]
   with patch.object(accounts,'DATA',tmp),patch.object(accounts,'load',return_value=d),patch.object(accounts,'_cli',return_value='/bin/codex'),patch.object(accounts.sys,'platform','darwin'),patch.object(accounts.subprocess,'run',side_effect=accounts.subprocess.TimeoutExpired('open',10)):
    result=accounts.start_login('codex-x')
   self.assertFalse(result['login_started']);self.assertIn('codex login',result['login_cmd']);self.assertNotIn('TimeoutExpired',result['message'])

class ClaudeBrowserLoginTests(unittest.TestCase):
 def setUp(self):
  for p in [patch.object(accounts,'_login_activity',return_value=None),patch.object(accounts,'_open_login_browser',return_value=True)]:
   p.start();self.addCleanup(p.stop)
 def test_default_login_returns_browser_url_and_accepts_code(self):
  import os
  with tempfile.TemporaryDirectory() as tmp:
   executable=Path(tmp)/'fake-claude'
   executable.write_text('#!/usr/bin/python3\nimport sys\nprint("https://claude.com/cai/oauth/authorize?state=fixture",flush=True)\ncode=input()\nsys.exit(0 if code=="fixture-code" else 1)\n')
   executable.chmod(0o700)
   d={'accounts':[{'id':'claude-default','tool':'claude','config_dir':'/fixture/default'}],'pending_accounts':[],'state':{'global_active':{'claude':'claude-default'}}}
   with patch.object(accounts,'load',return_value=d),patch.object(accounts,'_cli',return_value=str(executable)):
    result=accounts.start_login('claude-default')
    self.assertEqual(result['authorization_url'],'https://claude.com/cai/oauth/authorize?state=fixture')
    self.assertTrue(result['login_started'])
    done=accounts.submit_login_code('claude-default',result['login_id'],'fixture-code')
    self.assertTrue(done['ok'])
 def test_login_code_cannot_target_different_account(self):
  with self.assertRaises(ValueError):
   accounts.submit_login_code('other','missing','fixture-code')
 def test_inactive_default_can_start_isolated_login_without_global_switch(self):
  d={'accounts':[{'id':'claude-default','tool':'claude','config_dir':'/fixture/default'}],
     'pending_accounts':[],'state':{'global_active':{'claude':'claude-other'}}}
  with patch.object(accounts,'load',return_value=d),patch.object(accounts,'_claude_browser_login',return_value={'ok':True}) as launch,patch.object(accounts,'switch') as switch:
   self.assertTrue(accounts.start_login('claude-default')['ok'])
  launch.assert_called_once();switch.assert_not_called()
