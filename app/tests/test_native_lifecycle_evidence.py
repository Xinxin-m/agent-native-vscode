import json,tempfile,unittest,sys,threading
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import broker as m
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.home=Path(self.temp.name);self.sid='11111111-1111-1111-1111-111111111111';self.path=self.home/'sessions/2026/11/16'/('rollout-test-'+self.sid+'.jsonl');self.path.parent.mkdir(parents=True);self.cache={};self.write({'type':'session_meta','timestamp':'2026-11-16T00:00:01Z','payload':{'id':self.sid}})
 def write(self,row):
  with self.path.open('a') as f:f.write(json.dumps(row)+'\n')
 def event(self,kind,turn='own',second=2,**extra):self.write({'type':'event_msg','timestamp':f'2026-11-16T00:00:{second:02d}Z','payload':{'type':kind,'turn_id':turn,**extra}})
 def check(self,**kwargs):return m.codex_native_terminal({'CODEX_HOME':str(self.home)},self.sid,'own','2026-11-16T00:00:00Z',self.cache,**kwargs)
 def finish(self):self.event('task_started');self.event('task_complete',second=3)
 def test_terminal(self):self.finish();self.assertEqual(self.check()['status'],'completed')
 def test_later_active_then_terminal(self):
  self.finish();self.event('task_started','later',4);self.assertIsNone(self.check());self.event('task_complete','later',5,last_agent_message='Newest native answer');self.assertEqual(self.check()['turn_id'],'later');self.assertEqual(self.check()['text'],'Newest native answer')
 def test_inherited_ancestor_not_expected(self):
  self.event('task_started','ancestor',2);self.event('task_complete','ancestor',3);self.assertIsNone(self.check())
 def test_partial_tail(self):
  self.finish();self.assertIsNotNone(self.check())
  with self.path.open('a') as f:f.write('{"type":"event_msg"')
  self.assertIsNone(self.check())
 def test_bounded_scan(self):
  self.finish();self.assertIsNone(self.check(budget=10));self.assertEqual(self.cache['files'][str(self.path.resolve())]['offset'],10)
  for _ in range(100):result=self.check(budget=10)
  self.assertIsNotNone(result)
 def test_wrong_metadata(self):
  self.path.write_text(json.dumps({'type':'session_meta','timestamp':'2026-11-16T00:00:01Z','payload':{'id':'other'}})+'\n');self.finish();self.assertIsNone(self.check())
 def test_truncation(self):
  self.finish();self.assertIsNotNone(self.check());self.path.write_text('{}\n');self.assertIsNone(self.check())
 def test_earlier_than_session(self):
  self.event('task_started',second=0);self.event('task_complete',second=0);self.assertIsNone(self.check())
 def test_newer_aborted_turn_supersedes_prior_completed_turn(self):
  self.finish();self.event('task_started','later',4);self.event('turn_aborted','later',5)
  proof=self.check();self.assertEqual(proof['turn_id'],'later');self.assertEqual(proof['status'],'interrupted')
 def test_completed_child_reactivation_blocks_root_idle_close(self):
  child='22222222-2222-2222-2222-222222222222'
  self.finish()
  child_path=self.path.with_name('rollout-child-'+child+'.jsonl')
  rows=[{'type':'session_meta','timestamp':'2026-11-16T00:00:01Z','payload':{'id':child}},
        {'type':'event_msg','timestamp':'2026-11-16T00:00:02Z','payload':{'type':'task_started','turn_id':'child-turn'}},
        {'type':'event_msg','timestamp':'2026-11-16T00:00:03Z','payload':{'type':'task_complete','turn_id':'child-turn'}}]
  child_path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
  store=m.RunStore(self.home/'broker');controller=m.Broker(store)
  run=store.create({'provider':'codex','session_id':self.sid,'host_id':'mac'})
  run=store.update(run['id'],created_at='2026-11-16T00:00:00Z')
  ctrl={'protocol':'app-server','turn_id':'own','turn_open':True,'background':{child:'subagent'},'child_turn_ids':{child:'child-turn'},'control_lock':threading.RLock()}
  launch={'protocol':'app-server','env':{'CODEX_HOME':str(self.home)}}
  with patch.object(controller,'_after_turn'),patch.object(controller,'_settle'):
   controller._reconcile_native(run['id'],run,m.stream._State(self.sid),ctrl,launch)
   self.assertEqual(ctrl['background'],{});self.assertIn(child,ctrl['native_child_proofs'])
   with child_path.open('a') as f:f.write(json.dumps({'type':'event_msg','timestamp':'2026-11-16T00:00:04Z','payload':{'type':'task_started','turn_id':'child-again'}})+'\n')
   timer=object();ctrl['idle_timer']=timer
   with patch.object(m.threading,'current_thread',return_value=timer),patch.object(controller,'_signal') as signal:
    controller._close_idle(run['id'],ctrl)
    signal.assert_not_called()
   self.assertIn(child,ctrl['background']);self.assertFalse(ctrl.get('closing',False))
   ctrl['native_check_at']=0
   controller._reconcile_native(run['id'],run,m.stream._State(self.sid),ctrl,launch)
   self.assertIn(child,ctrl['background'])
 def test_native_completion_does_not_close_active_or_unconfirmed_goal(self):
  self.finish();store=m.RunStore(self.home/'broker');controller=m.Broker(store)
  run=store.create({'provider':'codex','session_id':self.sid,'host_id':'mac'});run=store.update(run['id'],created_at='2026-11-16T00:00:00Z')
  for guard in ({'goal':{'status':'active'}},{'goal_get_pending':3},{'goal_set_pending':4}):
   with self.subTest(guard=guard):
    ctrl={'protocol':'app-server','turn_id':'own','turn_open':True,'background':{},'control_lock':threading.RLock(),**guard}
    with patch.object(controller,'_after_turn') as after,patch.object(controller,'_settle'):
     controller._reconcile_native(run['id'],run,m.stream._State(self.sid),ctrl,{'protocol':'app-server','env':{'CODEX_HOME':str(self.home)}})
    self.assertTrue(ctrl['turn_open']);self.assertNotIn('native_terminal',ctrl);after.assert_not_called()
if __name__=='__main__':unittest.main()
