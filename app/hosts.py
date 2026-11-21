#!/usr/bin/env python3
"""Read-only host registry and explicit, bounded authenticated SSH probes.

Optional data/hosts.json shape (operator-owned, never written by this module):
{"hosts":[{"id":"second-mac","label":"Second Mac","transport":"ssh",
 "configured":true,"execution_alias":"other-mac","read_alias":"other-mac",
 "connector_path":"~/.local/lib/chat-library/host_connector.py",
 "expected_machine_id":"hash returned by a verified first probe"}]}

Second-Mac setup: enable Tailscale on both Macs manually, confirm the second
Mac's device name in the private tailnet, enable its macOS Remote Login for the
user, create/test an SSH alias using a dedicated key and verify its host key,
copy host_connector.py there, create its connector.json with host_id second-mac
and host-local account directories, add the record above, then explicitly
POST /api/hosts/probe {"id":"second-mac"}. Never sync credential files.
A cluster is a remote_sources entry in data/config.json, for example
{"name":"cluster","label":"Cluster","kind":"slurm","host":"cluster-dtn",
 "live_host":"cluster-compute","node_pattern":"node-\\d+","login_aliases":["cluster"]}:
host is its data-transfer alias, live_host an allocated compute node whose
hostname must match node_pattern, and login_aliases never execute. No scheduler
polling, allocation creation, SSH key enrollment, network changes or background probes.
"""
import json
import os
import re
import subprocess
import threading
import time
from automation import automation_kind
import json_cache

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
_LOCK = threading.Lock()
_PROBES = {}
_PROBING = set()
PROBE_FRESH_S = 120


def _read(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _index():
    """Shared parsed index, refreshed in the background; read-only."""
    return json_cache.latest(os.path.join(DATA, 'index.json'), {})


def _config():
    return _read(os.path.join(DATA, 'config.json'), {})


def _saved_hosts():
    return _read(os.path.join(DATA, 'hosts.json'), {}).get('hosts') or []


_WORKSPACES = [None, {}]  # (index object, {host: top folders}); the index copy is shared and unchanging


def _workspaces(index):
    if _WORKSPACES[0] is not index:
        counts = {}
        for meta in index.values():
            path = meta.get('cwd') or ''
            if meta.get('is_child') or automation_kind(meta) or not path.startswith('/') or path.startswith(('/tmp', '/private/tmp')):
                continue
            for host in str(meta.get('node') or 'mac').split('+'):
                host_counts = counts.setdefault(host, {})
                host_counts[path] = host_counts.get(path, 0) + 1
        _WORKSPACES[:] = [index, {host: sorted(c, key=lambda path: (-c[path], path))[:30] for host, c in counts.items()}]
    return _WORKSPACES[1]


def host_records():
    records = [{'id': 'mac', 'label': 'This Mac', 'transport': 'local', 'configured': True,
                'state': 'online', 'can_execute': True, 'stop_remote': False}]
    cfg = _config()
    for source in cfg.get('remote_sources') or []:
        name = source.get('name')
        if not name or name == 'mac':
            continue
        records.append({'id': name, 'label': source.get('label') or name,
                        'transport': 'ssh', 'configured': bool(source.get('live_host')),
                        'read_alias': source.get('read_alias') or source.get('host'),
                        'execution_alias': source.get('live_host'),
                        'connector_path': '~/.local/lib/chat-library/host_connector.py',
                        'kind': source.get('kind') or 'ssh', 'node_pattern': source.get('node_pattern') or '',
                        'login_aliases': source.get('login_aliases') or []})
    records.append({'id': 'second-mac', 'label': 'Second Mac', 'transport': 'ssh', 'configured': False,
                    'execution_alias': '', 'read_alias': '', 'connector_path': '~/.local/lib/chat-library/host_connector.py'})
    byid = {r['id']: r for r in records}
    for saved in _saved_hosts():
        if isinstance(saved, dict) and saved.get('id') and saved['id'] != 'mac':
            byid[saved['id']] = dict(byid.get(saved['id'], {}), **saved)
    now = time.time()
    for record in byid.values():
        if record.get('transport') == 'local':
            continue
        with _LOCK:
            observation = dict(_PROBES.get(record['id']) or {})
        record.update(state='unconfigured' if not record.get('configured') else 'unknown', can_execute=False, stop_remote=False)
        if observation:
            record.update(observation)
            if now - observation.get('observed_at', 0) > PROBE_FRESH_S:
                record.update(state='stale', can_execute=False)
        if not record.get('configured'):
            record.update(state='unconfigured', can_execute=False)
    folders_by_host = _workspaces(_index())
    for record in byid.values():
        folders = folders_by_host.get(record['id'], [])
        record['workspaces'] = folders
        record['default_cwd'] = record.get('default_cwd') or (folders[0] if folders else os.path.expanduser('~') if record['id']=='mac' else '')
    return list(byid.values())


def get_host(host_id):
    if not isinstance(host_id, str):
        raise ValueError('Choose an execution host')
    host = next((h for h in host_records() if h.get('id') == host_id), None)
    if not host:
        raise ValueError('Unknown execution host')
    return host


def probe_host(host_id):
    """One explicit probe, no retries or scheduling and no state/config writes."""
    host = get_host(host_id)
    if host.get('transport') == 'local':
        return dict(host, observed_at=time.time())
    if not host.get('configured'):
        raise ValueError('Configure the SSH host and install its connector first')
    import host_connector
    started = time.time()
    with _LOCK:
        if host_id in _PROBING:
            raise ValueError('A probe for this host is already running')
        _PROBING.add(host_id)
    try:
        argv = host_connector.ssh_command(host, 'inventory')
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError('SSH connector unavailable; check its installation, SSH alias and host key')
        if len(result.stdout) > 2_000_000:
            raise ValueError('Connector inventory exceeds the response limit')
        remote = json.loads(result.stdout)
        if remote.get('protocol') != host_connector.PROTOCOL or remote.get('host_id') != host_id or not remote.get('machine_id'):
            raise ValueError('Connector identity or protocol does not match this host')
        if host.get('expected_machine_id') and remote['machine_id'] != host['expected_machine_id']:
            raise ValueError('Connector machine identity changed')
        pattern = host.get('node_pattern')
        if host.get('kind') == 'slurm' and (not remote.get('job_id') or pattern and not re.fullmatch(pattern, str(remote.get('hostname') or ''))):
            raise ValueError('Cluster connector is not inside an allocated compute node')
        observed_at = time.time()
        sessions = []
        for row in remote.get('sessions') or []:
            row = dict(row, node=host_id, observed_at=observed_at)
            if 'stop' not in remote.get('capabilities', []) or not row.get('identity'):
                row.update(stop_allowed=False, stop_reason=row.get('stop_reason') or "This host's connector cannot stop sessions; stop it on the host.")
            sessions.append(row)
        state = dict(state='online', can_execute='run' in remote.get('capabilities', []), observed_at=observed_at,
                     machine_id=remote['machine_id'], hostname=remote.get('hostname'), job_id=remote.get('job_id'),
                     accounts=[{k: a.get(k) for k in ('id', 'tool', 'label', 'email', 'plan', 'logged_in', 'eligible', 'disabled', 'models', 'usage')} for a in remote.get('accounts', [])],
                     active_accounts=remote.get('active_accounts') or {}, commands=remote.get('commands') or {},
                     capabilities=remote.get('capabilities') or [], sessions=sessions, error='', last_ok=observed_at)
    except (ValueError, OSError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        with _LOCK:
            previous = dict(_PROBES.get(host_id) or {})
        state = {k: previous[k] for k in ('sessions', 'accounts', 'last_ok', 'machine_id', 'hostname', 'job_id') if k in previous}
        state.update(state='offline' if isinstance(exc, (OSError, subprocess.TimeoutExpired)) else 'error',
                     can_execute=False, observed_at=time.time(), error=str(exc)[:240])
    finally:
        with _LOCK:
            _PROBING.discard(host_id)
    state['seconds'] = round(time.time() - started, 2)
    with _LOCK:
        _PROBES[host_id] = state
    return dict(host, **state)


def session_control(host_id, operation, session, policy=None):
    if operation not in ('stop', 'stop-status'):
        raise ValueError('Unsupported session control')
    import host_connector
    host = get_host(host_id)
    request = dict(session.get('execution_identity') or {}, session=session)
    if policy is not None:
        request['policy'] = policy
    argv = host_connector.ssh_command(host, operation, request)
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError((result.stderr.strip() or 'Remote control failed')[-400:])
    if len(result.stdout) > 32768:
        raise ValueError('Remote control response exceeds limit')
    response = json.loads(result.stdout)
    if response.get('state') not in ('stop_requested', 'stopped'):
        raise ValueError('Remote control response is invalid')
    return response


def get_hosts(req, query, body):
    return {'hosts': host_records(), 'probe_fresh_s': PROBE_FRESH_S,
            'setup': {'second-mac': ['Enable Tailscale on both Macs and verify the second device in your private tailnet.',
                       'Enable Remote Login on the second Mac; create and test a dedicated SSH alias and verify its host key.',
                       'Install host_connector.py and its private connector.json on the second Mac; credentials remain there.',
                       'Add the second-mac record to data/hosts.json, then explicitly probe the host.']}}


def post_probe(req, query, body):
    origin = req.headers.get('Origin', '') if req is not None else ''
    host = req.headers.get('Host', '') if req is not None else ''
    if origin and origin not in ('http://' + host, 'https://' + host):
        return {'error': 'Cross-origin host probe rejected'}, 403
    try:
        return {'host': probe_host((body or {}).get('id'))}
    except ValueError as exc:
        return {'error': str(exc)}, 400


ROUTES = {('GET', '/api/hosts'): get_hosts, ('POST', '/api/hosts/probe'): post_probe}
BACKGROUND = []
