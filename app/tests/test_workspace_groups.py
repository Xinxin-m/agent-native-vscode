import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import workspace_groups
import pipeline


class WorkspaceGroupingTests(unittest.TestCase):
    def test_cross_provider_workspace_and_curated_placement(self):
        ix = {'a': {'cwd': '/work/alpha', 'source': 'claude'},
              'b': {'cwd': '/work/alpha', 'source': 'codex'},
              'c': {'cwd': '/other/alpha'},
              'd': {'cwd': '/work/alpha'},
              'child': {'cwd': '/work/alpha', 'is_child': True, 'root_key': 'a'}}
        original = {'projects': [{'id': 'manual', 'name': 'Curated'}], 'assignments': {'d': 'manual'}}
        result = workspace_groups.seed_projects(ix, original)
        self.assertEqual(result['assignments']['a'], result['assignments']['b'])
        self.assertNotEqual(result['assignments']['a'], result['assignments']['c'])
        self.assertEqual(result['assignments']['d'], 'manual')
        self.assertEqual(result['assignments']['child'], result['assignments']['a'])
        self.assertNotIn('a', original['assignments'])
        self.assertEqual(workspace_groups.seed_projects(ix, result), result)

    def test_empty_unknown_workspace_and_manual_changes_preserved(self):
        result = workspace_groups.seed_projects({'a': {}, 'b': {'cwd': '/'}, 'c': {'cwd': '/p'}},
             {'projects': [], 'assignments': {}})
        self.assertEqual(set(result['assignments']), {'c'})
        result['assignments']['c'] = 'new-manual'
        self.assertEqual(workspace_groups.seed_projects({'c': {'cwd': '/p'}}, result)['assignments']['c'], 'new-manual')

    def test_parse_only_never_runs_model_stages(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'LOG_PATH', os.path.join(tmp, 'log')), patch.object(pipeline, 'run_stage', return_value=True) as run:
            self.assertTrue(pipeline._run_pass({}, 'test', False, False, True, False))
        self.assertEqual([(c.args[1], c.args[2]) for c in run.call_args_list],
                         [('indexer.py', []), ('enrich.py', ['--children-only']), ('workspace_groups.py', []), ('project_review.py', [])])


if __name__ == '__main__':
    unittest.main()

class CompanionTests(unittest.TestCase):
    def test_launcher_supplies_checkout_and_configured_port(self):
        import json
        from pathlib import Path
        import start_project_map
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'project-map').mkdir()
            (root / 'project-map/server.py').write_text('')
            (root / 'data').mkdir()
            (root / 'data/config.json').write_text(json.dumps({'port': 8123}))
            with patch.object(start_project_map, 'ROOT', root), patch.object(start_project_map.urllib.request, 'urlopen', side_effect=OSError), patch.object(start_project_map.subprocess, 'Popen') as spawn:
                self.assertTrue(start_project_map.start())
            self.assertEqual(spawn.call_args.kwargs['env']['LIBRARY_ROOT'], str(root))
            self.assertEqual(spawn.call_args.kwargs['env']['LIBRARY_URL'], 'http://127.0.0.1:8123')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])

    def test_bundled_graph_reads_its_own_library_index(self):
        import json
        import subprocess
        from pathlib import Path
        root = Path(workspace_groups.HERE)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            data.mkdir()
            (data / 'index.json').write_text(json.dumps({'a': {'cwd': '/project', 'source': 'claude'}}))
            (data / 'projects.json').write_text(json.dumps({'projects': [{'id': 'p', 'name': 'Project'}], 'assignments': {'a': 'p'}}))
            result = subprocess.run([sys.executable, '-c', "from graph_data import Catalog; c=Catalog(); assert c.projects()[0]['count']==1; assert c.members('p')=={'a'}"], cwd=root/'project-map', env=dict(os.environ, LIBRARY_ROOT=tmp, PYTHONPATH=str(root)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

class ManualGroupingTests(unittest.TestCase):
    def seeded(self):
        return workspace_groups.seed_projects({'a': {'cwd': '/work/p'}}, {'projects': [], 'assignments': {}})

    def test_same_group_choice_survives_paid_refinement(self):
        value = self.seeded()
        pid = value['assignments']['a']
        workspace_groups.mark_manual(value, ['a'])
        workspace_groups.refine_workspace_seeds(value)
        self.assertEqual(value['assignments']['a'], pid)

    def test_explicit_ungrouped_survives_parse_and_refinement(self):
        value = self.seeded()
        workspace_groups.mark_manual(value, ['a'])
        value['assignments'].pop('a')
        workspace_groups.refine_workspace_seeds(value)
        value = workspace_groups.seed_projects({'a': {'cwd': '/work/p'}}, value)
        self.assertNotIn('a', value['assignments'])

    def test_renamed_workspace_preserves_existing_and_future_members(self):
        value = self.seeded()
        pid = value['assignments']['a']
        workspace_groups.preserve_project(value, pid)
        value['projects'][0]['name'] = 'My topic'
        value = workspace_groups.seed_projects({'a': {'cwd': '/work/p'}, 'b': {'cwd': '/work/p'}}, value)
        workspace_groups.refine_workspace_seeds(value)
        self.assertEqual(value['assignments'], {'a': pid, 'b': pid})
        self.assertEqual(value['projects'][0]['name'], 'My topic')

    def test_untouched_seed_can_still_be_refined(self):
        value = self.seeded()
        workspace_groups.refine_workspace_seeds(value)
        self.assertNotIn('a', value['assignments'])
