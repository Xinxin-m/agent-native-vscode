"""User-owned project organization. No transcript writes and no model calls.

Metadata lives on canonical projects.json records: pinned, pin_order, group and
workspace_mappings. Merge previews are optimistic, revision-checked transactions.
"""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import uuid

from workspace_groups import mark_manual, preserve_project
from automation import AUTOMATION_PROJECT, automation_kind, effective_projects
import json_cache

DATA = Path(__file__).resolve().parent / 'data'
_LOCK = threading.RLock()
RESERVED = AUTOMATION_PROJECT['id']


class CatalogError(ValueError):
    pass


def _load(data, name, default):
    path = Path(data) / (name + '.json')
    if not path.exists():
        return copy.deepcopy(default)
    # A corrupt existing taxonomy must never silently become an empty one.
    return json.loads(path.read_text())


def _inputs(data):
    return (_load(data, 'projects', {'projects': [], 'assignments': {}}),
            _load(data, 'index', {}), _load(data, 'chat_flags', {}), _load(data, 'config', {}))


def _revision(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _is_bot(meta):
    return bool(meta.get('automation') or automation_kind(meta))


def _is_child(meta):
    return any(meta.get(field) for field in ('is_child', 'parent_key', 'parent_agent_id', 'parent_session_id'))


def _reserved(project):
    return project['id'] == RESERVED or bool(project.get('automation'))


def _text(value, label, maximum=160, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise CatalogError('Invalid ' + label)
    return value.strip()


def _mappings(value):
    if not isinstance(value, list) or len(value) > 100:
        raise CatalogError('Workspace mappings must be a list of at most 100 paths')
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise CatalogError('Each workspace mapping needs a host and absolute path')
        host = _text(item.get('host'), 'host', 128)
        path = _text(item.get('path'), 'workspace path', 4096)
        if not (path.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', path)):
            raise CatalogError('Workspace paths must be absolute')
        row = {'host': host, 'path': path}
        if row not in out:
            out.append(row)
    return out


MIN_PROJECT_CHATS = 5


def display_state(index, project_state, flags, config):
    """What clients see (2026-11-15): a project with fewer than 5 human chats folds
    into the misc project ("Other"); its chats stay visible there. Pinned, curated,
    archived and automation projects stay, and so does every manually placed chat.
    Counts are per provider so the All / Claude / Codex filter can show its own.
    Stored placements are untouched; no model calls."""
    misc_name = (config or {}).get('misc_project') or 'Miscellaneous & Ops'
    stored = project_state.get('assignments') or {}
    assignments = dict(stored)
    misc = next((p for p in project_state.get('projects', []) if p['name'] == misc_name), None)
    archived = set((flags or {}).get('archived', []))
    manual = set(project_state.get('manual_assignments') or [])
    members = {}
    for key, pid in stored.items():
        members.setdefault(pid, []).append(key)
    def human(key):
        meta = index.get(key)
        return meta is not None and key not in archived and not _is_bot(meta) and not _is_child(meta)
    dissolved = {}
    for p in project_state.get('projects', []) if misc else []:
        if p is misc or p.get('pinned') or p.get('user_curated') or p.get('archived') or _reserved(p):
            continue
        keys = members.get(p['id'], [])
        if sum(map(human, keys)) >= MIN_PROJECT_CHATS:
            continue
        moved = [k for k in keys if k not in manual]  # children move with their roots
        for key in moved:
            assignments[key] = misc['id']
        if moved:
            dissolved[p['id']] = len(moved)
    counts = {}
    for key, pid in assignments.items():
        if human(key):
            row = counts.setdefault(pid, {'all': 0, 'claude': 0, 'codex': 0})
            row['all'] += 1
            source = index[key].get('source')
            if source in row:
                row[source] += 1
    return dict(project_state, assignments=assignments, stored_assignments=stored, dissolved=dissolved,
                project_counts=counts, min_project_chats=MIN_PROJECT_CHATS)


def catalog(data=DATA, inputs=None):
    inputs = inputs or _inputs(data)
    raw, index, flags, config = inputs
    effective = effective_projects(index, raw)
    archived = set(flags.get('archived', []))
    buckets = {p['id']: [] for p in effective['projects']}
    for key, meta in index.items():
        cwd = meta.get('cwd', '')
        if any(cwd == e or cwd.startswith(e.rstrip('/') + '/') for e in config.get('excluded_cwds', [])):
            continue
        pid = effective['assignments'].get(key)
        if pid in buckets:
            buckets[pid].append((key, meta))
    duplicates = {}
    for p in effective['projects']:
        duplicates[p['name'].casefold()] = duplicates.get(p['name'].casefold(), 0) + 1
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for p in effective['projects']:
        chats = buckets[p['id']]
        human = [(k, m) for k, m in chats if k not in archived and not _is_bot(m) and not _is_child(m)]
        last = max((m.get('ts_last', '') for _, m in human), default='')
        try:
            timestamp = dt.datetime.fromisoformat(last.replace('Z', '+00:00'))
            if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
            stale = (now - timestamp).total_seconds() > 7 * 86400
        except (ValueError, TypeError):
            stale = True
        pinned = bool(p.get('pinned'))
        hidden = bool(p.get('archived') or _reserved(p) or (len(human) < 5 or (not pinned and stale)))
        mappings = p.get('workspace_mappings') or []
        paths = [dict(x, inferred=False) for x in mappings]
        seen = {(x['host'], x['path']) for x in paths}
        for _, meta in human:
            pair = (meta.get('node') or 'mac', meta.get('cwd') or '')
            if pair[1] and pair not in seen:
                paths.append({'host': pair[0], 'path': pair[1], 'inferred': True}); seen.add(pair)
        display = p['name']
        if duplicates[p['name'].casefold()] > 1:
            display += ' · ' + (paths[0]['host'] + ':' + paths[0]['path'] if paths else p['id'])
        rows.append({**p, 'display_name': display, 'group': p.get('group', ''), 'pinned': pinned,
                     'pin_order': p.get('pin_order', 999999), 'workspace_mappings': mappings,
                     'humanCount': len(human), 'lastTs': last, 'archived': hidden, 'paths': paths,
                     'humanCounts': {tool: sum(1 for _, m in human if m.get('source') == tool) for tool in ('claude', 'codex')}})
    return {'ok': True, 'revision': _revision(inputs), 'projects': rows,
            'groups': sorted({p['group'] for p in rows if p['group']}, key=str.casefold)}


def _save(data, original, updated):
    data = Path(data)
    snapshots = data / 'project-snapshots'
    snapshots.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    snapshot = snapshots / (stamp + '-' + uuid.uuid4().hex[:8] + '.json')
    # Preserve the exact original bytes, including fields unknown to this module.
    with snapshot.open('xb') as out:
        out.write((data / 'projects.json').read_bytes())
        out.flush(); os.fsync(out.fileno())
    fd, temp = tempfile.mkstemp(prefix='.projects-', suffix='.tmp', dir=data)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(updated, out, ensure_ascii=False, indent=1)
            out.flush(); os.fsync(out.fileno())
        os.replace(temp, data / 'projects.json')
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return snapshot.name


def mutate(body, data=DATA):
    if not isinstance(body, dict): raise CatalogError('Expected an action object')
    with _LOCK:
        inputs = _inputs(data)
        original, index, flags, config = inputs
        value = copy.deepcopy(original)
        by_id = {p['id']: p for p in value['projects']}
        action = body.get('action')
        def project(pid):
            if pid not in by_id: raise CatalogError('Unknown project')
            p = by_id[pid]
            if _reserved(p): raise CatalogError('Automation runs is a reserved destination')
            return p
        if action == 'dissolve':
            source = project(body.get('id'))
            assignments = value.setdefault('assignments', {})
            keys = sorted(k for k, pid in assignments.items() if pid == source['id'])
            # Only exact workspace matches backed by five other human roots count.
            support = {}
            for key, pid in assignments.items():
                meta = index.get(key, {})
                candidate = by_id.get(pid)
                if pid == source['id'] or not candidate or candidate.get('archived') or _reserved(candidate) or _is_bot(meta) or _is_child(meta):
                    continue
                cwd = meta.get('cwd')
                if cwd:
                    support.setdefault((meta.get('node') or 'mac', cwd, pid), set()).add(key)
            moves = {}
            for key in keys:
                meta = index.get(key, {})
                candidates = {pid for (node, cwd, pid), members in support.items() if len(members) >= 5 and cwd == meta.get('cwd') and node == (meta.get('node') or 'mac')}
                moves[key] = next(iter(candidates)) if len(candidates) == 1 and not _is_bot(meta) else None
            counts = {}
            for target in moves.values():
                label = by_id[target]['name'] if target else 'Unassigned'
                counts[label] = counts.get(label, 0) + 1
            preview = {'project': source['name'], 'chats': len(keys), 'destinations': counts}
            if body.get('preview') is True:
                return {'ok': True, 'revision': _revision(inputs), 'preview': preview}
            if body.get('preview_revision') != _revision(inputs):
                raise CatalogError('Project data changed. Preview the dissolve again before committing.')
            mark_manual(value, keys)
            for key, target in moves.items():
                if target: assignments[key] = target
                else: assignments.pop(key, None)
            for row in value['projects']:
                if row.get('parent_id') == source['id']: row.pop('parent_id', None)
            value['projects'] = [row for row in value['projects'] if row['id'] != source['id']]
        elif action == 'merge':
            sources = body.get('source_ids')
            if not isinstance(sources, list) or not sources or any(not isinstance(x, str) for x in sources) or len(set(sources)) != len(sources):
                raise CatalogError('Provide explicit, unique source project IDs')
            target = project(body.get('target_id'))
            if target['id'] in sources: raise CatalogError('The destination cannot also be a source')
            source_rows = [project(pid) for pid in sources]
            keys = [k for k, pid in value.get('assignments', {}).items() if pid in sources]
            bots = [k for k in keys if _is_bot(index.get(k, {}))]
            human = [k for k in keys if k not in bots]
            preview = {'source_ids': sources, 'target_id': target['id'], 'source_names': [p['name'] for p in source_rows],
                       'target_name': target['name'], 'human_chats': len(human),
                       'archived_human_chats': len(set(human) & set(flags.get('archived', []))),
                       'automation_runs': len(bots), 'projects_merged': len(sources)}
            if body.get('preview') is True:
                return {'ok': True, 'revision': _revision(inputs), 'preview': preview}
            if body.get('preview_revision') != _revision(inputs):
                raise CatalogError('Project data changed. Preview the merge again before committing.')
            mark_manual(value, keys)
            preserve_project(value, target['id'])
            for key in human: value['assignments'][key] = target['id']
            for key in bots: value['assignments'][key] = RESERVED
            if bots and RESERVED not in by_id: value['projects'].append(dict(AUTOMATION_PROJECT))
            mappings = target.get('workspace_mappings', []) + [m for p in source_rows for m in p.get('workspace_mappings', [])]
            target['workspace_mappings'] = _mappings(mappings)
            target['merged_from'] = sorted(set(target.get('merged_from', []) + sources + [s for p in source_rows for s in p.get('merged_from', [])]))
            value['projects'] = [p for p in value['projects'] if p['id'] not in sources]
        else:
            p = project(body.get('id'))
            if action in ('pin', 'unpin'):
                p['pinned'] = action == 'pin'
                ordered = sorted([x for x in value['projects'] if x.get('pinned') and x is not p], key=lambda x: x.get('pin_order', 999999))
                if p['pinned']:
                    order = body.get('order', len(ordered))
                    if not isinstance(order, int) or isinstance(order, bool) or order < 0: raise CatalogError('Invalid pin order')
                    ordered.insert(min(order, len(ordered)), p)
                else: p.pop('pin_order', None)
                for i, row in enumerate(ordered): row['pin_order'] = i
            elif action in ('archive','restore'):
                p['archived'] = action == 'archive'
            elif action == 'rename':
                preserve_project(value, p['id'])
                p['name'] = _text(body.get('name'), 'project name')
                if re.sub('[^a-z0-9]', '', p['name'].lower()) == 'automationruns': raise CatalogError('Automation runs is reserved')
            elif action == 'group': p['group'] = _text(body.get('group'), 'group name', empty=True)
            elif action == 'mapping': p['workspace_mappings'] = _mappings(body.get('workspace_mappings'))
            else: raise CatalogError('Unknown project action')
        snapshot = _save(data, original, value)
        return {**catalog(data), 'snapshot': snapshot}


_CATALOG = {'inputs': None, 'value': None, 'busy': False}


def _build_catalog(inputs):
    try:
        value = catalog(inputs=inputs[:4])
        _CATALOG.update(inputs=inputs, value=value)
        return value
    finally:
        _CATALOG['busy'] = False


def cached_catalog(data):
    """GET catalog from the shared parsed inputs. A changed index (rewritten every
    few seconds while chats run) is answered from the last catalog while a new one
    is built in the background; mutations still read fresh files under the lock."""
    inputs = tuple(json_cache.latest(Path(data) / (name + '.json'), None) for name in ('projects', 'index', 'chat_flags', 'config'))
    if inputs[0] is None:
        return catalog(data)  # missing or corrupt taxonomy: keep the uncached behavior
    inputs = (inputs[0], inputs[1] or {}, inputs[2] or {}, inputs[3] or {}, int(time.time() // 60))
    last = _CATALOG['inputs']
    if last and all(a is b for a, b in zip(last, inputs[:4])) and last[4] == inputs[4]:
        return _CATALOG['value']
    if _CATALOG['value'] is None or last[0] is not inputs[0]:
        return _build_catalog(inputs)  # first read, or the taxonomy itself changed
    if not _CATALOG['busy']:
        _CATALOG['busy'] = True
        threading.Thread(target=_build_catalog, args=(inputs,), daemon=True).start()
    return _CATALOG['value']


def _route(req, query, body):
    # Share the core server mutex with legacy assign/rename routes. This works
    # both when imported and when server.py is executed as __main__.
    server = sys.modules.get(req.__class__.__module__)
    lock = getattr(server, 'LOCK', _LOCK)
    data = getattr(server, 'DATA', DATA)
    if body is None:
        return cached_catalog(data)
    try:
        with lock:
            return mutate(body, data)
    except CatalogError as error:
        return {'ok': False, 'error': str(error)}, 400


ROUTES = {('GET', '/api/project_catalog'): _route, ('POST', '/api/project_catalog'): _route}
BACKGROUND = []
