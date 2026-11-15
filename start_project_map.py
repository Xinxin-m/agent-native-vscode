#!/usr/bin/env python3
"""Start the bundled read-only project map without opening a browser."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
PORT = 53962


def start():
    companion = ROOT / 'project-map'
    if not (companion / 'server.py').is_file():
        return False
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/projects', timeout=2) as response:
            if isinstance(json.load(response).get('projects'), list):
                return True
    except (OSError, ValueError):
        pass
    config = ROOT / 'data/config.json'
    settings = json.loads(config.read_text()) if config.exists() else {}
    output = companion / 'output'
    output.mkdir(exist_ok=True)
    env = dict(os.environ, LIBRARY_ROOT=str(ROOT),
               LIBRARY_URL=f"http://127.0.0.1:{int(settings.get('port', 8744))}")
    with (output / 'server.log').open('a') as log:
        subprocess.Popen([sys.executable, str(companion / 'server.py'), '--port', str(PORT)],
                         cwd=companion, env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
    return True


if __name__ == '__main__':
    start()
