import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

import file_service
import hosts
import remote_files


class ClusterChatReferences(TestCase):
    cluster_file = '/cluster/data/project/J-trend-windows/J_windows.pdf'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        (self.data / 'chats').mkdir()
        self.service = file_service.FileService(self.data, default_root=None)
        self.reader = {
            'id': 'example-sc', 'transport': 'ssh', 'kind': 'slurm',
            'configured': True, 'read_alias': 'example-dtn', 'workspaces': ['/cluster/data'],
        }

    def chat(self, text, host='mac'):
        (self.data / 'chats' / 'codex-123.json').write_text(json.dumps({
            'execution_host': host, 'cwd': '/Users/example/research',
            'turns': [{'text': text}],
        }))

    def read(self, host, action, payload):
        self.assertEqual(host, self.reader['id'])
        self.assertEqual(payload['root'], str(Path(self.cluster_file).parent))
        if action == 'root':
            return {'path': payload['root']}
        if action == 'stat':
            self.assertEqual(payload['path'], Path(self.cluster_file).name)
            return {'resource': {'path': self.cluster_file, 'size': 24, 'mime': 'application/pdf'}}
        self.fail(f'Unexpected remote operation {action}')

    def test_mac_codex_chat_opens_explicit_cluster_pdf_on_configured_reader(self):
        for text in [
            f'[J_windows.pdf]({self.cluster_file})',
            f'[J_windows.pdf](<{self.cluster_file}#page=2>)',
            fr'[J_windows.pdf]\({self.cluster_file}\)',
        ]:
            with self.subTest(text=text):
                self.chat(text)
                with patch.object(hosts, 'host_records', return_value=[self.reader]), \
                     patch.object(remote_files, 'request', side_effect=self.read) as remote:
                    record = self.service.reference('codex-123', self.cluster_file)
                    self.assertEqual(record['host_id'], self.reader['id'])
                    self.assertEqual(record['mime'], 'application/pdf')
                    self.assertFalse(record['editable'])
                    self.assertEqual(remote.call_count, 2)
                    with self.assertRaises(file_service.FileError) as error:
                        self.service.reference('codex-123', self.cluster_file.replace('J_windows', 'secret'))
                    self.assertEqual(error.exception.status, 403)
                    self.assertEqual(remote.call_count, 2)

    def test_local_chat_reference_stays_on_mac(self):
        report = self.data / 'local.md'
        report.write_text('local bytes')
        self.chat(f'[local]({report})')
        with patch.object(remote_files, 'request') as remote:
            record = self.service.reference('codex-123', str(report))
            self.assertEqual(record['host_id'], 'mac')
            self.assertEqual(self.service.read(file_id=record['file_id']), b'local bytes')
            remote.assert_not_called()

    def test_unavailable_cluster_does_not_fall_back_to_mac(self):
        self.chat(f'[PDF]({self.cluster_file})')
        with patch.object(hosts, 'host_records', return_value=[]):
            with self.assertRaises(file_service.FileError):
                self.service.reference('codex-123', self.cluster_file)

    def test_mac_file_wins_over_remote_workspace(self):
        report = self.data / 'local.md'
        report.write_text('local bytes')
        self.chat(f'[local]({report})')
        reader = {**self.reader, 'workspaces': [str(self.data)]}
        with patch.object(hosts, 'host_records', return_value=[reader]), patch.object(remote_files, 'request') as remote:
            self.assertEqual(self.service.reference('codex-123', str(report))['host_id'], 'mac')
            remote.assert_not_called()

    def test_two_configured_clusters_require_the_chat_to_identify_the_host(self):
        readers = [self.reader, {**self.reader, 'id': 'other-sc'}]
        text = f'[PDF]({self.cluster_file})'
        with patch.object(hosts, 'host_records', return_value=readers), \
             patch.object(remote_files, 'request', side_effect=self.read) as remote:
            self.chat(text)
            with self.assertRaises(file_service.FileError):
                self.service.reference('codex-123', self.cluster_file)
            remote.assert_not_called()
            self.chat(text, host=self.reader['id'])
            self.assertEqual(self.service.reference('codex-123', self.cluster_file)['host_id'], self.reader['id'])
