"""doctor.py must not call another install's server healthy."""
import io
import json
import unittest
from unittest.mock import patch
import doctor


def answer(payload):
    return patch.object(doctor.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode()))


class DoctorServerTests(unittest.TestCase):
    def setUp(self):
        doctor.results.clear()

    def test_this_checkout_is_healthy(self):
        with answer({'config': {'data_dir': doctor.DATA}, 'chats': [], 'compose_options': {}}):
            doctor.check_server({'port': 9})
        self.assertEqual(doctor.results[0][:2], ('ok', 'server'))

    def test_other_install_or_unknown_server_fails_plainly(self):
        for payload in ({'config': {'data_dir': '/elsewhere/data'}, 'chats': []}, {'chats': []}):
            doctor.results.clear()
            with self.subTest(payload=payload), answer(payload):
                doctor.check_server({'port': 9})
            self.assertEqual(doctor.results[0][0], 'bad')
            self.assertIn('another Library install', doctor.results[0][2])


if __name__ == '__main__':
    unittest.main()
