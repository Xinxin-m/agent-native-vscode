"""Code-first project placement for chats that have none, with no model call.

A chat with 3 user turns and no project is placed from, in order: the purpose rules in
data/config.json (`classification_rules`, the same ones project_review.py uses); a
project's own workspace (`default_workspace`, `workspace_mappings`); and the folders its
existing chats ran in, when one project holds at least 80 percent of at least 3 chats
there. The chat's working folder and the folders of the files it changed are checked;
two different answers mean code cannot decide and the existing first-label model call
(enrich.py) places it. Manual and existing placements are never touched. Every decision is
appended to data/placement_log.jsonl, read back by GET /api/placements.
"""
import datetime as dt
import json
import os
import posixpath

from automation import automation_kind
from project_review import matching_rules

MIN_USER_TURNS = 3
MIN_FOLDER_CHATS = 3
PURITY = 0.8
LOG = 'placement_log.jsonl'


def _real(project):
    return not project.get('automation') and not project.get('archived') and project.get('grouping_source') != 'workspace'


def _host(meta):
    return str(meta.get('execution_host') or meta.get('host_id') or meta.get('node') or 'mac').split('+')[0]


def unplaced(key, meta, projects):
    """True when no project and no manual choice stands for this chat."""
    if meta.get('is_child') or automation_kind(meta) or (meta.get('n_user') or 0) < MIN_USER_TURNS:
        return False
    if key in (projects.get('manual_assignments') or []):
        return False
    pid = (projects.get('assignments') or {}).get(key)
    seed = (projects.get('workspace_seeds') or {}).get(key) == pid
    return pid is None or seed


def folder_votes(index, projects):
    """{(host, cwd): {project id: chats}} over chats placed in real projects."""
    real = {p['id'] for p in projects.get('projects', []) if _real(p)}
    votes = {}
    for key, pid in (projects.get('assignments') or {}).items():
        meta = index.get(key)
        if pid not in real or not meta or meta.get('is_child') or automation_kind(meta) or not meta.get('cwd'):
            continue
        if (projects.get('workspace_seeds') or {}).get(key) == pid and key not in (projects.get('manual_assignments') or []):
            continue
        row = votes.setdefault((_host(meta), meta['cwd']), {})
        row[pid] = row.get(pid, 0) + 1
    return votes


def _within(path, root):
    root = root.rstrip('/') or '/'
    return path == root or path.startswith(root.rstrip('/') + '/')


def _roots(projects):
    out = []
    for p in projects.get('projects', []):
        if not _real(p):
            continue
        saved = p.get('default_workspace') or {}
        if saved.get('path'):
            out.append((saved.get('host_id') or 'mac', saved['path'], p['id'], 'saved workspace'))
        for mapping in p.get('workspace_mappings') or []:
            if mapping.get('path'):
                out.append((mapping.get('host') or 'mac', mapping['path'], p['id'], 'mapped folder'))
    return out


def _by_workspace(host, path, roots):
    hits = [(len(root), pid, root, kind) for h, root, pid, kind in roots if h == host and _within(path, root)]
    if not hits:
        return None
    top = max(hit[0] for hit in hits)
    winners = {hit[1] for hit in hits if hit[0] == top}
    if len(winners) != 1:
        return None
    hit = next(hit for hit in hits if hit[0] == top)
    return hit[1], 'workspace', 'inside the %s %s' % (hit[3], hit[2])


def _by_folders(host, path, votes):
    folder = path
    while folder and folder != '/':
        row = votes.get((host, folder))
        total = sum(row.values()) if row else 0
        if total >= MIN_FOLDER_CHATS:
            pid, count = max(row.items(), key=lambda kv: kv[1])
            if count / total >= PURITY:
                return pid, 'folders', '%d of %d chats in %s are in this project' % (count, total, folder)
            return None  # a mixed folder (for example the research root) decides nothing
        folder = posixpath.dirname(folder)
    return None


def decide(meta, label, projects, config, votes, files=()):
    """(project id, method, reason) or None when code cannot decide."""
    real = {p['id'] for p in projects.get('projects', []) if _real(p)}
    rules = [r for r in matching_rules(meta, label or {}, config.get('classification_rules') or []) if r.get('project_id') in real]
    targets = {r['project_id'] for r in rules}
    if len(targets) == 1:
        return rules[0]['project_id'], 'rules', rules[0].get('reason') or 'matches a configured purpose rule'
    if len(targets) > 1:
        return None
    host = _host(meta)
    paths = [meta['cwd']] if meta.get('cwd') else []
    paths += sorted({posixpath.dirname(f) for f in files if isinstance(f, str) and f.startswith('/')})
    roots = _roots(projects)
    found = []
    for path in paths:
        found.append(_by_workspace(host, path, roots) or _by_folders(host, path, votes))
    decided = [d for d in found if d]
    if len({d[0] for d in decided}) != 1:
        return None
    # The working folder must not disagree: an undecided folder is fine, a different project is not.
    return decided[0]


def code_placements(index, projects, enrich, config, load_chat, limit=200):
    """Decisions for every chat that qualifies. load_chat(key) returns the chat dict or None."""
    votes = folder_votes(index, projects)
    names = {p['id']: p['name'] for p in projects.get('projects', [])}
    out = []
    for key, meta in sorted(index.items(), key=lambda kv: kv[1].get('ts_last') or '', reverse=True):
        if len(out) >= limit:
            break
        if not unplaced(key, meta, projects):
            continue
        chat = load_chat(key) or {}
        files = [f.get('path') for f in chat.get('files_changed') or [] if isinstance(f, dict)]
        got = decide(meta, enrich.get(key), projects, config, votes, files)
        if got:
            out.append({'key': key, 'project_id': got[0], 'project_name': names.get(got[0], got[0]), 'method': got[1],
                        'reason': got[2], 'title': (enrich.get(key) or {}).get('title') or meta.get('title_seed') or key})
    return out


def log_decisions(data, decisions, now=None):
    if not decisions:
        return
    at = (now or dt.datetime.now()).isoformat(timespec='seconds')
    with open(os.path.join(data, LOG), 'a') as handle:
        for row in decisions:
            handle.write(json.dumps(dict(row, at=at, by='code'), ensure_ascii=False) + '\n')


def read_log(data, key='', limit=50):
    try:
        with open(os.path.join(data, LOG)) as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        return []
    rows = [r for r in rows if not key or r.get('key') == key]
    return rows[-max(1, min(limit, 500)):][::-1]
