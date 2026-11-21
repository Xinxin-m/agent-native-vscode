"""Bounded, deterministic transcript search. No enrichment or external calls."""
import json
from pathlib import Path
import time

import file_service as fs

DATA = Path(__file__).resolve().parent / 'data'


def search(data, query):
    data = Path(data)
    needle = str(query.get('q') or '').strip()
    if len(needle) > 200:
        raise fs.FileError('query_too_long','Search is limited to 200 characters')
    try:
        limit = min(50,max(1,int(query.get('limit') or 50)))
        offset = max(0,int(query.get('cursor') or 0))
    except (TypeError,ValueError):
        raise fs.FileError('invalid_cursor','Invalid search cursor')
    index = fs.read_json(data/'index.json',{})
    config = fs.read_json(data/'config.json',{})
    project_state = fs.read_json(data/'projects.json',{})
    assignments = project_state.get('assignments',{})
    from project_review import descendants
    project_ids = descendants(project_state.get('projects',[]), query.get('project_id'))
    revision = str((data/'index.json').stat().st_mtime_ns) if (data/'index.json').exists() else '0'
    if not needle:
        return {'ok':True,'results':[],'next_cursor':None,'truncated':False,'revision':revision}
    rows, scanned, skipped = [], 0, 0
    matched_roots = set()
    deadline = time.monotonic()+3
    folded = needle.casefold()
    truncated = False
    for key, meta in sorted(index.items(),key=lambda pair:pair[1].get('ts_last') or '',reverse=True):
        # Explicit children are reached through their parent's tree, never search.
        if meta.get('is_child') or meta.get('parent_key') or (meta.get('root_key') and meta['root_key'] != key):
            continue
        logical_key = key
        if logical_key not in index:
            logical_key = key
        if logical_key in matched_roots:
            continue
        cwd = meta.get('cwd') or ''
        if any(cwd == p or cwd.startswith(p.rstrip('/')+'/') for p in config.get('excluded_cwds',[]) if isinstance(p,str)):
            continue
        if query.get('host_id') and query['host_id'] not in str(meta.get('node') or 'mac').split('+'):
            continue
        if query.get('account_id') and query['account_id'] != meta.get('account_id'):
            continue
        if query.get('provider') and query['provider'] != meta.get('source'):
            continue
        if query.get('project_id') and assignments.get(meta.get('root_key') or key) not in project_ids:
            continue
        if time.monotonic() > deadline or scanned >= 64*1024*1024 or len(rows) >= offset+limit+1:
            truncated = True
            break
        # Only index keys may choose a cleaned transcript; never accept client paths.
        if '/' in key or '\\' in key or key in ('.','..'):
            continue
        path = data/'chats'/(key+'.json')
        try:
            size = path.stat().st_size
            if size > 16*1024*1024:
                skipped += 1
                continue
            scanned += size
            chat = json.loads(path.read_text())
        except (OSError,ValueError):
            skipped += 1
            continue
        for turn_index, turn in enumerate(chat.get('turns') or []):
            if turn.get('channel') == 'analysis':
                continue
            text = str(turn.get('text') or '')
            position = text.casefold().find(folded)
            if position < 0:
                continue
            start = max(0,position-80)
            matched_roots.add(logical_key)
            rows.append({'key':logical_key,'matched_key':key,'turn_index':turn_index,
                         'snippet':('…' if start else '')+text[start:position+len(needle)+140],
                         'role':turn.get('role'),'host_id':meta.get('execution_host') or meta.get('host_id') or meta.get('node'),
                         'cwd':meta.get('cwd'), 'execution_cwd':meta.get('execution_cwd'),
                         'execution_host':meta.get('execution_host') or meta.get('host_id'),
                         'node':meta.get('node')})
            # Pagination counts logical conversations, never matching turns.
            break
    return {'ok':True,'results':rows[offset:offset+limit],
            'next_cursor':str(offset+limit) if len(rows)>offset+limit else None,
            'truncated':truncated or skipped>0,'skipped':skipped,'revision':revision}


@fs.guarded
def route(req,query,body):
    return search(DATA,query)


ROUTES = {('GET','/api/search'):route}
BACKGROUND = []
