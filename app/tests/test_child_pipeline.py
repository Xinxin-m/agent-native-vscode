import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import automation
import classify
import enrich

DIGEST = (('Summarize this chat for the nightly digest.', 'digest'),)


class ChildPipelineTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(automation, 'PREFIXES', DIGEST)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fixture(self):
        index = {'root': {'id': 'root', 'source': 'claude', 'content_sig': 'root-new'},
                 'child': {'id': 'child', 'source': 'claude', 'root_key': 'root', 'is_child': True,
                           'agent_label': 'Review parser', 'content_sig': 'child-new',
                           'first_prompt': automation.PREFIXES[0][0]}}
        projects = {'projects': [{'id': 'curated', 'name': 'Curated', 'custom': 'preserved'}],
                    'assignments': {'root': 'curated'}}
        return index, projects

    def test_child_inherits_project_even_when_prompt_looks_like_automation(self):
        index, projects = self.fixture()
        original = copy.deepcopy(projects)
        effective = automation.effective_projects(index, projects)
        self.assertEqual(effective['assignments']['child'], 'curated')
        self.assertEqual(effective['projects'], projects['projects'])
        self.assertEqual(projects, original)

    def test_children_only_label_does_not_call_model_or_change_root(self):
        index, _ = self.fixture()
        old = {'root': {'title': 'Curated title', 'content_sig': 'old'}, 'old-unindexed': {'title': 'Preserved'}}
        with tempfile.TemporaryDirectory() as tmp:
            ip, ep = os.path.join(tmp, 'index.json'), os.path.join(tmp, 'enrich.json')
            with open(ip, 'w') as fh: json.dump(index, fh)
            with open(ep, 'w') as fh: json.dump(old, fh)
            with patch.object(enrich, 'INDEX', ip), patch.object(enrich, 'OUT', ep), patch.object(enrich.sys, 'argv', ['enrich.py', '--children-only']), patch.object(enrich.subprocess, 'run', side_effect=AssertionError('provider called')), contextlib.redirect_stdout(io.StringIO()):
                enrich.main()
            with open(ep) as fh: labels = json.load(fh)
        self.assertEqual(labels['root'], old['root'])
        self.assertEqual(labels['old-unindexed'], old['old-unindexed'])
        self.assertEqual(labels['child']['title'], 'Review parser')
        self.assertEqual(labels['child']['model'], 'deterministic')

    def test_classify_child_backlog_does_not_call_model(self):
        index, projects = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            paths = {name: os.path.join(tmp, name + '.json') for name in ('index', 'enrich', 'projects')}
            for name, obj in [('index', index), ('enrich', {}), ('projects', projects)]:
                with open(paths[name], 'w') as fh: json.dump(obj, fh)
            with patch.object(classify, 'INDEX', paths['index']), patch.object(classify, 'ENRICH', paths['enrich']), patch.object(classify, 'PROJECTS', paths['projects']), patch.object(classify.sys, 'argv', ['classify.py']), patch.object(classify.subprocess, 'run', side_effect=AssertionError('provider called')), contextlib.redirect_stdout(io.StringIO()):
                classify.main()
            with open(paths['projects']) as fh: result = json.load(fh)
        self.assertEqual(result['projects'], projects['projects'])
        self.assertEqual(result['assignments'], {'root': 'curated', 'child': 'curated'})


class ChildAnalyticsTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(automation, 'PREFIXES', DIGEST)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ops_filter_follows_root_before_child_labels_exist(self):
        import datetime
        import usage_history
        day = datetime.date.today().isoformat()
        bucket = {'day': day, 'model': 'm', 'in': 100, 'turns': 1}
        index = {'ops': {'source': 'claude', 'first_prompt': automation.PREFIXES[0][0], 'usage_buckets': [bucket]},
                 'ops-child': {'source': 'claude', 'root_key': 'ops', 'is_child': True, 'usage_buckets': [bucket]},
                 'human': {'source': 'claude', 'usage_buckets': [bucket]},
                 'human-child': {'source': 'claude', 'root_key': 'human', 'is_child': True, 'usage_buckets': [bucket]}}
        # A child's own operational label must not hide a human-root delegate.
        labels = {'human-child': {'is_ops': True}}
        with patch.object(usage_history, '_jload', side_effect=lambda name, default: {'index': index, 'enrich': labels}.get(name, default)):
            ordinary = usage_history.compute(1)
            everything = usage_history.compute(1, include_ops=True)
        self.assertEqual(ordinary['totals']['in'], 200)
        self.assertEqual(ordinary['totals']['chats'], 2)
        self.assertEqual(everything['totals']['in'], 400)

if __name__ == '__main__':
    unittest.main()

class BackgroundSyncTests(unittest.TestCase):
    def test_remote_sync_only_runs_deterministic_pipeline(self):
        import remote_sync
        from types import SimpleNamespace
        with patch.object(remote_sync.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(remote_sync._run_pipeline())
        args = run.call_args.args[0]
        self.assertIn('--parse-only', args)
        self.assertIn('--keep-missing', args)
