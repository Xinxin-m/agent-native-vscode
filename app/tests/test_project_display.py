"""Small projects fold into the misc project in what clients see; counts per provider."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import projects


def meta(source='claude', **extra):
    return dict({'source': source, 'cwd': '/w', 'ts_last': '2026-11-15'}, **extra)


class DisplayStateTests(unittest.TestCase):
    def test_small_projects_fold_into_other_except_exempt_and_manual(self):
        index = {f'big{i}': meta('codex' if i < 2 else 'claude') for i in range(5)}
        index.update({'small1': meta(), 'small2': meta('codex'), 'manual': meta(), 'kid': meta(is_child=True, parent_key='small1'),
                      'pin1': meta(), 'cur1': meta(), 'old1': meta()})
        state = {'projects': [{'id': 'big', 'name': 'Big'}, {'id': 'small', 'name': 'Small'}, {'id': 'pin', 'name': 'mm', 'pinned': True},
                              {'id': 'cur', 'name': 'Fellowship', 'user_curated': True}, {'id': 'old', 'name': 'Old', 'archived': True},
                              {'id': 'misc', 'name': 'Other things'}],
                 'assignments': dict({f'big{i}': 'big' for i in range(5)}, small1='small', small2='small', manual='small',
                                     kid='small', pin1='pin', cur1='cur', old1='old'),
                 'manual_assignments': ['manual']}
        shown = projects.display_state(index, state, {}, {'misc_project': 'Other things'})
        a = shown['assignments']
        self.assertEqual((a['small1'], a['small2'], a['kid'], a['manual']), ('misc', 'misc', 'misc', 'small'))
        self.assertEqual((a['big0'], a['pin1'], a['cur1'], a['old1']), ('big', 'pin', 'cur', 'old'))
        self.assertEqual(shown['dissolved'], {'small': 3})
        self.assertEqual(shown['stored_assignments'], state['assignments'])
        self.assertEqual(shown['project_counts']['big'], {'all': 5, 'claude': 3, 'codex': 2})
        self.assertEqual(shown['project_counts']['misc'], {'all': 2, 'claude': 1, 'codex': 1}, 'children are not counted')
        self.assertEqual(state['assignments']['small1'], 'small', 'input is not mutated')

    def test_without_a_misc_project_nothing_moves(self):
        state = {'projects': [{'id': 'small', 'name': 'Small'}], 'assignments': {'a': 'small'}}
        shown = projects.display_state({'a': meta()}, state, {}, {})
        self.assertEqual((shown['assignments'], shown['dissolved']), ({'a': 'small'}, {}))


if __name__ == '__main__':
    unittest.main()
