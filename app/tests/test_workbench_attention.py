import tempfile
import threading
import time
from unittest.mock import patch
import unittest

import broker


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.obj=broker.Broker(broker.RunStore(self.tmp.name))
        self.run=self.obj.store.create({'provider':'codex','host_id':'mac','key':'codex-chat','session_id':'native'})
        self.obj.store.update(self.run['id'],status='running')
        self.ctrl={'protocol':'app-server','pending_requests':{'7':{'kind':'approval','native_id':7,'params':{'command':'echo hello'}}},'pending_rpc':{},'rpc_seq':100}
        self.obj.active[self.run['id']]=self.ctrl
        self.obj.store.emit(self.run['id'],'approval',{'request_id':'7','command':'echo hello'})

    def test_attention_is_actual_active_unresolved_requests(self):
        value=self.obj.attention()
        self.assertEqual([(r['run_id'],r['request_id']) for r in value['items']],[(self.run['id'],'7')])
        self.obj.store.update(self.run['id'],status='completed')
        self.assertEqual(self.obj.attention()['items'],[])

    def test_duplicate_answers_only_send_one_native_response(self):
        sent=[]
        errors=[]
        barrier=threading.Barrier(2)
        def send(ctrl,payload):
            time.sleep(.02)
            sent.append(payload)
        def answer():
            barrier.wait()
            try:
                self.obj.control(self.run['id'],{'action':'answer','request_id':'7','decision':'accept'})
            except ValueError as exc:
                errors.append(exc)
        with patch.object(self.obj,'_send',side_effect=send):
            threads=[threading.Thread(target=answer) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(2)
        self.assertEqual(len(sent),1)
        self.assertEqual(len(errors),1)
        self.assertEqual(self.obj.attention()['items'],[])
        self.assertEqual(sum(e['event']=='request_resolved' for e in self.obj.store.events(self.run['id'])),1)

    def test_stale_answer_route_returns_conflict(self):
        self.ctrl['pending_requests'].clear()
        with patch.object(broker,'instance',return_value=self.obj):
            value,status=broker.control_run(None,{}, {'id':self.run['id'],'action':'answer','request_id':'7','decision':'accept'})
        self.assertEqual(status,409)
        self.assertEqual(value['code'],'request_resolved')

    def test_attention_route_reports_whether_the_watcher_plays_the_sound(self):
        route=broker.ROUTES[('GET','/api/attention')]
        for running in (True,False):
            with patch.object(broker,'instance',return_value=self.obj),patch.object(broker.attention_watch,'active',return_value=running):
                value=route(None,{},{})
            self.assertIs(value['watcher_sound'],running)
            self.assertEqual(len(value['items']),1)
