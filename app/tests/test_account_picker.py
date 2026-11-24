import unittest
from unittest.mock import patch

import accounts
import accounts_guard  # noqa: F401

ROWS = [
    {'id': 'claude-default', 'tool': 'claude', 'label': 'Main', 'email': 'me@x.test', 'logged_in': True},
    {'id': 'claude-personal', 'tool': 'claude', 'label': 'personal', 'email': 'me@x.test', 'logged_in': True, 'duplicate_of': 'claude-default'},
    {'id': 'codex-default', 'tool': 'codex', 'label': 'Work', 'email': 'w@x.test', 'logged_in': True},
]


class PickerTests(unittest.TestCase):
    def test_duplicates_hidden_and_auto_maps_to_the_shown_twin(self):
        with patch.object(accounts, 'resolve_launch', side_effect=lambda tool, model, d=None: {'account_id': {'claude': 'claude-personal', 'codex': 'codex-default'}[tool]}):
            out = accounts.picker_list({}, rows=ROWS)
        self.assertEqual([a['id'] for a in out['accounts']], ['claude-default', 'codex-default'])
        self.assertEqual(set(out['accounts'][0]), {'id', 'tool', 'label', 'email', 'logged_in'})
        self.assertEqual(out['auto']['claude'], {'account_id': 'claude-default', 'label': 'Main', 'email': 'me@x.test'})
        self.assertEqual(out['auto']['codex']['label'], 'Work')

    def test_auto_is_null_when_nothing_resolves(self):
        with patch.object(accounts, 'resolve_launch', side_effect=ValueError('none')):
            self.assertEqual(accounts.picker_list({}, rows=ROWS)['auto'], {'claude': None, 'codex': None})


if __name__ == '__main__':
    unittest.main()
