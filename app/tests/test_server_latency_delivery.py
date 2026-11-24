"""Latency delivery contracts, using temporary files and in-process HTTP handlers."""
import gzip
import io
import json
import os
from pathlib import Path
import tempfile
import types
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch
import server

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.chats=self.root/'chats';self.chats.mkdir()
        self.flags=self.root/'flags.json';self.flags.write_text('{}')
        self.path=self.chats/'claude-s.json';self.path.write_text(json.dumps({'key':'claude-s','title':'original','turns':[{'text':'hello'}]}))
        self.patches=[patch.object(server,'CHATS',str(self.chats)),patch.dict(server.FILES,{'flags':str(self.flags)}),patch.object(server,'_STATE',{}),patch.object(server,'_STATE_ERROR',{})]
        for item in self.patches:item.start()
        if hasattr(server,'_CHAT_CACHE'):server._CHAT_CACHE.clear()
        if hasattr(server,'_STATE_HISTORY'):server._STATE_HISTORY.clear()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()
    def request(self,path,headers=None):
        handler=server.Handler.__new__(server.Handler);handler.path=path;handler.headers=headers or {};handler.wfile=io.BytesIO();handler._trusted_request=lambda:True
        result={'headers':{}}
        handler.send_response=lambda code:result.update(status=code)
        handler.send_header=lambda key,value:result['headers'].__setitem__(key,value)
        handler.end_headers=lambda:None
        handler.do_GET();result['body']=handler.wfile.getvalue();return result
    def test_chat_etag_gzip_and_conditional_304(self):
        response=self.request('/api/chat?key=claude-s',{'Accept-Encoding':'gzip'})
        self.assertEqual(response['headers'].get('Content-Encoding'),'gzip')
        self.assertEqual(json.loads(gzip.decompress(response['body']))['turns'][0]['text'],'hello')
        etag=response['headers']['ETag']
        response=self.request('/api/chat?key=claude-s',{'If-None-Match':'W/'+etag})
        self.assertEqual(response['status'],304);self.assertEqual(response['body'],b'')
    def test_chat_cache_avoids_reparse_and_global_lock(self):
        self.request('/api/chat?key=claude-s')
        lock=types.SimpleNamespace(__enter__=lambda _:self.fail('global lock acquired'))
        with patch.object(server,'LOCK',lock),patch.object(server.json,'load',side_effect=AssertionError('chat reparsed')):
            self.assertEqual(self.request('/api/chat?key=claude-s')['status'],200)
    def test_chat_atomic_file_replace_and_title_change_invalidate(self):
        first=self.request('/api/chat?key=claude-s')
        replace=self.root/'next';replace.write_text(json.dumps({'key':'claude-s','title':'changed','turns':[]}));replace.replace(self.path)
        second=self.request('/api/chat?key=claude-s');self.assertNotEqual(first['headers'].get('ETag'),second['headers'].get('ETag'))
        with patch.object(server.json_cache,'latest',return_value={'titles':{'claude-s':'renamed'}}):
            third=self.request('/api/chat?key=claude-s')
        self.assertEqual(json.loads(third['body'])['title'],'renamed');self.assertNotEqual(second['headers']['ETag'],third['headers']['ETag'])
        self.path.unlink();self.assertEqual(self.request('/api/chat?key=claude-s')['status'],404)
    def test_gzip_zero_quality_is_not_compressed(self):
        response=self.request('/api/chat?key=claude-s',{'Accept-Encoding':'gzip;q=0'})
        self.assertNotIn('Content-Encoding',response['headers']);json.loads(response['body'])
    def test_delta_rows_removals_metadata_and_unknown_full_fallback(self):
        first={'chats':[{'key':'a','title':'A'},{'key':'b','title':'B'}],'views':{'a':1},'projects':{'projects':[]}}
        second={'chats':[{'key':'a','title':'new A'},{'key':'c','title':'C'}],'views':{'a':2},'projects':{'projects':[]}}
        state={'sig':1,'payload':first}
        with patch.object(server,'_state_signature',side_effect=lambda:state['sig']),patch.object(server,'build_state',side_effect=lambda:state['payload']):
            before=self.request('/api/state');revision=json.loads(before['body'])['revision']
            state.update(sig=2,payload=second)
            response=self.request('/api/state?since='+revision)
            delta=json.loads(response['body']);self.assertTrue(delta['delta']);self.assertEqual(delta['since'],revision)
            self.assertEqual(delta['changed'],second['chats']);self.assertEqual(delta['removed'],['b']);self.assertEqual(delta['state']['views'],{'a':2})
            full=json.loads(self.request('/api/state?since=unknown')['body']);self.assertNotIn('delta',full);self.assertEqual(full['chats'],second['chats'])
            unchanged=json.loads(self.request('/api/state?since='+delta['revision'])['body']);self.assertEqual(unchanged['changed'],[]);self.assertEqual(unchanged['removed'],[])
            self.assertNotEqual(response['headers']['ETag'],'"'+delta['revision']+'"')
    def test_delta_history_is_bounded_and_expired_token_returns_full(self):
        state={'sig':0}
        with patch.object(server,'_state_signature',side_effect=lambda:state['sig']),patch.object(server,'build_state',side_effect=lambda:{'chats':[{'key':'a','version':state['sig']}] }):
            first=json.loads(self.request('/api/state')['body'])['revision']
            for version in range(1,8):state['sig']=version;self.request('/api/state')
            self.assertLessEqual(len(server._STATE_HISTORY),5)
            self.assertNotIn('delta',json.loads(self.request('/api/state?since='+first)['body']))
    def test_real_http_chat_conditional_delivery_closes_server(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=lambda:http.serve_forever(poll_interval=0.01),daemon=True);thread.start()
        try:
            url='http://127.0.0.1:'+str(http.server_port)+'/api/chat?key=claude-s'
            with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept-Encoding':'gzip'}),timeout=3) as response:
                etag=response.headers['ETag'];self.assertEqual(response.headers['Content-Encoding'],'gzip');self.assertEqual(json.loads(gzip.decompress(response.read()))['key'],'claude-s')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(url,headers={'If-None-Match':etag}),timeout=3)
            self.assertEqual(caught.exception.code,304);caught.exception.close()
        finally:
            http.shutdown();http.server_close();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
    def test_revision_reads_existing_store_without_initializing_broker(self):
        fake=types.SimpleNamespace(_BROKER=None,broker=lambda:self.fail('initialized broker'))
        with patch.dict(server.sys.modules,{'broker':fake}),patch.object(server,'data_revision',return_value='library'):
            self.assertEqual(server.revision_payload()['runs'],'')
            fake._BROKER=types.SimpleNamespace(store=types.SimpleNamespace(revision='runs-3'))
            self.assertEqual(server.revision_payload()['runs'],'runs-3')
    def test_switch_interval_is_set_before_plugin_loading(self):
        calls=[]
        with patch.object(server,'jload',return_value={}),patch.object(server.sys,'setswitchinterval',side_effect=lambda n:calls.append(('switch',n))),patch.object(server,'load_plugins',side_effect=lambda:calls.append(('plugins',None))),patch.object(server,'run_background'),patch.object(server,'ThreadingHTTPServer') as http,patch('builtins.print'):
            server.main()
        self.assertEqual(calls[:2],[('switch',0.0005),('plugins',None)])

if __name__=='__main__':unittest.main()
