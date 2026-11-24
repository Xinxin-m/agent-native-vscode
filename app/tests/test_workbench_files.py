import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest

import file_service as fs


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.data = self.base / 'data'
        self.service = fs.FileService(self.data, default_root=None)
        self.rid = self.service.register('mac', str(self.root), 'write')['id']
        (self.root / '中文 spaces.txt').write_bytes(b'hello\r\nworld\r\n')

    def test_list_stat_read_and_conflict_safe_save(self):
        rows = self.service.list(self.rid)['entries']
        self.assertEqual(rows[0]['name'], '中文 spaces.txt')
        item = self.service.stat(self.rid, '中文 spaces.txt')
        self.assertTrue(item['editable'])
        self.assertEqual(self.service.read(self.rid, '中文 spaces.txt'), b'hello\r\nworld\r\n')
        saved = self.service.save(self.rid, '中文 spaces.txt', item['version'], 'changed\n')
        self.assertNotEqual(saved['version'], item['version'])
        self.assertEqual((self.root / '中文 spaces.txt').read_bytes(), b'changed\r\n')
        with self.assertRaises(fs.FileError) as caught:
            self.service.save(self.rid, '中文 spaces.txt', item['version'], 'overwrite')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(list((self.data / 'file-backups').glob('*'))), 1)

    def test_create_file_and_folder_never_replaces_existing_items(self):
        self.service.create(self.rid, 'notes', 'directory')
        self.service.create(self.rid, 'notes/new.md', 'file')
        self.assertEqual((self.root / 'notes/new.md').read_bytes(), b'')
        (self.root / 'notes/new.md').write_text('keep')
        for kind in ('file', 'directory'):
            with self.assertRaises(fs.FileError) as caught:
                self.service.create(self.rid, 'notes/new.md', kind)
            self.assertEqual(caught.exception.code, 'destination_exists')
        self.assertEqual((self.root / 'notes/new.md').read_text(), 'keep')

    def test_create_respects_workspace_and_readonly_boundaries(self):
        read = self.service.register('mac', str(self.root), 'read')['id']
        (self.root / 'link').symlink_to(self.base)
        for rid, name in ((read, 'no.txt'), (self.rid, '../escape'), (self.rid, '/escape'), (self.rid, 'link/escape'), (self.rid, '')):
            with self.subTest(name=name), self.assertRaises(fs.FileError):
                self.service.create(rid, name, 'file')
        with self.assertRaises(fs.FileError):
            self.service.create(self.rid, 'name', 'other')

    def test_copy_is_exclusive(self):
        version = self.service.stat(self.rid, '中文 spaces.txt')['version']
        self.service.copy(self.rid, '中文 spaces.txt', version, 'copy.txt', 'copy')
        with self.assertRaises(fs.FileError):
            self.service.copy(self.rid, '中文 spaces.txt', version, 'copy.txt', 'lost')
        self.assertEqual((self.root / 'copy.txt').read_text(), 'copy')

    def test_diff_uses_head_without_mutating_worktree(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','--','中文 spaces.txt'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null','commit','-qm','fixture'],check=True)
        (self.root/'中文 spaces.txt').write_text('changed')
        result=self.service.diff(self.rid,'中文 spaces.txt')
        self.assertEqual(result['original'],'hello\r\nworld\r\n')
        self.assertEqual(result['modified'],'changed')
        self.assertEqual(result['label'],'Git HEAD')

    def test_diff_no_repo_is_explicit(self):
        with self.assertRaises(fs.FileError) as caught:
            self.service.diff(self.rid,'中文 spaces.txt')
        self.assertEqual(caught.exception.code,'no_git')

    def test_filename_search_descends_without_following_links(self):
        (self.root/'nested').mkdir()
        (self.root/'nested'/'report.md').write_text('report')
        (self.root/'outside').symlink_to(self.base)
        result=self.service.search(self.rid,'report')
        self.assertEqual([e['path'] for e in result['entries']],['nested/report.md'])

    def test_escape_symlink_and_remote_never_fall_back(self):
        (self.root / 'link').symlink_to(self.base)
        for path in ('../data/x', '/etc/passwd', 'link/data/x', 'bad\x00name'):
            with self.subTest(path=path), self.assertRaises(fs.FileError):
                self.service.read(self.rid, path)
        with self.assertRaises(fs.FileError) as caught:
            self.service.register('sc', str(self.root), 'write')
        self.assertEqual(caught.exception.code, 'read_only')

    def test_exact_reference_does_not_grant_sibling_or_write(self):
        outside = self.base / 'outside.txt'
        outside.write_text('outside')
        (self.data / 'chats').mkdir(parents=True)
        (self.data / 'chats' / 'claude-one.json').write_text(json.dumps({'node':'mac','cwd':str(self.base),'files_changed':[{'path':str(outside)}]}))
        ref = self.service.reference('claude-one', str(outside))
        self.assertEqual(self.service.read(file_id=ref['file_id']), b'outside')
        self.assertFalse(ref['editable'])
        with self.assertRaises(fs.FileError):
            self.service.reference('claude-one', str(self.base / 'sibling.txt'))

    def test_encoded_relative_markdown_reference_and_bom_save(self):
        file=self.base/'outside spaces.md'
        file.write_bytes(b'\xef\xbb\xbfhello\r\n')
        (self.data/'chats').mkdir(parents=True)
        (self.data/'chats'/'claude-link.json').write_text(json.dumps({'node':'mac','cwd':str(self.base),'turns':[{'text':'[report](outside%20spaces.md#heading)'}]}))
        ref=self.service.reference('claude-link',str(file))
        self.assertEqual(self.service.read(file_id=ref['file_id']),file.read_bytes())
        self.assertEqual(ref['encoding'],'utf-8-sig')

    def test_paths_shown_as_links_in_prose_or_code_are_exact_references(self):
        notes = self.base / 'outside.md'
        spaced = self.base / 'draft (May).md'
        sibling = self.base / 'private.md'
        for path in (notes, spaced, sibling):
            path.write_text(path.name)
        (self.data / 'chats').mkdir()
        (self.data / 'chats' / 'thread.json').write_text(json.dumps({
            'cwd': str(self.root),
            'turns': [{'role': 'assistant', 'text':
                       f'Open {notes}:12. See `{spaced}`.'}],
        }))
        for path in (notes, spaced):
            with self.subTest(path=path):
                ref = self.service.reference('thread', str(path))
                self.assertEqual(self.service.read(file_id=ref['file_id']).decode(), path.name)
                self.assertFalse(ref['editable'])
        with self.assertRaises(fs.FileError) as denied:
            self.service.reference('thread', str(sibling))
        self.assertEqual(denied.exception.code, 'outside_root')

    def test_file_link_in_current_reply_before_index_refresh(self):
        path = self.base / 'live.md'
        path.write_text('live')
        ref = self.service.reference('thread', str(path), latest_reply=lambda key: {
            'cwd': str(self.root), 'host_id': 'mac',
            'turns': [{'role': 'assistant', 'text': f'[result]({path})'}],
        } if key == 'thread' else {})
        self.assertEqual(self.service.read(file_id=ref['file_id']), b'live')


    def test_readonly_binary_and_large_are_truthful(self):
        read = self.service.register('mac', str(self.root), 'read')['id']
        (self.root / 'bytes.bin').write_bytes(b'a\0b')
        self.assertFalse(self.service.stat(read, 'bytes.bin')['editable'])
        with self.assertRaises(fs.FileError):
            self.service.save(read, '中文 spaces.txt', 'anything', 'lost')
        (self.root / 'large.txt').write_bytes(b'x' * (fs.MAX_TEXT + 1))
        self.assertFalse(self.service.stat(self.rid, 'large.txt')['editable'])


if __name__ == '__main__':
    unittest.main()
