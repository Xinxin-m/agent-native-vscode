"""Request paths that must not parse big files or touch the filesystem per item."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import automation
import json_cache
import session_lineage


def write(path, value):
    tmp = str(path) + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(value, fh)
    os.replace(tmp, path)


def wait_for(predicate, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(.01)
    return False


class JsonCacheTests(unittest.TestCase):
    def test_changed_file_is_served_stale_once_then_refreshed_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'index.json')
            write(path, {'a': 1})
            first = json_cache.latest(path, {})
            self.assertIs(json_cache.latest(path, {}), first)
            write(path, {'a': 1, 'b': 2})
            os.utime(path, ns=(1, 1))  # a different stamp even on coarse clocks
            self.assertIs(json_cache.latest(path, {}), first, 'a click never waits for the re-parse')
            self.assertTrue(wait_for(lambda: json_cache.latest(path, {}) == {'a': 1, 'b': 2}))

    def test_missing_file_returns_default(self):
        self.assertEqual(json_cache.latest('/nonexistent/index.json', {'x': 1}), {'x': 1})


class SessionTreeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        index = {'root': {'id': 'root', 'source': 'codex', 'cwd': '/w'}}
        for n in range(40):
            index[f'child{n}'] = {'id': f'child{n}', 'source': 'codex', 'cwd': '/w', 'parent_key': 'root'}
        write(self.data / 'index.json', index)
        write(self.data / 'projects.json', {'assignments': {'root': 'p'}})
        patcher = patch.object(session_lineage, 'DATA', str(self.data))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tree_does_not_wait_for_resume_checks_and_fills_them_later(self):
        gate = threading.Event()

        def slow(meta):
            gate.wait(5)
            return {'native_resumable': False, 'resume_reason': 'checked'}
        with patch.object(session_lineage, 'resume_availability', side_effect=slow):
            start = time.perf_counter()
            tree = session_lineage.session_tree(None, {'key': 'child3'}, None)
            self.assertLess(time.perf_counter() - start, 1)
            self.assertEqual(len(tree['nodes']), 41)
            self.assertEqual({n['project_id'] for n in tree['nodes']}, {'p'})
            self.assertFalse(any('native_resumable' in n for n in tree['nodes']))
            gate.set()
            self.assertTrue(wait_for(lambda: all(n.get('native_resumable') is False for n in
                                                 session_lineage.session_tree(None, {'key': 'root'}, None)['nodes'])))

    def test_unchanged_files_are_not_parsed_again(self):
        session_lineage.session_tree(None, {'key': 'root'}, None)
        with patch.object(session_lineage.json, 'load', side_effect=AssertionError('parsed again')):
            self.assertEqual(len(session_lineage.session_tree(None, {'key': 'child0'}, None)['nodes']), 41)

    def test_new_key_rebuilds_at_once(self):
        session_lineage.session_tree(None, {'key': 'root'}, None)
        index = json.loads((self.data / 'index.json').read_text())
        index['fresh'] = {'id': 'fresh', 'source': 'claude', 'cwd': '/w'}
        write(self.data / 'index.json', index)
        self.assertEqual(session_lineage.session_tree(None, {'key': 'fresh'}, None)['root_key'], 'fresh')


class AutomationPrefixTests(unittest.TestCase):
    def test_config_is_checked_at_most_once_a_second_across_many_chats(self):
        real, calls = os.stat, []

        def counted(path, *args, **kwargs):
            calls.append(path)
            return real(path, *args, **kwargs)
        automation.prefixes()
        with patch.object(automation.os, 'stat', side_effect=counted):
            for _ in range(5000):
                automation.automation_kind({'first_prompt': 'hello'})
        self.assertLessEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
