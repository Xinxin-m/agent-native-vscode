"""Queue a signed Library Code release and install it when Library agents stop."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import install

sys.path.insert(0, str(install.ROOT.parent / 'chat-dashboard'))

LABEL = 'local.library.code.idle-install'
STATE = install.ROOT / 'output' / 'idle-install'
JOURNALS = install.ROOT.parent / 'chat-dashboard/data/execution/runs'
TERMINAL = {'completed', 'failed', 'interrupted'}


class Busy(install.GuardError):
    pass


def api(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8744/api/' + path, timeout=12) as response:
        return json.load(response)


def check_runs(rows):
    if not isinstance(rows, list) or any(row.get('status') not in TERMINAL for row in rows):
        raise Busy('Library has active or unverified runs.')
    for row in rows:
        if row.get('recovery_blocked'):
            if row.get('host_id', 'mac') in ('mac', 'local') and row.get('process_transport') != 'ssh':
                from broker import observe_process
                if observe_process(row)['state'] in ('exited', 'reused'):
                    continue
            raise Busy('Waiting for a previously interrupted provider process to exit.')


def session_idle(row):
    if row.get('alive') is False or row.get('live_status') == 'idle':
        return True
    # Desktop Codex can retain an app-server after every chat has finished.
    # Inspect the rollouts actually held by that process, never the most
    # recent chat in its cwd. Standalone processes of unknown purpose defer.
    pid = row.get('pid')
    if not (row.get('tool') == 'codex' and row.get('node') == 'mac' and
            row.get('process_only') and isinstance(pid, int) and pid > 1 and
            row.get('activity_note', '').startswith('Shared app service;')):
        return False
    files = subprocess.run(['/usr/sbin/lsof', '-nP', '-p', str(pid), '-Fnc'],
                           timeout=8, text=True, capture_output=True)
    records = files.stdout.splitlines()
    if files.returncode or 'ccodex' not in records:
        return False
    from sessions_cli import transcript_state
    rollouts = [Path(line[1:]) for line in records if line.startswith('n/') and
                '/sessions/' in line and '/rollout-' in line and line.endswith('.jsonl')]
    return bool(rollouts) and all(path.is_file() and transcript_state('codex', str(path))['state'] in
               {'complete', 'aborted', 'error'} for path in rollouts)


def require_idle():
    check_runs(api('runs').get('runs'))
    live = api('live')
    age = time.time() - (live.get('fetched_at') or 0)
    if not 0 <= age <= 120 or not any(node.get('name') == 'mac' and node.get('ok') is True
                                      for node in live.get('nodes', [])):
        raise Busy('Waiting for a fresh Mac process inventory.')
    sessions = live.get('sessions')
    if not isinstance(sessions, list) or any(not session_idle(row) for row in sessions):
        raise Busy('Waiting for active, waiting, or unattributed agents to stop.')
    # /api/runs on older backends is limited to the newest 100 records.
    if not JOURNALS.is_dir():
        raise Busy('Run inventory unavailable.')
    for folder in JOURNALS.iterdir():
        if folder.is_dir():
            check_runs([json.loads((folder / 'run.json').read_bytes())])


def atomic_write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


def save(path, value):
    atomic_write(path, json.dumps(value, indent=2).encode() + b'\n')


def enable():
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    options = {'Label': LABEL, 'ProgramArguments': [sys.executable, str(Path(__file__).resolve()), 'poll'],
               'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Background',
               'StandardErrorPath': str(STATE / 'errors.log')}
    data = plistlib.dumps(options)
    if plist.exists() and plist.read_bytes() != data:
        raise SystemExit('Existing LaunchAgent has different settings: ' + str(plist))
    if not plist.exists():
        atomic_write(plist, data)
    domain = f'gui/{os.getuid()}'
    if subprocess.run(['launchctl', 'print', domain + '/' + LABEL], capture_output=True).returncode:
        subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)


def backend_hash():
    digest = hashlib.sha256()
    for source in sorted((install.ROOT.parent / 'chat-dashboard').glob('*.py')):
        digest.update(source.name.encode() + b'\0' + source.read_bytes() + b'\0')
    return digest.hexdigest()


def backend_pid(service):
    result = subprocess.run(['/bin/launchctl', 'print', service], check=True,
                            text=True, capture_output=True)
    match = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.MULTILINE)
    if not match:
        raise Busy('Waiting for the Library backend.')
    return int(match[1])


def refresh_backend(job, path):
    if backend_hash() != job['backend']:
        raise install.GuardError('Backend source changed after queueing. Review the release again.')
    service = f'gui/{os.getuid()}/com.chatdashboard.server'
    pid = backend_pid(service)
    if 'refresh_requested_pid' not in job:
        require_idle()
        job['refresh_requested_pid'] = pid
        save(path, job)
        subprocess.run(['/bin/launchctl', 'kill', 'SIGTERM', service], check=True)
    # KeepAlive starts the replacement. A crash here resumes verification on
    # the next tick without sending another termination signal.
    if backend_pid(service) == job['refresh_requested_pid']:
        raise Busy('Waiting for the updated Library backend to start.')
    check_runs(api('runs').get('runs'))
    job['backend_ready'] = True
    save(path, job)


def poll(job, path):
    try:
        require_idle()
    except (Busy, OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        reason = str(exc)
        if job.get('waiting') != reason:
            save(path, dict(job, waiting=reason))
        return
    if install.bundle_hash(install.APP) != job['previous']:
        raise install.GuardError('Daily bundle changed since this release was queued.')
    if job.get('backend') and not job.get('backend_ready'):
        refresh_backend(job, path)
        require_idle()
    result = install.install(Path(job['candidate']), job['sha256'], job['extension'],
                             restart=True, before_replace=require_idle, expected_previous=job['previous'])
    save(STATE / 'last-install.json', dict(job, result=result))
    path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    commands.add_parser('enable')
    commands.add_parser('poll')
    queue = commands.add_parser('queue')
    queue.add_argument('candidate', type=Path)
    queue.add_argument('sha256')
    queue.add_argument('extension')
    queue.add_argument('--refresh-backend', action='store_true')
    args = parser.parse_args()
    STATE.mkdir(exist_ok=True, parents=True, mode=0o700)
    if args.mode == 'enable':
        enable()
        return
    path = STATE / 'pending.json'
    with (STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.mode == 'queue':
            candidate = args.candidate.resolve(strict=True)
            install.validate_candidate(candidate, args.sha256, args.extension)
            previous = install.bundle_hash(install.APP)
            if args.sha256 == previous:
                raise SystemExit('Release already installed.')
            job = {'candidate': str(candidate), 'sha256': args.sha256,
                   'extension': args.extension, 'previous': previous}
            if args.refresh_backend:
                job['backend'] = backend_hash()
            save(path, job)
        elif path.exists():
            job = json.loads(path.read_text())
            if job.get('error'):
                return
            try:
                poll(job, path)
            except Busy as exc:
                save(path, dict(job, waiting=str(exc)))
            except install.GuardError as exc:
                # These guards run before closing the client. Preserve the
                # queued release until the open client can be replaced safely.
                reason = str(exc)
                if reason.startswith(('Dirty native editor', 'Client owns external processes', 'New agent activity')):
                    save(path, dict(job, waiting=reason))
                elif reason in (
                    'The daily client must use the verified 19342 debugging endpoint for preservation checks',
                    'Cannot verify the daily client debugging endpoint',
                    'Debugging endpoint does not belong to the selected client process tree',
                ):
                    save(path, dict(job, waiting='Waiting for Library Code to quit so its workspace can be preserved.'))
                else:
                    save(path, dict(job, error=str(exc)))
                    raise


if __name__ == '__main__':
    main()
