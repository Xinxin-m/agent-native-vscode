"""Sidebar state every Library client shares: when she last saw a chat (so the blue dot
agrees everywhere), one live status per chat, the combined refresh and the choices for
a project's workspace. No model calls. Contract: library-code-oss/docs/redesign-2026-11-22/API.md.
"""
import datetime as dt
import glob
import json
import os
import re
import sys
import threading
import time

import json_cache

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
_LOCK = threading.RLock()
KEY = re.compile(r'^(claude|codex)-[A-Za-z0-9_.-]{1,200}$')
LISTS = ('recent', 'starred', 'archive', 'unclassified')
RUN_DONE = {'completed', 'failed', 'interrupted'}


def _iso(now=None):
    moment = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (moment.microsecond // 1000)


def _parse(value):
    try:
        moment = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return 0.0
    return (moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)).timestamp()


def _write(path, value):
    tmp = path + '.tmp%d' % os.getpid()
    with open(tmp, 'w') as handle:
        json.dump(value, handle, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ seen time
# Cause of the false blue dots (reproduced 2026-11-22 on read-only live data): only 91 of
# 5,939 chats with a result had a server view time, the old Library counted every other
# chat unread, and the workbench kept its own baseline in each browser's localStorage, so
# clients disagreed and a reply that arrived while a chat was open stayed blue. The time
# is now stored here, per chat, and shared.
def load_seen(data=DATA, now=None, create=True):
    """{'baseline': iso, 'seen': {key: iso}}. The first load after deploy creates the file
    with baseline = now, so every chat that already has a result counts as seen."""
    path = os.path.join(data, 'seen.json')
    with _LOCK:
        try:
            with open(path) as handle:
                value = json.load(handle)
            if isinstance(value, dict) and isinstance(value.get('seen'), dict) and value.get('baseline'):
                return value
            raise ValueError('seen.json has an unexpected shape')
        except FileNotFoundError:
            value = {'baseline': _iso(now), 'seen': {}}
            if create:
                _write(path, value)
            return value
        except (OSError, ValueError):
            # Unreadable: keep the file for inspection and start clean in memory only.
            return {'baseline': _iso(now), 'seen': {}}


def seen_time(key, seen, views=None):
    """Latest of this chat's stored seen time, the legacy view time and the baseline, in seconds."""
    return max(_parse(seen['seen'].get(key)), _parse(((views or {}).get(key) or {}).get('last')), _parse(seen['baseline']))


def is_unseen(meta, seen, views=None):
    result = _parse(meta.get('last_assistant_ts'))
    return bool(result) and result > seen_time(meta['key'], seen, views)


def mark_seen(keys, visible, focused, data=DATA, now=None):
    """Only a visible chat in a focused window counts; a background window never marks seen."""
    if visible is not True or focused is not True:
        return {'ok': True, 'marked': 0, 'reason': 'window not visible and focused'}
    index = json_cache.latest(os.path.join(data, 'index.json'), {})
    keys = [k for k in dict.fromkeys(keys) if isinstance(k, str) and KEY.match(k) and k in index][:100]
    with _LOCK:
        seen = load_seen(data, now)
        stamp = _iso(now)
        for key in keys:
            seen['seen'][key] = max(stamp, seen['seen'].get(key, ''))
        if keys:
            _write(os.path.join(data, 'seen.json'), seen)
    return {'ok': True, 'marked': len(keys), 'seen_at': stamp if keys else None}


# ------------------------------------------------------------------ live status
_RUNS = {}  # run.json path -> ((mtime_ns, size), (status, keys, blocked, created_at)); only changed journals are parsed


def _run_facts(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _RUNS.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(path) as handle:
            run = json.load(handle)
        facts = (str(run.get('status') or ''), (run.get('key'), run.get('conversation_key')), bool(run.get('recovery_blocked')),
                 str(run.get('created_at') or ''))
    except (OSError, ValueError, AttributeError):
        facts = None
    _RUNS[path] = (stamp, facts)
    return facts


def _active_runs(data):
    """{chat key: 'working' | 'waiting'} from the broker's run journals."""
    out = {}
    for path in glob.glob(os.path.join(data, 'execution', 'runs', '*', 'run.json')):
        facts = _run_facts(path)
        if not facts or not facts[0] or facts[0] in RUN_DONE or facts[2]:
            continue
        state = 'waiting' if facts[0].startswith(('waiting', 'awaiting')) else 'working'
        for key in facts[1]:
            if key and out.get(key) != 'waiting':
                out[key] = state
    return out


def failed_keys(data=DATA):
    """Chats whose latest broker run failed (the run the feed's failed-run notice points at)."""
    latest = {}
    for path in glob.glob(os.path.join(data, 'execution', 'runs', '*', 'run.json')):
        facts = _run_facts(path)
        if not facts or not facts[0]:
            continue
        for key in facts[1]:
            if key and (key not in latest or facts[3] >= latest[key][0]):
                latest[key] = (facts[3], facts[0])
    return {key for key, (_, status) in latest.items() if status == 'failed'}


def _attention_keys():
    """Chats with a pending approval or question, from the running broker only."""
    module = sys.modules.get('broker')
    broker = getattr(module, '_BROKER', None) if module else None
    if broker is None:
        return set()
    try:
        return {item.get('key') for item in broker.attention().get('items', []) if item.get('key')}
    except Exception:
        return set()


RANK = {'waiting': 3, 'working': 2, 'idle': 1}


def live_statuses(sessions, runs=None, waiting_keys=(), now=None):
    """{chat key: waiting | working | idle}. Sessions come from the verified process
    inventory (fresh rows only, see live.session_status); runs and waiting_keys from the
    broker. Transcript recency is never used."""
    import live
    now = time.time() if now is None else now
    out = {}

    def put(key, state):
        if key and state and RANK[state] > RANK.get(out.get(key), 0):
            out[key] = state
    for row in sessions:
        put(row.get('chat_key'), live.session_status(row, now))
    for key, state in (runs or {}).items():
        put(key, state)
    for key in waiting_keys:
        put(key, 'waiting')
    return out


def _live_sessions():
    module = sys.modules.get('live')
    if module is None:
        return []
    try:
        return module.get_live(None, {}, None).get('sessions') or []
    except Exception:
        return []


def current_statuses(data=DATA, now=None):
    return live_statuses(_live_sessions(), _active_runs(data), _attention_keys(), now)


# ------------------------------------------------------------------ chat list fields
def annotate(chats, data=DATA, assignments=None, views=None, statuses=None, now=None):
    """Add seen_at, unseen, live_status, failed and project_id to each /api/state chat row."""
    seen = load_seen(data, now)
    if statuses is None:
        statuses = current_statuses(data, now)
    failed = failed_keys(data)
    assignments = assignments or {}
    for chat in chats:
        key = chat['key']
        chat['seen_at'] = _iso(seen_time(key, seen, views))
        chat['unseen'] = is_unseen(chat, seen, views)
        chat['live_status'] = statuses.get(key)
        chat['failed'] = key in failed and statuses.get(key) not in ('working', 'waiting')
        chat['project_id'] = assignments.get(key) or ''
    return chats


# ------------------------------------------------------------------ combined refresh
# One Refresh button: rescan for new chats (parse only, never a paid label) and refresh
# live status. Poll GET /api/refresh?id= until "done" is true.
_JOBS = {}


def _run_refresh(job, server, include_remote, history_wait=600):
    import live
    try:
        live.refresh(remote=include_remote)
        # A running session whose transcript exists but is not indexed yet is indexed now.
        import indexer, live_library, live_refresh
        live_refresh.publish(live_library.LiveLibrary(interval=0))
        job['live'].update(running=False, fetched_at=int(time.time()))
    except Exception as exc:
        job['live'].update(running=False, error='%s: %s' % (type(exc).__name__, exc))
    try:
        started = server.start_history_refresh()
        hid = started['refresh']['id']
        job['history']['id'] = hid
        deadline = time.time() + history_wait
        while time.time() < deadline:
            state = server.history_refresh_status(hid) or {}
            job['history'].update(phase=state.get('phase'), errors=state.get('errors') or [])
            if not state.get('running'):
                break
            time.sleep(0.5)
        job['history']['running'] = False
    except Exception as exc:
        job['history'].update(running=False, errors=['%s: %s' % (type(exc).__name__, exc)])


def _view(job):
    done = not job['live']['running'] and not job['history']['running']
    return dict(job, done=done, ok=done and not job['live'].get('error') and not job['history'].get('errors'))


def start_refresh(server, include_remote=False, sync=False):
    job = {'id': str(time.monotonic_ns()), 'history': {'running': True, 'errors': []}, 'live': {'running': True}}
    _JOBS[job['id']] = job
    for old in list(_JOBS)[:-8]:
        _JOBS.pop(old, None)
    thread = threading.Thread(target=_run_refresh, args=(job, server, include_remote), daemon=True)
    thread.start()
    if sync:
        thread.join()
    return _view(job)


def post_refresh(req, query, body):
    return start_refresh(_server(req), include_remote=(body or {}).get('include_remote') is True)


def get_refresh(req, query, body):
    job = _JOBS.get((query or {}).get('id') or '')
    return _view(job) if job else ({'ok': False, 'error': 'unknown refresh id'}, 404)


# ------------------------------------------------------------------ project workspace choices
def workspace_choices(project_id, projects, index, hosts, assignments):
    """Where a project's chats can start: every configured host's default folder, the
    folders its own chats ran in (most used first) and its saved workspace mappings."""
    out, seen = [], set()

    def add(host_id, path, source, label, n=0):
        if path and (host_id, path) not in seen:
            seen.add((host_id, path))
            out.append({'host_id': host_id, 'path': path, 'source': source, 'label': label, 'n_chats': n})
    project = next((p for p in projects.get('projects', []) if p.get('id') == project_id), None)
    if project is None:
        raise ValueError('Unknown project')
    saved = project.get('default_workspace') or {}
    if saved:
        add(saved.get('host_id'), saved.get('path'), 'saved', 'Saved workspace')
    counts = {}
    for key, pid in assignments.items():
        meta = index.get(key)
        if pid == project_id and meta and meta.get('cwd') and not meta.get('is_child'):
            pair = (str(meta.get('node') or 'mac').split('+')[0], meta['cwd'])
            counts[pair] = counts.get(pair, 0) + 1
    for (host_id, path), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        add(host_id, path, 'chat_folder', os.path.basename(path.rstrip('/')) or path, n)
    for mapping in project.get('workspace_mappings') or []:
        add(mapping.get('host'), mapping.get('path'), 'mapping', 'Mapped folder')
    for host in hosts:
        if host.get('state') in ('unconfigured',) or not host.get('default_cwd'):
            continue
        add(host['id'], host['default_cwd'], 'host', host.get('label') or host['id'])
    return {'ok': True, 'project_id': project_id, 'current': saved or None, 'choices': out}


def get_workspace_choices(req, query, body):
    import hosts
    data = _data(req)
    read = lambda name, default: json_cache.latest(os.path.join(data, name + '.json'), default)
    try:  # read fresh: the cached copy lags a save, and the menu must show the choice just saved
        with open(os.path.join(data, 'projects.json')) as fh:
            projects = json.load(fh)
    except (OSError, ValueError):
        projects = read('projects', {'projects': [], 'assignments': {}})
    from session_lineage import effective_assignments
    index = read('index', {})
    assignments = effective_assignments(index, projects.get('assignments') or {})
    try:
        return workspace_choices((query or {}).get('id') or '', projects, index, hosts.host_records(), assignments)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 404


# ------------------------------------------------------------------ why a chat went to a project
def get_placements(req, query, body):
    import autoclassify
    return {'ok': True, 'decisions': autoclassify.read_log(_data(req), (query or {}).get('key') or '', int((query or {}).get('limit') or 50))}


# ------------------------------------------------------------------ routes
def post_seen(req, query, body):
    body = body or {}
    keys = body.get('keys') if isinstance(body.get('keys'), list) else [body.get('key')]
    return mark_seen(keys, body.get('visible'), body.get('focused'), _data(req))


def _server(req):
    """The module that holds the core server functions: python3 server.py runs it as __main__,
    a test or QA harness imports it, and a handler subclass may live in a third module."""
    names = [req.__class__.__module__ if req is not None else '', 'server', '__main__']
    return next((m for m in (sys.modules.get(n) for n in names) if hasattr(m, 'start_history_refresh')), None)


def _data(req):
    return getattr(_server(req), 'DATA', DATA)


ROUTES = {('POST', '/api/seen'): post_seen, ('POST', '/api/refresh'): post_refresh, ('GET', '/api/refresh'): get_refresh,
          ('GET', '/api/project_workspace'): get_workspace_choices, ('GET', '/api/placements'): get_placements}
BACKGROUND = []
