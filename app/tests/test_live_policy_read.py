"""Settings reads the stop rule without waiting behind an automatic stop."""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import live


class PolicyReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for name in ('POLICY_JSON', 'OUTCOMES_JSON'):
            patcher = patch.object(live, name, os.path.join(temp.name, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        with open(live.OUTCOMES_JSON, 'w') as f:
            json.dump([{'id': 'mac:1', 'name': 'synthetic', 'state': 'stopped', 'source': 'automatic', 'at': 1, 'identity': {'pid': 1}}], f)

    def test_policy_answers_while_lock_is_held(self):
        held, release = threading.Event(), threading.Event()
        def hold():
            with live._POLICY_LOCK:
                held.set()
                release.wait(5)
        thread = threading.Thread(target=hold)
        thread.start()
        held.wait(5)
        try:
            start = time.time()
            value = live.get_policy(None, {}, None)
            self.assertLess(time.time() - start, 1)
            self.assertEqual(value['outcomes'][0]['name'], 'synthetic')
            self.assertNotIn('identity', value['outcomes'][0])
        finally:
            release.set()
            thread.join()


if __name__ == '__main__':
    unittest.main()
