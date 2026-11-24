import json
import os
import tempfile
import unittest

import autoclassify
from enrich_fixture import Env

PROJECTS = {'projects': [{'id': 'alpha', 'name': 'Alpha'}, {'id': 'beta', 'name': 'Beta'},
                         {'id': 'ws', 'name': 'ws', 'grouping_source': 'workspace'}],
            'assignments': {}, 'manual_assignments': []}


def meta(cwd, n_user=3, **extra):
    return dict({'cwd': cwd, 'n_user': n_user, 'node': 'mac'}, **extra)


class DecideTests(unittest.TestCase):
    def projects(self, **extra):
        value = json.loads(json.dumps(PROJECTS))
        value.update(extra)
        return value

    def index(self, folders):
        index, assignments = {}, {}
        for i, (cwd, pid) in enumerate(folders):
            index['c%d' % i] = meta(cwd)
            assignments['c%d' % i] = pid
        return index, assignments

    def test_folder_of_existing_chats_decides_and_mixed_folder_does_not(self):
        index, assign = self.index([('/w/a', 'alpha')] * 3 + [('/w/mix', 'alpha'), ('/w/mix', 'beta'), ('/w/mix', 'beta')])
        projects = self.projects(assignments=assign)
        votes = autoclassify.folder_votes(index, projects)
        self.assertEqual(autoclassify.decide(meta('/w/a'), {}, projects, {}, votes)[:2], ('alpha', 'folders'))
        self.assertEqual(autoclassify.decide(meta('/w/a/sub'), {}, projects, {}, votes)[:2], ('alpha', 'folders'))
        self.assertIsNone(autoclassify.decide(meta('/w/mix'), {}, projects, {}, votes))
        self.assertIsNone(autoclassify.decide(meta('/w/new'), {}, projects, {}, votes))

    def test_two_chats_in_a_folder_are_not_enough(self):
        index, assign = self.index([('/w/a', 'alpha')] * 2)
        votes = autoclassify.folder_votes(index, self.projects(assignments=assign))
        self.assertIsNone(autoclassify.decide(meta('/w/a'), {}, self.projects(), {}, votes))

    def test_project_workspace_wins_and_deepest_root_is_used(self):
        projects = self.projects()
        projects['projects'][0]['default_workspace'] = {'host_id': 'mac', 'path': '/w/alpha'}
        projects['projects'][1]['workspace_mappings'] = [{'host': 'mac', 'path': '/w/alpha/deep'}]
        self.assertEqual(autoclassify.decide(meta('/w/alpha/x'), {}, projects, {}, {})[:2], ('alpha', 'workspace'))
        self.assertEqual(autoclassify.decide(meta('/w/alpha/deep/x'), {}, projects, {}, {})[:2], ('beta', 'workspace'))
        self.assertIsNone(autoclassify.decide(meta('/w/alpha/x', node='sc'), {}, projects, {}, {}))  # another host

    def test_file_folder_that_disagrees_with_cwd_means_undecided(self):
        projects = self.projects()
        projects['projects'][0]['default_workspace'] = {'host_id': 'mac', 'path': '/w/alpha'}
        projects['projects'][1]['default_workspace'] = {'host_id': 'mac', 'path': '/w/beta'}
        self.assertEqual(autoclassify.decide(meta('/w/alpha'), {}, projects, {}, {}, ['/w/alpha/a.py'])[0], 'alpha')
        self.assertIsNone(autoclassify.decide(meta('/w/alpha'), {}, projects, {}, {}, ['/w/beta/b.py']))
        self.assertEqual(autoclassify.decide(meta('/elsewhere'), {}, projects, {}, {}, ['/w/beta/b.py'])[0], 'beta')

    def test_rules_decide_first_and_conflicting_rules_do_not(self):
        projects = self.projects()
        config = {'classification_rules': [{'project_id': 'beta', 'any': ['essay'], 'reason': 'essay work'}]}
        got = autoclassify.decide(meta('/w/x', title_seed='Application essay'), {}, projects, config, {})
        self.assertEqual(got, ('beta', 'rules', 'essay work'))
        config['classification_rules'].append({'project_id': 'alpha', 'any': ['essay']})
        self.assertIsNone(autoclassify.decide(meta('/w/x', title_seed='Application essay'), {}, projects, config, {}))

    def test_unplaced_respects_turns_manual_children_and_existing_placement(self):
        projects = self.projects(assignments={'placed': 'alpha', 'seed': 'ws'}, manual_assignments=['manual'],
                                 workspace_seeds={'seed': 'ws'})
        self.assertTrue(autoclassify.unplaced('new', meta('/w'), projects))
        self.assertTrue(autoclassify.unplaced('seed', meta('/w'), projects))
        self.assertFalse(autoclassify.unplaced('new', meta('/w', n_user=2), projects))
        self.assertFalse(autoclassify.unplaced('manual', meta('/w'), projects))
        self.assertFalse(autoclassify.unplaced('placed', meta('/w'), projects))
        self.assertFalse(autoclassify.unplaced('new', meta('/w', is_child=True), projects))


class EnrichWiringTests(unittest.TestCase):
    def setup_env(self, env, flag):
        env.cfg['auto_classify_code'] = flag
        for name in ('a', 'b', 'c'):
            env.chat(name, 2, cwd='/w/home')
            env.proj['assignments'][name] = 'alpha'
        env.chat('new', 3, cwd='/w/home')
        env.chat('manual', 3, cwd='/w/other')
        env.proj['manual_assignments'] = ['manual']
        env.proj['assignments']['manual'] = 'beta'
        env.done = {k: {'title': 'T', 'content_sig': 'x', 'labelled_n_user': 9, 'model': 'm'} for k in env.index}

    def test_code_places_logs_and_never_calls_the_model(self):
        with Env() as env:
            self.setup_env(env, True)
            env.run('--children-only')  # no model path
            self.assertNotIn('new', env.proj['assignments'])
            env.run('--force-keys', 'zzz')  # reaches the placement step without labelling any chat
            self.assertEqual(env.prompts, [])
            self.assertEqual(env.proj['assignments']['new'], 'alpha')
            self.assertEqual(env.proj['assignments']['manual'], 'beta')
            rows = autoclassify.read_log(env.tmp.name, 'new')
        self.assertEqual((rows[0]['method'], rows[0]['project_id'], rows[0]['by']), ('folders', 'alpha', 'code'))

    def test_default_off_places_nothing(self):
        with Env() as env:
            self.setup_env(env, False)
            env.run('--force-keys', 'zzz')
            self.assertNotIn('new', env.proj['assignments'])


if __name__ == '__main__':
    unittest.main()
