"""Explicit native session lineage and transcript drill-in metadata.

The tree is served from a snapshot of index/enrich/flags/projects that is
rebuilt in the background when one of those files changes, so opening a tree
never waits on parsing the 20+ MB index. Resume availability globs native
session stores (thousands of directory reads), which under a busy server's GIL
took over a minute per tree; it is checked off the request path and cached.
"""
import json
import os
import threading

import agent_activity
from indexer import LINEAGE_FIELDS, resolve_lineage

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
INPUTS = (('index', {}), ('enrich', {}), ('chat_flags', {}), ('projects', {}))

_FILES = {}  # name -> (stamp, parsed); read only by this module
_SNAP = {}  # 'value': latest snapshot
_BUILD = threading.Lock()
_RESUME = {}  # (key, transcript signature, accounts stamp) -> availability
_RESUME_QUEUE = []
_RESUME_LOCK = threading.Lock()


def _stamp(name):
    try:
        st = os.stat(os.path.join(DATA, name + '.json'))
        return (DATA, st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return (DATA, None)


def _load(name, default):
    stamp = _stamp(name)
    hit = _FILES.get(name)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(os.path.join(DATA, name + '.json')) as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return default
    _FILES[name] = (stamp, value)
    return value


def effective_assignments(index, assignments):
    """Return inherited placement without modifying curated project storage."""
    result = dict(assignments)
    for key, meta in index.items():
        if not meta.get('is_child'):
            continue
        root = meta.get('root_key')
        if root in assignments:
            result[key] = assignments[root]
        else:
            result.pop(key, None)
    return result


def resume_availability(meta):
    """Verify a resumable local native identity without launching a provider."""
    host=meta.get('execution_host') or meta.get('host_id') or meta.get('node') or 'mac'
    if host not in ('mac','local'):
        return {'native_resumable':False,'resume_reason':'Native resume ownership must be verified on its execution host'}
    try:
        import host_connector
        selected=host_connector.resolve_local_account(meta['source'],account_id=meta.get('account_id'),model=meta.get('model_main'),session_id=meta['id'],cwd=os.path.realpath(meta.get('cwd') or ''))
        return {'native_resumable':True,'resume_account_id':selected['account_id']}
    except (ValueError,OSError,KeyError):
        return {'native_resumable':False,'resume_reason':'This transcript has no verified resumable native session; start a linked session from a response instead.'}


def _stamps():
    return tuple(_stamp(name) for name, _ in INPUTS)


def _rebuild(force=False):
    with _BUILD:
        stamps = _stamps()
        snap = _SNAP.get('value')
        if snap and snap['stamps'] == stamps and not force:
            return snap
        index, enrich, flags, projects = (_load(name, default) for name, default in INPUTS)
        # resolve_lineage edits the parsed index in place; this module owns that object.
        index = resolve_lineage(index)
        by_root = {}
        for key, meta in index.items():
            by_root.setdefault(meta.get('root_key') or key, []).append(key)
        snap = {'stamps': stamps, 'index': index, 'by_root': by_root, 'enrich': enrich,
                'titles': flags.get('titles') or {}, 'assignments': projects.get('assignments', {})}
        _SNAP['value'] = snap
        return snap


def warm():
    if _SNAP.get('value') is None or _SNAP['value']['stamps'] != _stamps():
        _rebuild()


def _snapshot(key):
    """Latest snapshot. A stale one is served while a rebuild runs, unless it lacks the key."""
    snap = _SNAP.get('value')
    if snap is None or snap['stamps'][0][0] != DATA or (key not in snap['index'] and key not in snap['by_root']):
        return _rebuild(force=True)
    if snap['stamps'] != _stamps() and not _BUILD.locked():
        threading.Thread(target=_rebuild, daemon=True).start()
    return snap


def _resume_key(key, meta):
    return (key, json.dumps(meta.get('src_sig') or meta.get('ts_last')), _stamp('accounts'))


def _resume_worker():
    while True:
        with _RESUME_LOCK:
            if not _RESUME_QUEUE:
                return
            cache_key, meta = _RESUME_QUEUE[0]
        _RESUME[cache_key] = resume_availability(meta)
        with _RESUME_LOCK:
            _RESUME_QUEUE.pop(0)


def _cached_resume(key, meta):
    """Availability if already checked; otherwise queue the check and return {}."""
    cache_key = _resume_key(key, meta)
    if cache_key in _RESUME:
        return _RESUME[cache_key]
    with _RESUME_LOCK:
        if any(item[0] == cache_key for item in _RESUME_QUEUE):
            return {}
        _RESUME_QUEUE.append((cache_key, meta))
        if len(_RESUME_QUEUE) == 1:
            threading.Thread(target=_resume_worker, daemon=True).start()
    return {}


STATES = ('working', 'waiting', 'idle', 'finished', 'stopped', 'failed', 'unknown')


def node_state(node):
    """One word per node, from the sidebar's live_status first, so the tree and the sidebar row
    agree. A sub-agent has no process: its transcript evidence (activity) decides when it is not live."""
    live = node.get('live_status')
    if live in ('waiting', 'working', 'idle'):
        return live
    activity = node.get('activity')
    if activity == 'running':
        return 'working'
    if activity in ('finished', 'stopped', 'failed', 'unknown'):
        return activity
    return 'failed' if node.get('status') in ('failed', 'error') else 'finished'


def session_tree(req, query, body):
    key = (query or {}).get('key') or ''
    snap = _snapshot(key)
    index = snap['index']
    if key not in index and key not in snap['by_root']:
        return {'error': 'Session not found', 'key': key, 'nodes': [], 'edges': []}
    root = index.get(key, {}).get('root_key') or key
    enrich, titles = snap['enrich'], snap['titles']  # a manual rename wins
    project_id = snap['assignments'].get(root)
    selected = {k: index[k] for k in snap['by_root'].get(root, ())}
    # Include ancestors when a partial transcript import lacks a root record.
    if key in index:
        selected.setdefault(key, index[key])
    nodes, edges, missing = [], [], set()
    live = agent_activity._live()
    import sidebar
    statuses, failed = sidebar.current_statuses(), sidebar.failed_runs(DATA)
    seen, views = sidebar.load_seen(DATA, create=False), _load('views', {})
    fields = ('id', 'source', 'title_seed', 'model_main', 'models', 'ts_first',
              'ts_last', 'status', 'node', 'execution_host', 'host_id', 'cwd', 'account_id', 'usage') + LINEAGE_FIELDS
    from enrich import conversation_title
    for node_key, meta in selected.items():
        node = {f: meta[f] for f in fields if f in meta}
        node.update(key=node_key, root_key=root, project_id=project_id,
                    title=conversation_title(node_key, {**meta, 'title_seed': meta.get('agent_label') or meta.get('title_seed')},
                                             index, enrich, titles))
        # Absent until checked in the background; the broker re-verifies on send.
        node.update(_cached_resume(node_key, meta))
        if meta.get('is_child'):
            # Subagents are not broker runs or processes; their transcripts say whether they run.
            try:
                node.update(agent_activity.child_activity(meta, index, live=live))
            except Exception as exc:  # evidence is advisory; never fail the tree
                node.update(activity='unknown', activity_evidence='Activity check failed: ' + type(exc).__name__)
            # The same mark the sidebar uses: a running child is working, a settled one has none.
            node['live_status'] = 'working' if node.get('activity') == 'running' else None
        node['unseen'] = sidebar.is_unseen(dict(meta, key=node_key), seen, views)  # the sidebar row's blue dot
        if not meta.get('is_child'):  # the sidebar row's one mark: a standing failure clears working
            node['live_status'], failed_now, _ = sidebar.resolve(statuses.get(node_key), failed.get(node_key), meta.get('ts_last'), node['unseen'])
            if failed_now:
                node['live_status'], node['activity'] = None, 'failed'
        node['state'] = node_state(node)
        nodes.append(node)
        parent = meta.get('parent_key')
        if parent and parent != node_key:
            edges.append({'parent_key': parent, 'child_key': node_key})
            if parent not in selected:
                missing.add(parent)
    for parent in sorted(missing):
        nodes.append({'key': parent, 'root_key': root, 'missing': True,
                      'title': 'Transcript unavailable', 'project_id': project_id})
    nodes.sort(key=lambda n: (n.get('ts_first') or '', n['key']))
    counts = dict.fromkeys(STATES, 0)
    for node in nodes:
        if node.get('state'):
            counts[node['state']] += 1
    return {'key': key, 'root_key': root, 'nodes': nodes, 'edges': edges, 'counts': counts,
            'missing_parent_keys': sorted(missing)}


ROUTES = {('GET', '/api/session_tree'): session_tree}
BACKGROUND = [(warm, 2)]
