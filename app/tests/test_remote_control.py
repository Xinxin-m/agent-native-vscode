"""Synthetic providers only: no user inference, remote registration, or real sessions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import stream

FAKE_CLAUDE = r"""
import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if m.get('type')=='control_request':
  request=m['request'];rid=m['request_id']
  if request['subtype']=='initialize':value={'remote_control_available':True}
  elif request['subtype']=='remote_control':value={'session_url':'https://claude.ai/code/session_synthetic'} if request['enabled'] else {}
  else:break
  print(json.dumps({'type':'control_response','response':{'subtype':'success','request_id':rid,'response':value}}),flush=True)
 elif m.get('type')=='user':
  print(json.dumps({'type':'system','subtype':'init','session_id':'synthetic'}),flush=True)
  print(json.dumps({'type':'result','session_id':'synthetic','result':'synthetic reply'}),flush=True)
"""
class NativeCommandsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=broker.RunStore(self.tmp.name);self.controller=broker.Broker(self.store)
 def tearDown(self):
  for rid,ctrl in list(self.controller.active.items()):
   if self.store.get(rid)['status'] not in broker.TERMINAL:self.controller.control(rid,{'action':'interrupt'})
   ctrl['finished'].wait(8)
  self.tmp.cleanup()
 def wait(self,rid,predicate):
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   value=self.store.get(rid)
   if predicate(value):return value
   time.sleep(.01)
  self.fail('Timed out: '+repr(self.store.get(rid)))
 def test_remote_owns_one_process_across_two_turns_and_disconnects(self):
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',FAKE_CLAUDE],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'),patch.object(self.controller,'_persist') as persist:
   run=self.controller.start(spec);rid=run['id'];initial=self.wait(rid,lambda r:r['status']=='remote_idle')
   self.assertEqual(initial['remote_control']['status'],'connected');pid=initial['process_pid']
   with self.assertRaises(broker.SessionBusy):self.controller.start(spec)
   for n in range(2):
    body={'action':'message','message':'turn '+str(n),'request_id':'request'+str(n)}
    self.controller.control(rid,body);self.controller.control(rid,body)
    result=self.wait(rid,lambda r:r['status']=='remote_idle')
    self.assertEqual(result['process_pid'],pid)
    self.assertEqual(result['text'],'synthetic reply')
   self.assertEqual(persist.call_count,2)
   self.assertEqual(len([e for e in self.store.events(rid) if e['event']=='remote_turn_start']),2)
   self.controller.control(rid,{'action':'remote_control','enabled':False})
   done=self.wait(rid,lambda r:r['status'] in broker.TERMINAL)
   self.assertEqual(done['status'],'completed');self.assertEqual(done['remote_control']['status'],'disconnected')
 def test_disconnect_during_turn_preserves_final_reply(self):
  delayed=FAKE_CLAUDE.replace('import sys,json','import sys,json,threading').replace("print(json.dumps({'type':'result','session_id':'synthetic','result':'synthetic reply'}),flush=True)","threading.Timer(.2,lambda:print(json.dumps({'type':'result','session_id':'synthetic','result':'synthetic reply'}),flush=True)).start()")
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',delayed],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'),patch.object(self.controller,'_persist') as persist:
   rid=self.controller.start(spec)['id'];self.wait(rid,lambda r:r['status']=='remote_idle')
   self.controller.control(rid,{'action':'message','message':'finish this turn','request_id':'disconnect-race'})
   self.controller.control(rid,{'action':'remote_control','enabled':False})
   done=self.wait(rid,lambda r:r['status'] in broker.TERMINAL)
   self.assertEqual(done['status'],'completed');self.assertEqual(done['text'],'synthetic reply')
   self.assertEqual(persist.call_count,1);self.assertEqual(persist.call_args.args[0]['message'],'finish this turn')
 def test_remote_not_connected_without_valid_receipt(self):
  run=self.store.create({'provider':'claude','operation':'remote_control'});ctrl={}
  with self.assertRaisesRegex(ValueError,'valid Remote Control'):
   self.controller._claude_control(run['id'],run,ctrl,{'type':'control_response','response':{'request_id':'remote-on','subtype':'success','response':{'session_url':'http://evil.invalid'}}})
  self.assertFalse(ctrl.get('remote_connected'))
 def test_disabling_remote_during_work_does_not_terminate_the_turn(self):
  run=self.store.create({'provider':'claude','operation':'remote_control','status':'running'});ctrl={}
  self.controller._claude_control(run['id'],run,ctrl,{'type':'control_response','response':{'request_id':'remote-off','subtype':'success','response':{}}})
  self.assertTrue(ctrl['remote_disabling']);self.assertFalse(ctrl['remote_finish'])
 def test_remote_policy_failure_ignores_stale_epoch(self):
  run=self.store.create({'provider':'claude','operation':'remote_control','remote_control':{'status':'connected','bridge_epoch':2}})
  self.controller._remote_event(run['id'],run,{}, {'type':'system','subtype':'bridge_state','state':'failed','bridge_epoch':1})
  with self.assertRaisesRegex(ValueError,'disconnected'):
   self.controller._remote_event(run['id'],run,{}, {'type':'system','subtype':'bridge_state','state':'failed','bridge_epoch':2})
 def test_preparation_requires_existing_claude_and_no_handoff(self):
  index={'c':{'source':'claude','id':'c1','cwd':self.tmp.name,'account_id':'bound'},'x':{'source':'codex','id':'x1','cwd':self.tmp.name}}
  spec=broker.prepare_run({'key':'c','operation':'remote_control'},index,lambda _: {})
  self.assertEqual((spec['session_id'],spec['account_id']),('c1','bound'))
  for body in ({'operation':'remote_control','new':{'cwd':self.tmp.name}}, {'key':'x','operation':'remote_control'}, {'key':'c','operation':'remote_control','handoff':True}, {'key':'c','operation':'remote_control','account':'other'}):
   with self.assertRaises(ValueError):broker.prepare_run(body,index,lambda _: {})
 def test_disable_error_clears_connected_state(self):
  run=self.store.create({'provider':'claude','operation':'remote_control','status':'running','remote_control':{'status':'disconnecting'}})
  ctrl={'remote_connected':True,'protocol':'claude-stream'}
  self.controller._claude_control(run['id'],run,ctrl,{'type':'control_response','response':{'request_id':'remote-off','subtype':'error','error':'denied'}})
  self.assertFalse(ctrl.get('remote_connected'))
  self.assertEqual(self.store.get(run['id'])['remote_control']['status'],'error')
 def test_unexpected_provider_exit_while_connected_reports_failure(self):
  fake=FAKE_CLAUDE.replace("else:break\n  print", "else:break\n  print").replace("flush=True)\n elif m.get('type')=='user':", "flush=True)\n  if request['subtype']=='remote_control' and request['enabled']: break\n elif m.get('type')=='user':")
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',fake],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'):
   rid=self.controller.start(spec)['id'];done=self.wait(rid,lambda r:r['status'] in broker.TERMINAL)
   self.assertEqual(done['status'],'failed');self.assertNotEqual((done.get('remote_control') or {}).get('status'),'connected')
 def test_ambiguous_send_does_not_replay_same_request_id(self):
  import threading
  run=self.store.create({'provider':'claude','operation':'remote_control','status':'remote_idle','remote_control':{'status':'connected'}})
  self.store.update(run['id'],status='remote_idle')
  ctrl={'protocol':'claude-stream','turn_open':False,'control_lock':threading.RLock()};self.controller.active[run['id']]=ctrl
  self.addCleanup(self.controller.active.pop,run['id'],None)
  body={'action':'message','request_id':'same','message':'once'}
  with patch.object(self.controller,'_send',side_effect=ValueError('flush failed')) as send:
   with self.assertRaises(ValueError):self.controller.control(run['id'],body)
   with self.assertRaises(ValueError):self.controller.control(run['id'],body)
   self.assertEqual(send.call_count,1)
  self.controller.active.pop(run['id'])
 def test_disconnect_holds_deferred_compaction_and_queue(self):
  from unittest.mock import Mock
  run=self.store.create({'provider':'claude','operation':'remote_control','status':'running'})
  ctrl={'protocol':'claude-stream','remote_disabling':True,'context':{'tokens':600,'window':1000}}
  with patch.object(self.controller,'_send') as send,patch.object(self.controller,'_deliver_next') as deliver:
   self.controller._after_turn(run['id'],run,ctrl)
   send.assert_not_called();deliver.assert_not_called()
 def test_mismatched_session_event_cannot_rebind(self):
  fake=FAKE_CLAUDE.replace("'session_id':'synthetic','result':'synthetic reply'", "'session_id':'other','result':'synthetic reply'")
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',fake],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'):
   rid=self.controller.start(spec)['id'];self.wait(rid,lambda r:r['status']=='remote_idle')
   self.controller.control(rid,{'action':'message','message':'hello','request_id':'one'})
   time.sleep(.2)
   self.assertEqual(self.store.get(rid)['session_id'],'synthetic')
 def test_internal_compaction_replay_is_not_a_remote_turn(self):
  run=self.store.create({'provider':'claude','operation':'remote_control'})
  ctrl={'auto_compacting':True}
  self.controller._remote_event(run['id'],run,ctrl,{'type':'user','isReplay':True,'message':{'content':'/compact'}})
  self.assertNotIn('turn_message',ctrl)
  self.assertFalse(any(e['event']=='remote_turn_start' for e in self.store.events(run['id'])))
 def test_dispatched_next_turn_does_not_claim_remote_idle(self):
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',FAKE_CLAUDE],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'),patch.object(self.controller,'_persist'),patch.object(self.controller,'_after_turn',side_effect=lambda rid,run,ctrl:ctrl.update(turn_open=True)):
   rid=self.controller.start(spec)['id'];self.wait(rid,lambda r:r['status']=='remote_idle')
   self.controller.control(rid,{'action':'message','message':'first','request_id':'request'})
   self.wait(rid,lambda r:any(e['event']=='remote_turn_done' for e in self.store.events(rid)))
   self.assertEqual(self.store.get(rid)['status'],'running')
 def test_message_rejected_while_disconnect_pending(self):
  import threading
  run=self.store.create({'provider':'claude','operation':'remote_control','remote_control':{'status':'disconnecting'}});self.store.update(run['id'],status='remote_idle')
  ctrl={'protocol':'claude-stream','remote_disconnect_pending':True,'control_lock':threading.RLock()};self.controller.active[run['id']]=ctrl
  self.addCleanup(self.controller.active.pop,run['id'],None)
  with patch.object(self.controller,'_send') as send:
   with self.assertRaises(ValueError):self.controller.control(run['id'],{'action':'message','request_id':'x','message':'do not send'})
   send.assert_not_called()
  self.controller.active.pop(run['id'])
 def test_disable_receipt_waits_for_background_work(self):
  run=self.store.create({'provider':'claude','operation':'remote_control','status':'running'})
  ctrl={'protocol':'claude-stream','remote_disconnect_pending':True,'turn_open':False,'background':{'child':'task'}}
  self.controller._claude_control(run['id'],run,ctrl,{'type':'control_response','response':{'request_id':'remote-off','subtype':'success','response':{}}})
  self.assertFalse(ctrl.get('remote_finish',False))
 def test_disconnect_waits_for_background_completion(self):
  replacement="print(json.dumps({'type':'system','subtype':'task_started','task_id':'child','is_backgrounded':True}),flush=True); threading.Timer(.08,lambda:print(json.dumps({'type':'result','session_id':'synthetic','result':'done'}),flush=True)).start(); threading.Timer(.35,lambda:print(json.dumps({'type':'system','subtype':'task_notification','task_id':'child'}),flush=True)).start()"
  fake=FAKE_CLAUDE.replace('import sys,json','import sys,json,threading').replace("print(json.dumps({'type':'result','session_id':'synthetic','result':'synthetic reply'}),flush=True)",replacement)
  spec={'provider':'claude','operation':'remote_control','cwd':self.tmp.name,'host_id':'mac','session_id':'synthetic','key':'claude-synthetic','message':'/remote-control','prompt':'/remote-control'}
  launch={'argv':[sys.executable,'-u','-c',fake],'cwd':self.tmp.name,'env':os.environ.copy(),'protocol':'claude-stream'}
  with patch.object(self.controller,'_launch',return_value=launch),patch.object(self.controller,'_reject_live_resume'),patch.object(self.controller,'_persist'):
   rid=self.controller.start(spec)['id'];self.wait(rid,lambda r:r['status']=='remote_idle')
   self.controller.control(rid,{'action':'message','message':'work','request_id':'one'})
   self.wait(rid,lambda r:any(e['event']=='provider_event' and e['data']['event'].get('subtype')=='task_started' for e in self.store.events(rid)))
   self.controller.control(rid,{'action':'remote_control','enabled':False});time.sleep(.18)
   self.assertNotIn(self.store.get(rid)['status'],broker.TERMINAL)
   done=self.wait(rid,lambda r:r['status'] in broker.TERMINAL)
   self.assertEqual(done['status'],'completed')
if __name__=='__main__':unittest.main()
