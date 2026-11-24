import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import file_service as fs


class MoveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(os.path.realpath(temp.name))
        self.a, self.b = base / 'wsA', base / 'wsB'
        (self.a / 'docs' / 'inner').mkdir(parents=True)
        self.b.mkdir()
        (self.a / 'note.md').write_text('a note')
        (self.a / 'docs' / 'inner' / 'deep.txt').write_text('deep')
        (self.b / 'note.md').write_text('b note')
        self.service = fs.FileService(base / 'data', default_root=None)
        self.ra = self.service.register('mac', str(self.a), 'write')['id']
        self.rb = self.service.register('mac', str(self.b), 'write')['id']

    def move(self, paths, dest_dir='', root=None, **kw):
        return self.service.move([{'root_id': self.ra, 'path': p} for p in paths], root or self.rb, dest_dir, **kw)

    def test_move_folder_across_workspaces_and_undo(self):
        result = self.move(['docs'])
        self.assertTrue((self.b / 'docs' / 'inner' / 'deep.txt').exists())
        self.assertFalse((self.a / 'docs').exists())
        self.assertEqual(result['moved'][0]['dest_path'], 'docs')
        self.service.undo_move(result['token'])
        self.assertTrue((self.a / 'docs' / 'inner' / 'deep.txt').exists())
        self.assertFalse((self.b / 'docs').exists())
        with self.assertRaises(fs.FileError):
            self.service.undo_move(result['token'])

    def test_collision_asks_then_keep_both_or_replace(self):
        with self.assertRaises(fs.FileError) as caught:
            self.move(['note.md'])
        self.assertEqual((caught.exception.code, caught.exception.details['names']), ('destination_exists', ['note.md']))
        self.assertEqual((self.a / 'note.md').read_text(), 'a note')
        kept = self.move(['note.md'], conflict='keep_both')
        self.assertEqual(kept['moved'][0]['dest_path'], 'note copy.md')
        self.assertEqual((self.b / 'note.md').read_text(), 'b note')
        self.service.undo_move(kept['token'])
        replaced = self.move(['note.md'], conflict='replace')
        self.assertEqual((self.b / 'note.md').read_text(), 'a note')
        self.service.undo_move(replaced['token'])
        self.assertEqual((self.b / 'note.md').read_text(), 'b note')
        self.assertEqual((self.a / 'note.md').read_text(), 'a note')

    def test_folder_into_itself_and_traversal_are_refused(self):
        for dest in ('docs', 'docs/inner'):
            with self.assertRaises(fs.FileError) as caught:
                self.move(['docs'], dest, root=self.ra)
            self.assertEqual(caught.exception.code, 'into_itself')
        for bad in ('../wsB/note.md', '/etc/hosts', 'docs/../../x'):
            with self.assertRaises(fs.FileError) as caught:
                self.move([bad])
            self.assertEqual(caught.exception.code, 'outside_root')
        with self.assertRaises(fs.FileError):
            self.move(['note.md'], '../')
        with self.assertRaises(fs.FileError):
            self.service.move([{'root_id': 'unknown', 'path': 'note.md'}], self.rb)
        os.symlink(self.b, self.a / 'link')
        with self.assertRaises(fs.FileError):
            self.move(['note.md'], 'link', root=self.ra)
        self.assertTrue((self.a / 'docs' / 'inner' / 'deep.txt').exists())

    def test_copy_keeps_source_and_same_folder_copy_gets_new_name(self):
        result = self.move(['note.md'], root=self.ra, copy=True)
        self.assertEqual(result['moved'][0]['dest_path'], 'note copy.md')
        self.assertEqual(self.move(['note.md'], root=self.ra, copy=True)['moved'][0]['dest_path'], 'note copy 2.md')
        self.assertEqual(fs.free_name(str(self.a), 'docs', True), 'docs copy')
        self.assertEqual((self.a / 'note.md').read_text(), 'a note')
        self.service.undo_move(result['token'])
        self.assertFalse((self.a / 'note copy.md').exists())

    def test_cross_device_copies_verifies_then_deletes(self):
        real = os.rename
        calls = []
        def rename(src, dst, *a, **k):
            calls.append(os.path.basename(dst))
            if not os.path.basename(dst).startswith('.workbench-move-') and len(calls) == 1:
                raise OSError(errno.EXDEV, 'cross-device')
            return real(src, dst, *a, **k)
        with mock.patch('os.rename', rename):
            self.move(['docs'])
        self.assertTrue((self.b / 'docs' / 'inner' / 'deep.txt').exists())
        self.assertFalse((self.a / 'docs').exists())
        self.assertEqual([p.name for p in self.b.iterdir() if p.name.startswith('.workbench')], [])

    def test_failed_verification_leaves_source_intact(self):
        with mock.patch('os.rename', side_effect=OSError(errno.EXDEV, 'x')), mock.patch.object(fs, 'tree_summary', side_effect=[['a'], ['b']]):
            with self.assertRaises(fs.FileError) as caught:
                self.move(['docs'])
        self.assertEqual(caught.exception.code, 'verify_failed')
        self.assertTrue((self.a / 'docs' / 'inner' / 'deep.txt').exists())
        self.assertEqual(sorted(p.name for p in self.b.iterdir()), ['note.md'])

    def test_remote_and_read_only_roots(self):
        self.service.roots['r'] = {'id': 'r', 'root_id': 'r', 'host_id': 'sc', 'path': '/remote', 'access': 'read', 'label': 'remote'}
        with self.assertRaises(fs.FileError) as caught:
            self.service.move([{'root_id': 'r', 'path': 'x'}], self.rb)
        self.assertEqual((caught.exception.code, caught.exception.status), ('move_confirmation_required', 409))
        with self.assertRaises(fs.FileError) as caught:
            self.move(['note.md'], root='r')
        self.assertEqual(caught.exception.code, 'move_confirmation_required')
        ro = self.service.register('mac', str(self.b), 'read')['id']
        with self.assertRaises(fs.FileError) as caught:
            self.service.move([{'root_id': self.ra, 'path': 'note.md'}], ro)
        self.assertEqual(caught.exception.code, 'read_only')
        self.assertTrue((self.a / 'note.md').exists())

    def test_partial_failure_rolls_back_earlier_items(self):
        (self.a / 'z.txt').write_text('z')
        real = fs.transfer
        def flaky(src, dst, copy=False):
            if src.endswith('z.txt'):
                raise OSError('disk full')
            return real(src, dst, copy)
        with mock.patch.object(fs, 'transfer', flaky):
            with self.assertRaises(OSError):
                self.move(['docs', 'z.txt'])
        self.assertTrue((self.a / 'docs' / 'inner' / 'deep.txt').exists())
        self.assertFalse((self.b / 'docs').exists())


if __name__ == '__main__':
    unittest.main()
