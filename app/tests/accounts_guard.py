"""Import this after `import accounts` in any test that touches it.

accounts.py fixes its file paths at import. This points them at a throwaway
folder for the whole test process, so a background sign-in thread that outlives
a test can never write the real data/accounts.json.
"""
import atexit
import os
import shutil
import tempfile

import accounts

ROOT = tempfile.mkdtemp(prefix='accounts-test-')
atexit.register(shutil.rmtree, ROOT, True)
accounts.DATA = ROOT
accounts.ACCOUNTS_FILE = os.path.join(ROOT, 'accounts.json')
accounts.CONFIG_FILE = os.path.join(ROOT, 'config.json')
accounts.BACKUPS = os.path.join(ROOT, 'account-backups')
