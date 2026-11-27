import json
import sys
from pathlib import Path
import tempfile
from unittest import TestCase, skipUnless
from unittest.mock import patch

import file_service
import hosts
import remote_files


class ClusterChatReferences(TestCase):
    cluster_file = '/cluster/data/project/J-trend-windows/J_windows.pdf'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name).resolve()
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

    def test_old_link_survives_library_folder_rename_and_restart(self):
        root = self.service.register('mac', str(self.data), 'write')
        report = self.data / 'old' / 'report.md'
        report.parent.mkdir()
        report.write_text('original report')
        self.chat(f'[report]({report})')
        self.service.rename(root['id'], 'old', 'renamed')
        fresh_service = file_service.FileService(self.data, default_root=None)
        record = fresh_service.reference('codex-123', str(report))
        self.assertEqual(record['path'], str(self.data / 'renamed' / 'report.md'))
        self.assertEqual(record['evidence_path'], str(report))
        self.assertEqual(fresh_service.read(file_id=record['file_id']), b'original report')

    @skipUnless(sys.platform == 'darwin', 'APFS file identities are Mac-specific')
    def test_opened_link_follows_rename_outside_library(self):
        self.service.register('mac', str(self.data), 'write')
        original = self.data / 'old.html'
        original.write_text('original report')
        self.chat(f'[report]({original})')
        self.service.reference('codex-123', str(original))
        target = self.data / 'new.html'
        original.rename(target)
        self.assertEqual(self.service.reference('codex-123', str(original))['path'], str(target))

    def test_missing_cluster_link_recovers_only_a_unique_match_in_its_workspace(self):
        self.chat(f'[PDF]({self.cluster_file})')
        moved = '/cluster/data/current/J_windows.pdf'
        matches = ['current/J_windows.pdf']

        def request(host, action, payload):
            self.assertEqual(host, self.reader['id'])
            if action == 'root' and payload['root'] == '/cluster/data/project/J-trend-windows':
                raise file_service.FileError('not_found', 'Folder moved', 404)
            if action == 'search':
                self.assertEqual(payload['root'], '/cluster/data')
                return {'entries': [{'path': path} for path in matches], 'truncated': False}
            if action == 'root':
                return {'path': payload['root']}
            if action == 'stat':
                return {'resource': {'path': moved, 'size': 24, 'mime': 'application/pdf'}}
            self.fail(f'Unexpected remote operation {action}')

        with patch.object(hosts, 'host_records', return_value=[self.reader]), \
             patch.object(remote_files, 'request', side_effect=request):
            matches.append('archive/J_windows.pdf')
            with self.assertRaises(file_service.FileError) as error:
                self.service.reference('codex-123', self.cluster_file)
            self.assertEqual(error.exception.code, 'ambiguous_file')
            matches.pop()
            record = self.service.reference('codex-123', self.cluster_file)
            self.assertEqual(record['path'], moved)
            self.assertEqual(record['evidence_path'], self.cluster_file)

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
