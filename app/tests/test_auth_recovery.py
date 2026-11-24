import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import auth_failure,auth_recovery,broker,host_connector,indexer

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.chats=self.root/'chats';self.chats.mkdir();self.index=self.root/'index.json';self.key='claude-s'
  self.meta={'id':'s','source':'claude','cwd':str(self.root),'account_id':'claude-old','host_id':'mac'}
  self.chat=dict(self.meta,turns=[{'role':'user','text':'Earlier request'},{'role':'assistant','text':'Earlier answer'},{'role':'tool','text':'TOOL_SECRET'},{'role':'user','text':'Please continue'},{'role':'assistant','text':"You've hit your weekly limit · resets Nov 17 at 12am (America/Los_Angeles)",'error':True}])
  self.index.write_text(json.dumps({self.key:self.meta}));(self.chats/(self.key+'.json')).write_text(json.dumps(self.chat))
  self.obj=broker.Broker(broker.RunStore(self.root/'execution'));self.run=self.obj.store.create(dict(provider='claude',cwd=str(self.root),host_id='mac',session_id='s',key=self.key,account_id='claude-old',message='Please continue',prompt='Please continue',attachments=[]))
  self.obj.store.update(self.run['id'],status='failed',error=self.chat['turns'][-1]['text'],failure=auth_failure.classify('claude',self.chat['turns'][-1]['text'],'claude-old'))
  self.guards=[patch.object(broker.stream,'INDEX',str(self.index)),patch.object(broker.stream,'CHATS',str(self.chats)),patch('live.mac_sessions',return_value=([],[]))]
  for g in self.guards:g.start();self.addCleanup(g.stop)
  self.specs=[]
  def start(spec):self.specs.append(spec);return self.obj.store.create(spec)
  g=patch.object(self.obj,'start',side_effect=start);g.start();self.addCleanup(g.stop)
 def test_retry_same_native_identity_and_failed_message(self):
  r=auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'});s=self.specs[-1]
  self.assertEqual((s['provider'],s['session_id'],s['account_id'],s['message']),('claude','s','claude-old','Please continue'))
  again=auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'});self.assertEqual(r['run']['id'],again['run']['id']);self.assertEqual(len(self.specs),1)
 def test_account_requires_shared_store_and_keeps_native_session(self):
  with patch.object(host_connector,'shared_session_store',return_value='/shared') as proof,patch('live.mac_sessions',return_value=([],[4242])):
   auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'account','account_id':'claude-next'})
  proof.assert_called_once_with('claude','claude-old','claude-next');s=self.specs[-1];self.assertEqual(s['session_id'],'s');self.assertEqual(s['account_id'],'claude-next');self.assertEqual(s['shared_session_from'],'claude-old')
  for run in self.obj.store.list():self.obj.store.update(run['id'],status='failed')
  with patch.object(host_connector,'shared_session_store',side_effect=ValueError('not shared')):
   with self.assertRaisesRegex(ValueError,'not shared'):auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'account','account_id':'claude-other'})
 def test_handoff_excludes_error_tool_and_raw_journal(self):
  auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'handoff','provider':'codex'});s=self.specs[-1]
  self.assertEqual(s['source_turn'],3);self.assertEqual(s['session_id'],'');self.assertEqual(s['provider'],'codex')
  self.assertIn('Earlier answer',s['prompt']);self.assertNotIn('TOOL_SECRET',s['prompt']);self.assertNotIn('weekly limit',s['prompt']);self.assertNotIn('Recorded tool',s['prompt']);self.assertEqual(s['context_omitted_chars'],0)
 def test_login_uses_bound_account_normalizes_inline_flow(self):
  import accounts
  with patch.object(accounts,'start_login',return_value={'ok':True,'login_id':'login','authorization_url':'https://example.com/auth','code_required':True}) as start:
   r=auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'login','account_id':'wrong'})
  start.assert_called_once_with('claude-old',when_idle=True);self.assertTrue(r['login']['needs_code']);self.assertEqual(r['login']['url'],'https://example.com/auth');self.assertFalse(self.specs)
 def test_refuses_nonfailed_run(self):
  self.obj.store.update(self.run['id'],status='running')
  with self.assertRaisesRegex(ValueError,'failed run'):auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'})
 def test_key_only_native_error_recovery(self):
  auth_recovery.recover(self.obj,{'key':self.key,'action':'retry'});self.assertEqual(self.specs[-1]['message'],'Please continue')
 def test_shared_store_requires_realpath_equality(self):
  left=self.root/'left';right=self.root/'right';left.mkdir();right.mkdir();(left/'projects').mkdir();(right/'projects').symlink_to(left/'projects')
  with patch.object(host_connector,'local_profile',side_effect=lambda tool,account:(account,str(left if account=='a' else right),{})):
   self.assertEqual(host_connector.shared_session_store('claude','a','b'),str((left/'projects').resolve()))
  (right/'projects').unlink();(right/'projects').mkdir()
  with patch.object(host_connector,'local_profile',side_effect=lambda tool,account:(account,str(left if account=='a' else right),{})):
   with self.assertRaisesRegex(ValueError,'do not share'):host_connector.shared_session_store('claude','a','b')
 def test_failed_retry_can_be_explicitly_retried_after_signin(self):
  first=auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'})['run'];self.obj.store.update(first['id'],status='failed')
  second=auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'})['run'];self.assertNotEqual(first['id'],second['id'])
 def test_latest_accepted_message_is_retried(self):
  self.obj.store.update(self.run['id'],submitted_message='Latest follow-up',failed_message='Latest follow-up')
  auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry'});self.assertEqual(self.specs[-1]['message'],'Latest follow-up')
 def test_newer_native_user_turn_and_live_session_block_recovery(self):
  self.chat['turns'].append({'role':'user','text':'Already retrying'});(self.chats/(self.key+'.json')).write_text(json.dumps(self.chat))
  with self.assertRaisesRegex(ValueError,'newer user'):auth_recovery.recover(self.obj,{'key':self.key,'action':'retry'})
  self.chat['turns'].pop();(self.chats/(self.key+'.json')).write_text(json.dumps(self.chat))
  with patch('live.mac_sessions',return_value=([{'chat_key':self.key,'status':'busy'}],[])):
   with self.assertRaisesRegex(ValueError,'running'):auth_recovery.recover(self.obj,{'key':self.key,'action':'retry'})
  # live.mac_sessions also returns bare unclassified PIDs (ints); they must not crash recovery.
  with patch('live.mac_sessions',return_value=([],[4242,4343])):
   auth_recovery.recover(self.obj,{'key':self.key,'action':'retry'})
 def test_indexer_marks_native_error_turn_and_card_metadata(self):
  p=self.root/'native.jsonl';p.write_text(json.dumps({'type':'user','message':{'content':'Question'},'timestamp':'2026-11-16T00:00:00Z'})+'\n'+json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'Not logged in · Please run /login'}]},'isApiErrorMessage':True,'timestamp':'2026-11-16T00:01:00Z'})+'\n')
  chat=indexer.parse_claude(str(p));self.assertEqual(chat['turns'][-1]['failure']['kind'],'not_logged_in');self.assertEqual(indexer.meta_of(chat)['failure']['kind'],'not_logged_in')

 def native_binding_fixture(self):
  owner=self.root/'owner';actual=self.root/'actual';(owner/'projects'/'p').mkdir(parents=True);actual.mkdir();(actual/'projects').symlink_to(owner/'projects')
  transcript=owner/'projects'/'p'/'s.jsonl'
  transcript.write_text(json.dumps({'type':'user','sessionId':'s','cwd':str(self.root),'message':{'content':'Please continue'},'timestamp':'2026-11-16T00:00:00Z'})+'\n'+json.dumps({'type':'assistant','sessionId':'s','cwd':str(self.root),'isApiErrorMessage':True,'message':{'content':[{'type':'text','text':'Not logged in · Please run /login'}]},'timestamp':'2026-11-16T00:01:00Z'})+'\n')
  self.meta['file']=str(transcript);self.index.write_text(json.dumps({self.key:self.meta}))
  return owner,actual,transcript
 def test_indexed_native_login_uses_verified_actual_account_not_stale_index_binding(self):
  import accounts
  owner,actual,transcript=self.native_binding_fixture()
  with patch.object(host_connector,'local_profile',return_value=('claude-actual',str(actual),{})) as profile,patch.object(accounts,'start_login',return_value={'ok':True}) as login:
   result=auth_recovery.recover(self.obj,{'key':self.key,'action':'login','account_id':'claude-actual'})
  profile.assert_called_once_with('claude','claude-actual');login.assert_called_once_with('claude-actual',when_idle=True);self.assertEqual(result['account_id'],'claude-actual')
 def test_indexed_native_retry_accepts_actual_shared_store_but_broker_run_keeps_original_binding(self):
  owner,actual,transcript=self.native_binding_fixture()
  with patch.object(host_connector,'local_profile',return_value=('claude-actual',str(actual),{})):
   auth_recovery.recover(self.obj,{'key':self.key,'action':'retry','account_id':'claude-actual'})
  self.assertEqual(self.specs[-1]['account_id'],'claude-actual');self.assertEqual(self.specs[-1]['session_id'],'s')
  for run in self.obj.store.list():self.obj.store.update(run['id'],status='failed')
  with patch.object(host_connector,'local_profile',side_effect=AssertionError('Broker run binding must not be overridden')):
   auth_recovery.recover(self.obj,{'id':self.run['id'],'action':'retry','account_id':'claude-other'})
  self.assertEqual(self.specs[-1]['account_id'],'claude-old')
 def test_native_actual_account_must_match_provider_and_contain_physical_transcript(self):
  import accounts
  owner,actual,transcript=self.native_binding_fixture();independent=self.root/'independent';(independent/'projects').mkdir(parents=True);(independent/'projects'/'s.jsonl').write_text(transcript.read_text())
  with patch.object(host_connector,'local_profile',return_value=('claude-other',str(independent),{})),patch.object(accounts,'start_login') as login:
   with self.assertRaisesRegex(ValueError,'contain.*transcript'):auth_recovery.recover(self.obj,{'key':self.key,'action':'login','account_id':'claude-other'})
   login.assert_not_called()
  with patch.object(host_connector,'_local_accounts',return_value={'accounts':[{'id':'codex-other','tool':'codex','config_dir':str(actual)}],'state':{}}),patch.object(accounts,'start_login') as login:
   with self.assertRaisesRegex(ValueError,'not configured'):auth_recovery.recover(self.obj,{'key':self.key,'action':'login','account_id':'codex-other'})
   login.assert_not_called()
 def test_account_action_target_does_not_replace_native_source_binding(self):
  with patch.object(host_connector,'local_profile',side_effect=AssertionError('Target is not source')),patch.object(host_connector,'shared_session_store',return_value='/shared') as proof:
   auth_recovery.recover(self.obj,{'key':self.key,'action':'account','account_id':'claude-next'})
  proof.assert_called_once_with('claude','claude-old','claude-next');self.assertEqual(self.specs[-1]['shared_session_from'],'claude-old')

if __name__=='__main__':unittest.main()
