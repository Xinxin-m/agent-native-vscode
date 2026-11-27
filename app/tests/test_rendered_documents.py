from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from file_service import FileError, FileService
import remote_files


class RenderedDocuments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'workspace'
        self.root.mkdir()
        self.file = self.root / 'draft.md'
        self.file.write_text('Initial draft\n')
        self.service = FileService(base / 'state', str(self.root))
        self.rid = next(iter(self.service.roots))

    def test_each_save_rotates_checkpoint_and_preserves_idempotent_comments(self):
        def save(text, at):
            current = self.service.stat(self.rid, self.file.name)
            with patch('file_service.time.time', return_value=at):
                self.service.save_document(self.rid, self.file.name, None, current['version'], text)

        save('First edit\n', 7200)
        save('Second edit\n', 7300)
        self.assertEqual(self.service.document_review(self.rid, self.file.name, None)['previous'], 'First edit\n')
        save('Third edit\n', 10900)
        self.assertEqual(self.file.read_text(), 'Third edit\n')
        note = {'id':'946ead78-89f9-4949-9f2b-35ca6a342bab','text':'Tighten this','quote':'Third edit'}
        self.service.document_review(self.rid, self.file.name, None, note)
        review = self.service.document_review(self.rid, self.file.name, None, note)
        self.assertEqual(review['previous'], 'Second edit\n')
        self.assertEqual([c['quote'] for c in review['comments']], ['Third edit'])
        self.assertEqual(len(list((self.service.data / 'file-backups').rglob('*.json'))), 1)

    def test_stale_local_save_preserves_external_edit(self):
        version = self.service.stat(self.rid, self.file.name)['version']
        self.file.write_text('Change from another editor\n')
        with self.assertRaises(FileError) as failure:
            self.service.save_document(self.rid, self.file.name, None, version, 'stale edit')
        self.assertEqual(failure.exception.code, 'version_conflict')
        self.assertEqual(self.file.read_text(), 'Change from another editor\n')

    def test_observed_external_edits_and_restart_preserve_two_byte_exact_versions(self):
        self.assertFalse(self.service.stat(self.rid, self.file.name)['has_previous'])
        before = b'\xef\xbb\xbfsecond\r\nmixed\n'
        after = b'third\n'
        self.file.write_bytes(before)
        self.service.observe_local_changes()
        self.file.write_bytes(after)
        self.service.observe_local_changes()
        self.service = FileService(self.service.data, str(self.root))
        current = self.service.stat(self.rid, self.file.name)
        self.assertTrue(current['has_previous'])
        rewound = self.service.rewind_document(self.rid, self.file.name, None, current['version'])
        self.assertEqual(self.file.read_bytes(), before)
        self.service.rewind_document(self.rid, self.file.name, None, rewound['version'])
        self.assertEqual(self.file.read_bytes(), after)

    def test_broker_tool_events_observe_a_document_before_it_is_opened(self):
        import broker
        from unittest.mock import Mock
        run = {'host_id':'mac', 'cwd':str(self.root)}
        ctrl, store = {}, Mock()
        with patch('file_service.instance', return_value=self.service):
            runner = broker.Broker.__new__(broker.Broker)
            runner.store = store
            runner._claude_tools('run', run, ctrl, {'type':'assistant', 'message':{'content':[
                {'type':'tool_use', 'name':'Edit', 'id':'edit-1', 'input':{'file_path':self.file.name}}
            ]}})
            self.file.write_text('Session edit')
            runner._claude_tools('run', run, ctrl, {'type':'user', 'message':{'content':[
                {'type':'tool_result', 'tool_use_id':'edit-1'}
            ]}})
        self.assertFalse(ctrl['document_edit_paths'])
        self.assertEqual(self.service.document_review(self.rid, self.file.name, None)['previous'], 'Initial draft\n')

    def test_rewind_requires_write_access_and_current_disk_version(self):
        initial = self.service.stat(self.rid, self.file.name)
        with self.assertRaises(FileError) as missing:
            self.service.rewind_document(self.rid, self.file.name, None, initial['version'])
        self.assertEqual(missing.exception.code, 'no_previous')
        self.file.write_text('Unseen external edit')
        with self.assertRaises(FileError) as stale:
            self.service.rewind_document(self.rid, self.file.name, None, initial['version'])
        self.assertEqual(stale.exception.code, 'version_conflict')
        read = self.service.register('mac', str(self.root), 'read')['id']
        current = self.service.stat(read, self.file.name)
        with self.assertRaises(FileError) as readonly:
            self.service.rewind_document(read, self.file.name, None, current['version'])
        self.assertEqual(readonly.exception.status, 403)
        self.assertEqual(self.file.read_text(), 'Unseen external edit')

    def test_remote_helper_updates_only_the_existing_document(self):
        helper = {}
        exec(remote_files.HELPER, helper)
        old = self.file
        remote = self.root / 'draft.html'
        remote.write_bytes(b'\xef\xbb\xbf<p>Before</p>\r\n')
        def request(operation, path=remote.name, **fields):
            return helper['perform']({'operation':operation,'root':str(self.root.resolve()),'path':path,**fields})
        state = request('stat')['resource']
        self.assertTrue(state['document_editable'])
        self.assertTrue(request('save_document', base_version=state['version'], text='<p>After</p>\n')['ok'])
        self.assertEqual(remote.read_bytes(), b'\xef\xbb\xbf<p>After</p>\r\n')
        self.assertEqual(request('save_document', base_version=state['version'], text='stale')['code'], 'version_conflict')
        (self.root / 'other.txt').write_text('Other')
        another = request('stat', 'other.txt')['resource']
        self.assertEqual(request('save_document', 'other.txt', base_version=another['version'], text='no')['code'], 'read_only')
        self.assertEqual(old.read_text(), 'Initial draft\n')


    def test_cluster_save_is_routed_to_original_host_and_path(self):
        helper = {}
        exec(remote_files.HELPER, helper)

        def remote(host, operation, values):
            self.assertEqual(host, 'sc-fixture')
            result = helper['perform']({'operation':operation,**values})
            if not result.get('ok'):
                raise FileError(result['code'], result['error'], result['status'])
            return result

        with patch('remote_files.request', side_effect=remote) as connector:
            root = self.service.register('sc-fixture', str(self.root), 'read')
            rid = root['id']
            original = self.service.stat(rid, self.file.name)
            saved = self.service.save_document(rid, self.file.name, None, original['version'], 'Cluster update\n')
            self.assertEqual(saved['host_id'], 'sc-fixture')
            self.assertEqual(self.file.read_text(), 'Cluster update\n')
            self.assertEqual(self.service.document_review(rid, self.file.name, None)['previous'], 'Initial draft\n')
            writes = [call for call in connector.call_args_list if call.args[1] == 'save_document']
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0].args[2]['root'], str(self.root.resolve()))
            self.assertEqual(writes[0].args[2]['path'], self.file.name)
            changed = b'\xef\xbb\xbfChanged outside the app\r\n'
            self.file.write_bytes(changed)
            updated = self.service.stat(rid, self.file.name)
            restored = self.service.rewind_document(rid, self.file.name, None, updated['version'])
            self.assertEqual(self.file.read_bytes(), b'Cluster update\n')
            self.service.rewind_document(rid, self.file.name, None, restored['version'])
            self.assertEqual(self.file.read_bytes(), changed)
