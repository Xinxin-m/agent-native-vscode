"""Account discovery, quota switch notices and the pool watcher, through their public functions."""
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounts
import accounts_guard  # noqa: F401  temp DATA, registry and profile roots
import attention_watch
import notices
import notifications
import pools

NOW = 1795514400.0  # 2026-11-24 10:00 local time under TZ=UTC
RESET = NOW + 5 * 3600 + 40 * 60  # 15:40, "3:40 PM"


def utc():
    old = os.environ.get('TZ')
    os.environ['TZ'] = 'UTC'
    time.tzset()

    def restore():
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()
    return restore


def claude_profile(path, email, uuid):
    os.makedirs(path)
    with open(os.path.join(path, '.claude.json'), 'w') as fh:
        json.dump({'oauthAccount': {'accountUuid': uuid, 'emailAddress': email, 'organizationUuid': 'org'}}, fh)


def codex_profile(path, email, account_id):
    os.makedirs(path)
    claims = base64.urlsafe_b64encode(json.dumps({'email': email, 'https://api.openai.com/auth': {'chatgpt_user_id': 'user-' + account_id}}).encode()).decode().rstrip('=')
    with open(os.path.join(path, 'auth.json'), 'w') as fh:
        json.dump({'tokens': {'account_id': account_id, 'id_token': 'x.' + claims + '.y'}}, fh)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        for root in accounts.PROFILE_ROOTS.values():
            shutil.rmtree(root, ignore_errors=True)
            os.makedirs(root)
        self.addCleanup(lambda: [shutil.rmtree(r, ignore_errors=True) for r in accounts.PROFILE_ROOTS.values()])
        if os.path.exists(accounts.ACCOUNTS_FILE):
            os.unlink(accounts.ACCOUNTS_FILE)
        claude, codex = accounts.PROFILE_ROOTS['claude'], accounts.PROFILE_ROOTS['codex']
        claude_profile(os.path.join(claude, 'work'), 'work@example.test', 'uuid-work')
        os.makedirs(os.path.join(claude, 'never-signed-in'))
        codex_profile(os.path.join(codex, 'personal'), 'me@example.test', 'acct-1')
        codex_profile(os.path.join(codex, 'copy'), 'me@example.test', 'acct-1')

    def fetch(self, d, a):
        email = {'claude-work': 'work@example.test', 'codex-personal': 'me@example.test',
                 'codex-copy': 'me@example.test'}.get(a['id'])
        return {'logged_in': bool(email), 'email': email or '', 'windows': []}

    def test_wiped_registry_is_rebuilt_from_signed_in_profile_folders(self):
        data = accounts.load()
        pending = {a['id']: a for a in data['pending_accounts']}
        self.assertEqual(set(pending), {'claude-work', 'codex-copy', 'codex-personal'})
        self.assertEqual(pending['claude-work']['label'], 'work@example.test')
        self.assertEqual(pending['codex-personal']['label'], 'me@example.test')
        with patch.object(accounts, '_fetch_claude', self.fetch), patch.object(accounts, '_fetch_codex', self.fetch):
            accounts.refresh()
        saved = json.load(open(accounts.ACCOUNTS_FILE))
        self.assertEqual({a['id'] for a in saved['accounts']},
                         {'claude-default', 'codex-default', 'claude-work', 'codex-copy', 'codex-personal'})
        rows = {r['id']: r for r in accounts.payload(accounts.load())['accounts']}
        self.assertEqual([r.get('duplicate_of') for r in (rows['codex-copy'], rows['codex-personal'])], [None, 'codex-copy'])

    def test_registered_account_stays_when_its_login_file_disappears(self):
        with patch.object(accounts, '_fetch_claude', self.fetch), patch.object(accounts, '_fetch_codex', self.fetch):
            accounts.refresh()
        os.unlink(os.path.join(accounts.PROFILE_ROOTS['codex'], 'personal', 'auth.json'))
        with patch.object(accounts, '_fetch_claude', self.fetch), patch.object(accounts, '_fetch_codex', self.fetch):
            accounts.refresh()
        self.assertIn('codex-personal', {a['id'] for a in accounts.load()['accounts']})

    def test_registered_folder_is_not_added_twice(self):
        data = accounts.load()
        accounts.save(data)
        self.assertEqual(len(accounts.load()['pending_accounts']), 3)


class SwitchNoticeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(utc())
        self.path = os.path.join(accounts.DATA, 'notices.json')
        if os.path.exists(self.path):
            os.unlink(self.path)
        reset = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(RESET))
        self.data = {'accounts': [{'id': 'claude-default', 'tool': 'claude', 'label': 'Default', 'config_dir': '/x'},
                                  {'id': 'claude-work', 'tool': 'claude', 'label': 'Work', 'config_dir': '/y'}],
                     'pending_accounts': [],
                     'cache': {'claude-default': {'logged_in': True, 'email': 'me@example.test', 'windows': [
                                   {'kind': '5h', 'label': 'Session (5h)', 'percent': 92, 'resets_at': reset}]},
                               'claude-work': {'logged_in': True, 'email': 'work@example.test', 'windows': [
                                   {'kind': '5h', 'label': 'Session (5h)', 'percent': 10, 'resets_at': reset}]}},
                     'state': {'active': {'claude': 'claude-default'}, 'global_active': {}}}

    def test_switch_notice_names_both_accounts_and_the_reset_once_per_window(self):
        with patch.object(accounts, 'load', return_value=self.data):
            self.assertTrue(accounts.announce_switch('claude-default', 'claude-work', now=NOW))
            self.assertFalse(accounts.announce_switch('claude-default', 'claude-work', now=NOW + 60))
        rows = notices.listing(self.path, NOW)['notices']
        self.assertEqual([r['message'] for r in rows], [
            'me@example.test is at 92% of its Session (5h) limit (resets 3:40 PM); new work goes to work@example.test.'])
        self.assertEqual(rows[0]['kind'], 'account_switch')

    def test_early_warning_on_the_configured_account_names_where_new_work_goes(self):
        warning, = accounts.account_warnings(self.data['accounts'][0], self.data['cache']['claude-default'], NOW,
                                             accounts._warning_successor(self.data, self.data['accounts'][0]))
        self.assertEqual(warning['message'],
                         'me@example.test is at 92% of its Session (5h) limit (resets 3:40 PM); new work goes to work@example.test.')
        self.assertIsNone(accounts._warning_successor(self.data, self.data['accounts'][1]))

    def test_quota_notices_reach_the_feed_with_a_banner(self):
        notices.announce(self.path, 'switch:a', 'account_switch', 'A is at 92%; new work goes to B.', at=RESET, now=NOW)
        with patch.object(notifications, '_path', return_value=self.path):
            items, _ = notifications._app_items(NOW, [])
            later, _ = notifications._app_items(RESET + 1, [])
        self.assertEqual([(i['title'], i['body'], i['banner']) for i in items],
                         [('Moving work to another account', 'A is at 92%; new work goes to B.', True)])
        self.assertEqual(later, [])

    def test_banner_posts_new_quota_notice_but_not_old_ones(self):
        posted = []
        banners = attention_watch.Banners(lambda *a: posted.append(a))
        old = {'id': 'notice:old', 'state': 'open', 'banner': True, 'title': 'Usage is high', 'body': 'old'}
        new = {'id': 'notice:new', 'state': 'open', 'banner': True, 'title': 'Moving work to another account', 'body': 'A; B'}
        banners.process([dict(old, banner=False)], {})  # served before the server learned the banner flag
        banners.process([old, new, dict(new, id='notice:plain', banner=False)], {})
        self.assertEqual(posted, [('Moving work to another account', '', 'A; B')])


CCS_RAW = {'order': ['a@x.test', 'b@x.test'], 'order_mode': 'file', 'accounts': [
    {'id': 'a@x.test', 'email': 'a@x.test', 'state': 'available', 'cooldown_until': None, 'windows': [
        {'type': 'five_hour', 'label': '5h', 'used_percent': 91, 'reset_at': '2026-11-24T15:40:00Z'}]},
    {'id': 'b@x.test', 'email': 'b@x.test', 'state': 'available', 'cooldown_until': None, 'windows': [
        {'type': 'five_hour', 'label': '5h', 'used_percent': 5, 'reset_at': '2026-11-24T15:40:00Z'}]},
    {'id': 'c@x.test', 'email': 'c@x.test', 'state': 'paused', 'cooldown_until': None, 'windows': []}]}


def codex_lb(primary_a, status_a='active', latest='acc-a'):
    accounts_payload = {'accounts': [
        {'accountId': 'acc-a', 'email': 'a@y.test', 'displayName': 'a@y.test', 'status': status_a,
         'usage': {'primaryRemainingPercent': 100 - primary_a, 'secondaryRemainingPercent': 60},
         'resetAtPrimary': '2026-11-24T15:40:00Z', 'windowMinutesPrimary': 300,
         'resetAtSecondary': '2026-11-29T15:40:00Z', 'windowMinutesSecondary': 10080},
        {'accountId': 'acc-b', 'email': 'b@y.test', 'displayName': 'Backup', 'status': 'active',
         'usage': {'primaryRemainingPercent': 90}, 'resetAtPrimary': '2026-11-24T15:40:00Z', 'windowMinutesPrimary': 300}]}
    return accounts_payload, {'requests': [{'accountId': latest}], 'total': 1, 'hasMore': False}


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(utc())
        self.path = os.path.join(tempfile.mkdtemp(), 'notices.json')
        pools._serving.clear()

    def run_poll(self, *snaps, now=NOW):
        for snap in snaps:
            pools.poll(self.path, 90, now=now, readers=(lambda: snap,))
        return [r['message'] for r in notices.listing(self.path, now)['notices']]

    def test_ccs_serving_account_over_threshold_warns_once_with_the_next_account(self):
        snap = pools.parse_ccs(CCS_RAW)
        self.assertEqual(snap['serving'], 'a@x.test')
        self.assertEqual(self.run_poll(snap, snap), [
            'Claude pool (CCS): a@x.test is at 91% of its 5h limit (resets 3:40 PM); new work goes to b@x.test when it runs out.'])

    def test_ccs_move_after_cooldown_names_both_accounts(self):
        moved = json.loads(json.dumps(CCS_RAW))
        moved['accounts'][0].update(state='cooling', cooldown_until=(NOW + 1800) * 1000)
        moved['accounts'][0]['windows'][0]['used_percent'] = 100
        messages = self.run_poll(pools.parse_ccs(CCS_RAW), pools.parse_ccs(moved))
        self.assertIn('Claude pool (CCS): a@x.test is at 100% of its 5h limit (resets 3:40 PM); new work goes to b@x.test.', messages)

    def test_codex_lb_rotation_between_healthy_accounts_is_quiet(self):
        first = pools.parse_codex_lb(*codex_lb(20))
        second = pools.parse_codex_lb(*codex_lb(20, latest='acc-b'))
        self.assertEqual(self.run_poll(first, second), [])

    def test_codex_lb_threshold_then_move(self):
        high = pools.parse_codex_lb(*codex_lb(95))
        moved = pools.parse_codex_lb(*codex_lb(100, status_a='quota_exceeded', latest='acc-b'))
        self.assertEqual(sorted(self.run_poll(high, moved)), [
            'Codex pool (codex-lb): a@y.test is at 100% of its Session (5h) limit (resets 3:40 PM); new work goes to Backup.',
            'Codex pool (codex-lb): a@y.test is at 95% of its Session (5h) limit (resets 3:40 PM); new work goes to Backup when it runs out.'])

    def test_empty_or_unreachable_pools_raise_nothing(self):
        def down():
            raise ConnectionRefusedError('pool is not running')
        empty = pools.parse_ccs({'order': [], 'order_mode': 'file', 'accounts': []})
        pools.poll(self.path, 90, now=NOW, readers=(down, lambda: empty))
        self.assertEqual(notices.listing(self.path, NOW)['notices'], [])
        with self.assertRaises(ValueError):
            pools.parse_codex_lb({'detail': 'unauthorized'}, {'requests': []})


if __name__ == '__main__':
    unittest.main()
