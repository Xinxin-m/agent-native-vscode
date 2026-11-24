import io
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server


class RequestSecurityTests(unittest.TestCase):
    def handler(self, path='/api/send', body=b'{}', **headers):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler.server = SimpleNamespace(server_port=8744)
        handler.headers = {'Host': '127.0.0.1:8744', 'Content-Type': 'application/json',
                           'Content-Length': str(len(body)), **headers}
        handler.rfile = io.BytesIO(body)
        handler._json = Mock(side_effect=lambda value, code=200: (value, code))
        handler._plugin = Mock(return_value=False)
        return handler

    def test_rejects_foreign_hosts_and_browser_origins_before_dispatch(self):
        cases = [{'Host': 'evil.test:8744'}, {'Host': '127.0.0.1:9999'},
                 {'Origin': 'https://evil.test'}, {'Origin': 'null'},
                 {'Origin': 'http://127.0.0.1:9999'},
                 {'Origin': 'http://localhost:8744'},
                 {'Sec-Fetch-Site': 'cross-site'}]
        for headers in cases:
            with self.subTest(headers=headers):
                handler = self.handler(**headers)
                handler.do_POST()
                self.assertEqual(handler._json.call_args.args[1], 403)
                handler._plugin.assert_not_called()

    def test_same_origin_and_native_clients_reach_retired_route(self):
        for headers in ({}, {'Origin': 'http://127.0.0.1:8744'},
                        {'Host': 'localhost:8744', 'Origin': 'http://localhost:8744'}):
            self.assertEqual(self.handler(**headers).do_POST()[1], 410)

    def test_rejects_bad_content_length_type_and_nonobject_json(self):
        for headers, body, code in [({'Content-Length': '-1'}, b'{}', 400),
                                    ({'Content-Length': 'bad'}, b'{}', 400),
                                    ({'Content-Length': str(31 * 1024 * 1024)}, b'{}', 413),
                                    ({'Content-Type': 'text/plain'}, b'{}', 415),
                                    ({}, b'[]', 400), ({}, b'null', 400)]:
            with self.subTest(headers=headers, body=body):
                self.assertEqual(self.handler(body=body, **headers).do_POST()[1], code)

    def test_get_checks_host_and_origin(self):
        handler = self.handler(path='/api/revision', Host='evil.test:8744')
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[1], 403)

    @patch('server.jsave')
    @patch('server.jload', return_value={})
    def test_config_rejects_invalid_exclusions_without_saving(self, load, save):
        for value in ([None], [''], 'abc', [123], ['  ']):
            handler = self.handler(path='/api/config', body=json.dumps({'excluded_cwds': value}).encode())
            self.assertEqual(handler.do_POST()[1], 400)
        save.assert_not_called()

    @patch('server.jsave')
    @patch('server.jload', return_value={'auto_compact': {'enabled': True, 'threshold': 0.5, 'idle_minutes': 0}})
    def test_config_saves_agent_questions_and_auto_compact_and_rejects_bad_values(self, load, save):
        body = {'agent_questions': 'ask', 'auto_compact': {'threshold': 0.6, 'idle_minutes': 30}}
        self.assertEqual(self.handler(path='/api/config', body=json.dumps(body).encode()).do_POST()[1], 200)
        self.assertEqual(save.call_args.args[1]['agent_questions'], 'ask')
        self.assertEqual(save.call_args.args[1]['auto_compact'], {'enabled': True, 'threshold': 0.6, 'idle_minutes': 30})
        save.reset_mock()
        for compact in ({'threshold': 0.9}, {'idle_minutes': 5}, {'enabled': 'yes'}):
            handler = self.handler(path='/api/config', body=json.dumps({'auto_compact': compact}).encode())
            self.assertEqual(handler.do_POST()[1], 400)
        save.assert_not_called()

    @patch('server.jsave')
    @patch('server.jload', return_value={})
    def test_core_missing_required_fields_return_json_error(self, load, save):
        for path in ('/api/view', '/api/delete_chat', '/api/projects'):
            self.assertEqual(self.handler(path=path).do_POST()[1], 400)
        save.assert_not_called()

    def test_config_exclusions_ignore_legacy_malformed_values(self):
        self.assertEqual(server.config_exclusions({'excluded_cwds': [None, '', '/valid', 5]}), ['/valid'])
        self.assertEqual(server.config_exclusions({'excluded_cwds': 'bad'}), [])

    def test_real_http_checks_use_the_bound_port_without_mutation(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_port
            for headers, code in [({'Origin': 'https://evil.test'}, 403),
                                  ({'Host': 'evil.test'}, 403),
                                  ({'Origin': f'http://127.0.0.1:{port}'}, 410),
                                  ({}, 410)]:
                client = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    client.request('POST', '/api/send', body='{}', headers={
                        'Content-Type': 'application/json', **headers})
                    response = client.getresponse()
                    self.assertEqual(response.status, code)
                    self.assertFalse(json.loads(response.read())['ok'])
                finally:
                    client.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


class StaticBoundaryTests(unittest.TestCase):
    def test_prefix_sibling_is_not_inside_web_root(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            web = Path(directory) / 'web'
            web.mkdir()
            sibling = Path(directory) / 'web-private'
            sibling.mkdir()
            (sibling / 'private.txt').write_text('private fixture')
            handler = server.Handler.__new__(server.Handler)
            handler.send_error = Mock()
            with patch.object(server, 'WEB', str(web)):
                handler._static('../web-private/private.txt')
            handler.send_error.assert_called_once_with(404)
