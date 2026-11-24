import datetime,json,tempfile,unittest
from pathlib import Path
import auth_failure as a
AUTH='Failed to authenticate: this login reached its time limit and cannot be renewed. Run `claude auth login` to sign in again, or use `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN for unattended jobs'
LIMIT="You've hit your weekly limit · resets Nov 17 at 12am (America/Los_Angeles)"
class AuthFailureTests(unittest.TestCase):
 def test_real_strings(self):
  for provider,text,kind in [('claude',AUTH,'auth_expired'),('claude',LIMIT,'limit_reached'),('codex','Your access token could not be refreshed because your refresh token has expired. Please log out and sign in again.','auth_expired'),('codex','Your access token could not be refreshed because your refresh token was already used. Please log out and sign in again.','auth_expired'),('codex','You’ve hit your usage limit','limit_reached'),('claude','Not logged in · Please run /login','not_logged_in'),('codex','RateLimitExceeded','rate_limited'),('claude','unrelated network failure','other')]:
   with self.subTest(text=text):self.assertEqual(a.classify(provider,text,'p',at='2026-11-16T01:07:55Z')['kind'],kind)
 def test_absolute_reset_and_unknown_remain_distinct(self):
  self.assertEqual(a.classify('claude',LIMIT,at='2026-11-16T01:07:55Z')['resets_at'],'2026-11-17T08:00:00Z')
  self.assertIsNone(a.classify('codex','You’ve hit your usage limit')['resets_at'])
 def test_only_actual_error_events(self):
  self.assertIsNone(a.from_event('claude',{'type':'assistant','message':{'content':[{'type':'text','text':LIMIT}]}}))
  e={'type':'assistant','isApiErrorMessage':True,'error':'rate_limit','message':{'content':[{'type':'text','text':LIMIT}]}}
  self.assertEqual(a.from_event('claude',e,at='2026-11-16T01:07:55Z')['kind'],'limit_reached')
  self.assertIsNone(a.from_event('claude',{'type':'control_response','response':{'subtype':'error','error':'denied'}}))
 def test_structured_codex_reset(self):
  e={'method':'error','params':{'error':{'message':'You’ve hit your usage limit','codexErrorInfo':{'usageLimitExceeded':{'resetsAt':1794902400}}}}}
  self.assertEqual(a.from_event('codex',e)['kind'],'limit_reached')
  self.assertEqual(a.from_event('codex',e)['resets_at'],'2026-11-17T08:00:00Z')
 def test_native_codex_task_failure_and_nested_live_error(self):
  e={'type':'event_msg','timestamp':'2026-08-20T05:41:32Z','payload':{'type':'task_complete','error':{'message':"You've hit your usage limit. Try again at Aug 26th, 2026 8:11 AM.",'codex_error_info':'usage_limit_exceeded'}}}
  f=a.from_event('codex',e);self.assertEqual(f['kind'],'limit_reached');self.assertIsNotNone(f['resets_at'])
  e={'method':'turn/completed','params':{'turn':{'status':'failed','error':{'message':'Your access token could not be refreshed. Please log out and sign in again.'}}}}
  self.assertEqual(a.from_event('codex',e)['raw'],e['params']['turn']['error']['message'])
  self.assertEqual(a.classify('codex','Quota unavailable',structured={'codex_error_info':'usage_limit_exceeded'})['kind'],'limit_reached')
 def test_tool_result_is_never_retry_message(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'s.jsonl';rows=[{'type':'user','message':{'content':'Original request'}},{'type':'user','message':{'content':[{'type':'tool_result','content':'TOOL OUTPUT'}]}},{'type':'assistant','isApiErrorMessage':True,'message':{'content':[{'type':'text','text':AUTH}]}}]
   p.write_text(''.join(json.dumps(r)+'\n' for r in rows));self.assertEqual(a.transcript_failure('claude',p)['message'],'Original request')
 def test_background_notification_error_is_not_root_failure(self):
  e={'type':'result','is_error':True,'result':AUTH,'origin':{'kind':'task-notification'}};self.assertIsNone(a.from_event('claude',e))
 def test_transcript_error_cleared_by_later_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'s.jsonl';rows=[{'type':'user','message':{'content':'Keep working'}},{'type':'assistant','isApiErrorMessage':True,'error':'authentication_failed','message':{'content':[{'type':'text','text':AUTH}]},'timestamp':'2026-11-16T00:00:00Z'}]
   p.write_text(''.join(json.dumps(r)+'\n' for r in rows));r=a.transcript_failure('claude',p,'p');self.assertEqual(r['failure']['kind'],'auth_expired');self.assertEqual(r['message'],'Keep working');self.assertEqual(r['through_turn'],0)
   with p.open('a') as f:f.write(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'Succeeded'}]}})+'\n')
   self.assertIsNone(a.transcript_failure('claude',p,'p')['failure'])
if __name__=='__main__':unittest.main()
