"""Restart com.chatdashboard.server without breaking a running chat.

    python3 scripts/restart_server.py [--timeout HOURS] [--remove PATH ...] [--log FILE]

Runs started through provider_host.py survive a restart and are reattached by
the new service. A run whose provider is a direct child of the old service
(started before provider hosts, or remote) would be interrupted, so this waits
until none is active, then restarts the server and the attention watcher and
checks that every hosted run is still owned. --remove deletes files right
before the restart (each is logged to DELETED_<date>.md beside --log).
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'data' / 'execution' / 'runs'
SERVER = f'gui/{os.getuid()}/com.chatdashboard.server'
ATTENTION = f'gui/{os.getuid()}/com.chatdashboard.attention'
TERMINAL = {'completed', 'failed', 'interrupted'}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(message):
    print(f'{datetime.datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z} {message}', flush=True)


def service_pid(service):
    result = subprocess.run(['/bin/launchctl', 'print', service], capture_output=True, text=True)
    match = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.MULTILINE)
    return int(match[1]) if result.returncode == 0 and match else None


def api(path):
    with OPENER.open('http://127.0.0.1:8744' + path, timeout=8) as response:
        return json.load(response)


def active_runs():
    """Every non-terminal run, from run.json (status changes are written at once)."""
    runs = []
    for path in RUNS.glob('*/run.json'):
        run = json.loads(path.read_text())
        if run.get('status') not in TERMINAL:
            runs.append(run)
    return runs


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def describe(run):
    host = 'hosted' if run.get('process_host') else 'direct child'
    return (f"{run['id']} {run.get('provider')} session {run.get('session_id') or '-'} {run['status']}/{run.get('phase')} "
            f"pid {run.get('process_pid')} ({host})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, default=24, help='hours to wait for direct-child runs to finish')
    parser.add_argument('--remove', action='append', default=[], help='file to delete right before the restart')
    parser.add_argument('--log', default='', help='deploy log path; DELETED_<date>.md is written beside it')
    args = parser.parse_args()

    old_pid = service_pid(SERVER)
    if not old_pid:
        raise SystemExit('Could not read the current server PID from launchd')
    log(f'Server PID {old_pid}. Waiting until no direct-child run is active.')
    deadline = time.monotonic() + args.timeout * 3600
    shown = None
    while True:
        if service_pid(SERVER) != old_pid:
            raise SystemExit('The server was restarted by someone else; nothing done.')
        legacy = [r for r in active_runs() if not r.get('process_host')]
        if not legacy:
            break
        ids = sorted(r['id'] for r in legacy)
        if ids != shown:
            log('Waiting for: ' + '; '.join(describe(r) for r in legacy))
            shown = ids
        if time.monotonic() > deadline:
            raise SystemExit('Time limit reached with direct-child runs active. Server was not restarted.')
        time.sleep(2)

    hosted = active_runs()
    for path in map(Path, args.remove):
        if path.exists():
            path.unlink()
            stamp = datetime.date.today().isoformat()
            ledger = Path(args.log).parent / f'DELETED_{stamp}.md' if args.log else None
            if ledger:
                with ledger.open('a') as handle:
                    handle.write(f'- {datetime.datetime.now().astimezone():%H:%M %Z} deleted `{path}` (approved in the pending deploy list)\n')
            log(f'Deleted {path}')
    started = time.time()
    subprocess.run(['/bin/launchctl', 'kickstart', '-k', SERVER], check=True)
    for _ in range(120):
        try:
            if service_pid(SERVER) not in (None, old_pid) and isinstance(api('/api/runs').get('runs'), list):
                break
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    else:
        raise SystemExit('Server restarted but /api/runs did not answer within 60 s')
    new_pid = service_pid(SERVER)
    log(f'Server restarted: PID {old_pid} -> {new_pid}, API answering after {time.time() - started:.1f} s.')
    old_attention = service_pid(ATTENTION)
    subprocess.run(['/bin/launchctl', 'kickstart', '-k', ATTENTION], check=True)
    for _ in range(40):
        if service_pid(ATTENTION) not in (None, old_attention):
            break
        time.sleep(.25)
    log(f'Attention watcher restarted: PID {old_attention} -> {service_pid(ATTENTION)}.')

    runs = {r['id']: r for r in api('/api/runs')['runs']}
    for before in hosted:
        after = runs.get(before['id']) or json.loads((RUNS / before['id'] / 'run.json').read_text())
        owned = before['id'] in [r['id'] for r in active_runs()] or after['status'] == 'completed'
        log(f"Hosted run {before['id']}: before {before['status']}/{before.get('phase')}, after {after['status']}/{after.get('phase')}, "
            f"provider pid {before.get('process_pid')} alive {alive(before['process_pid'])}, "
            f"{'reattached' if owned else 'NOT reattached: ' + str(after.get('error'))}")
    if not hosted:
        log('No hosted run was active at the restart.')


if __name__ == '__main__':
    main()
