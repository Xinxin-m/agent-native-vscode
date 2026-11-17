"""Review model-proposed moves. Rejected chat/destination pairs stay rejected."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

DATA = Path(__file__).resolve().parent / 'data'
_LOCK = threading.RLock()


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def save(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.stem + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=1)
            out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(data, name='project-suggestions.lock', blocking=True):
    with (Path(data) / name).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def identity(key, destination):
    return hashlib.sha256(json.dumps([key, destination], separators=(',', ':')).encode()).hexdigest()


def valid(key, row, index, projects, rejected):
    destination = row.get('to') if isinstance(row, dict) else None
    return (key in index and destination and identity(key, destination) not in rejected
            and key not in (projects.get('manual_assignments') or [])
            and (projects.get('assignments') or {}).get(key) == row.get('from')
            and destination != row.get('from')
            and any(p.get('id') == destination and not p.get('automation')
                    for p in projects.get('projects', [])))


def merge(path, index, projects, proposed):
    """Called by enrichment under refresh.lock; reread rejection receipts on flush."""
    path = Path(path)
    with _LOCK, locked(path.parent):
        rejected = load(path.parent / 'project_suggestion_rejections.json', {})
        pending = load(path, {})
        pending.update(proposed)
        pending = {key: row for key, row in pending.items() if valid(key, row, index, projects, rejected)}
        save(path, pending)


def listing(data=DATA):
    data = Path(data)
    pending = load(data / 'project_suggestions.json', {})
    projects = load(data / 'projects.json', {})
    index = load(data / 'index.json', {})
    rejected = load(data / 'project_suggestion_rejections.json', {})
    names = {p['id']: p.get('name', p['id']) for p in projects.get('projects', [])}
    titles = load(data / 'chat_flags.json', {}).get('titles') or {}
    rows = [dict(row, key=key, title=titles.get(key) or row.get('title') or key,
                 from_name=names.get(row.get('from'), 'Unassigned'), to_name=names.get(row.get('to'), row.get('to_name')))
            for key, row in pending.items() if valid(key, row, index, projects, rejected)]
    return {'suggestions': sorted(rows, key=lambda row: row.get('at', ''), reverse=True)}


def decide(body, data=DATA):
    from projects import CatalogError, _save
    from workspace_groups import mark_manual, preserve_project
    data = Path(data)
    if not isinstance(body, dict) or body.get('action') not in ('accept', 'reject'):
        raise CatalogError('Choose Accept or Reject.')
    key, destination = body.get('key'), body.get('to')
    if not isinstance(key, str) or not isinstance(destination, str):
        raise CatalogError('A chat and destination are required.')
    try:
        # Enrichment holds this lock across its read/model/write sequence. Refuse
        # rather than waiting on a model call or overwriting its fresh state.
        with _LOCK, locked(data, 'refresh.lock', blocking=False), locked(data):
            pending = load(data / 'project_suggestions.json', {})
            row = pending.get(key)
            if not isinstance(row, dict) or row.get('to') != destination:
                raise CatalogError('This suggestion changed. Refresh the list.')
            projects = load(data / 'projects.json', {})
            rejected = load(data / 'project_suggestion_rejections.json', {})
            if body['action'] == 'accept':
                if not valid(key, row, load(data / 'index.json', {}), projects, rejected):
                    raise CatalogError('The chat or project changed. Refresh the list.')
                original = json.loads(json.dumps(projects))
                projects.setdefault('assignments', {})[key] = destination
                mark_manual(projects, [key]); preserve_project(projects, destination)
                _save(data, original, projects)
            else:
                rejected[identity(key, destination)] = dict(row, key=key, rejected_at=dt.datetime.now(dt.timezone.utc).isoformat())
                save(data / 'project_suggestion_rejections.json', rejected)
            pending.pop(key)
            save(data / 'project_suggestions.json', pending)
    except BlockingIOError:
        raise CatalogError('Labelling is running. Try this decision when it finishes.')
    return listing(data)


def route(req, query, body):
    import projects
    server = sys.modules.get(req.__class__.__module__)
    data = getattr(server, 'DATA', DATA)
    try:
        if body is None:
            return listing(data)
        with getattr(server, 'LOCK', _LOCK):
            return decide(body, data)
    except projects.CatalogError as error:
        return {'ok': False, 'error': str(error)}, 409


ROUTES = {('GET', '/api/project_suggestions'): route, ('POST', '/api/project_suggestions'): route}
BACKGROUND = []
