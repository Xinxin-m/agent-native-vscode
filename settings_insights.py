"""Read-only resource catalog and evidence-based custom tool / agent analytics."""
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import re
import threading
import time
from commands import _metadata
from automation import automation_kind

HOME = Path.home()
DATA = Path(__file__).parent / 'data'
_cache = {}
_file_cache = {}
_lock = threading.Lock()


# Standard API-equivalent USD / million tokens, verified 2026-11-14.
# Sources are returned with the estimates; actual subscription charges are unknown.
# Tuple: fresh input, output, cache read, short write, long write.
PRICES = {
    'claude-opus-5-5': (4,20,.2,5,8),
    'claude-sonnet-5-5': (2,10,.1,2.5,4),
    'claude-fable-5-1': (10,50,.25,12.5,20),
    'claude-fable-5-2': (8,40,.2,10,16),
    'claude-opus-5': (5,25,.5,6.25,10),
    'claude-haiku-4-5': (1,5,.1,1.25,2),
    'gpt-6.1-sol': (2,10,.1,2.5,5),
    'gpt-6.1-terra': (1,5,.05,1.25,2.5),
    'gpt-6-astra': (10,50,1,12.5,25),
    'gpt-6-luna': (.1,.5,.01,.125,.25),
}
PRICE_SOURCES = ['https://platform.claude.com/docs/en/about-claude/pricing',
                 'https://developers.openai.com/api/docs/pricing']


def estimate_cost(buckets):
    low=high=0.0; priced=total=0
    for b in buckets:
        tokens=sum(int(b.get(k) or 0) for k in ('in','out','cache_read','cache_create'))
        total+=tokens
        model=re.sub(r'-\d{8}$','',b.get('model') or '')
        rates=PRICES.get(model)
        if not rates: continue
        priced+=tokens
        base=sum(int(b.get(k) or 0)*r for k,r in zip(('in','out','cache_read'),rates[:3]))
        low+=(base+int(b.get('cache_create') or 0)*rates[3])/1e6
        high+=(base+int(b.get('cache_create') or 0)*rates[4])/1e6
    return dict(low=round(low,6) if priced else None, high=round(high,6) if priced else None,
                coverage=priced/total if total else 0, priced_tokens=priced, total_tokens=total)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def load_index():
    return load_json(DATA / 'index.json', {})


def workspace_rows(index):
    rows = {}
    for meta in index.values():
        cwd = meta.get('cwd')
        if not cwd:
            continue
        host = meta.get('node') or 'mac'
        row = rows.setdefault((host, cwd), dict(path=cwd, host=host, chats=0, agents=0))
        row['agents' if meta.get('is_child') else 'chats'] += 1
    return sorted(rows.values(), key=lambda r: (-r['chats'], r['path']))


def catalog(cwd='', host='mac'):
    workspaces = workspace_rows(load_index())
    local = host in ('mac', 'local', '')
    valid = not cwd or any(r['path'] == cwd and r['host'] == host for r in workspaces)
    if not valid:
        return dict(items=[], workspaces=workspaces, notice='Workspace is not in the indexed library.')
    if not local:
        return dict(items=[], workspaces=workspaces, notice='Remote resources have not been inventoried. Conversation counts come from mirrored history.')
    items, seen, scanned = [], {}, set()

    def add(path, kind, provider, scope):
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                return
            canonical = str(path.resolve())
            if canonical in seen:
                item=seen[canonical]
                if provider not in item['provider'].split(' / '): item['provider']+=' / '+provider
                return
            meta = _metadata(path) if path.suffix == '.md' else {}
            name = meta.get('name') or (path.parent.name if path.name == 'SKILL.md' else path.stem)
            items.append(dict(id=hashlib.sha256(canonical.encode()).hexdigest()[:24], name=name,
                              description=meta.get('description', '')[:600], kind=kind, provider=provider,
                              scope=scope, path=str(path), modified=path.stat().st_mtime))
            seen[canonical]=items[-1]
        except (OSError, RuntimeError):
            pass

    def scan(directory, scope):
        if directory in scanned: return
        scanned.add(directory)
        for provider, dot in [('Claude', '.claude'), ('Codex', '.codex'), ('Shared', '.agents')]:
            base = directory / dot
            for pattern in ('skills/*/SKILL.md', 'skills/.system/*/SKILL.md'):
                for p in sorted(base.glob(pattern)): add(p, 'skill', provider, scope)
            for p in sorted((base / 'agents').glob('*')):
                if p.suffix in ('.md', '.toml'): add(p, 'agent', provider, scope)
            for p in sorted((base / 'memory').glob('*.md')): add(p, 'memory', provider, scope)
            for p in sorted((base / 'rules').glob('**/*.md')): add(p, 'harness', provider, scope)
            for filename in ('CLAUDE.md', 'AGENTS.md', 'MEMORY.md'):
                add(base / filename, 'memory' if filename == 'MEMORY.md' else 'harness', provider, scope)
        for filename, provider in [('CLAUDE.md', 'Claude'), ('AGENTS.md', 'Shared'), ('GEMINI.md', 'Gemini')]:
            add(directory / filename, 'harness', provider, scope)

    if cwd:
        directory = Path(cwd)
        if directory.is_dir():
            for parent in (directory, *directory.parents):
                if parent == HOME: break
                scan(parent, 'Workspace' if parent == directory else 'Inherited')
        slug = cwd.replace('/', '-')
        for p in sorted((HOME / '.claude/projects' / slug / 'memory').glob('*.md')):
            add(p, 'memory', 'Claude', 'Workspace')
    scan(HOME, 'Global')
    if not cwd:
        for workspace in workspaces:
            if workspace['host'] not in ('mac','local',''): continue
            directory=Path(workspace['path'])
            if not directory.is_dir(): continue
            for parent in (directory,*directory.parents):
                if parent == HOME: break
                scan(parent,'Workspace' if parent == directory else 'Inherited')
    # Plugin skill packages are separately scoped; inventories never imply loaded instructions.
    for provider, dot in [('Codex', '.codex'), ('Claude', '.claude')]:
        for p in sorted((HOME / dot / 'plugins/cache').glob('*/*/*/skills/*/SKILL.md')):
            add(p, 'skill', provider, 'Plugin')
        for p in sorted((HOME / dot / 'plugins/cache').glob('*/*/*/agents/*.md')):
            add(p, 'agent', provider, 'Plugin')
    if not cwd:
        for p in sorted((HOME / '.claude/projects').glob('*/memory/*.md')):
            add(p, 'memory', 'Claude', 'Workspace')
    return dict(items=items, workspaces=workspaces, notice='Inventory of files on this Mac. Availability does not prove a session loaded a resource.')


def preview(resource_id, cwd='', host='mac'):
    item = next((i for i in catalog(cwd, host)['items'] if i['id'] == resource_id), None)
    if not item:
        return {'error': 'Resource is not in this catalog.'}, 404
    try:
        text = Path(item['path']).read_text(errors='replace')
        return dict(text=text[:100000], truncated=len(text) > 100000)
    except OSError:
        return {'error': 'Resource is no longer available.'}, 404


def day_of(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone().date().isoformat()
    except ValueError:
        return ''


def visible_chars(value):
    if isinstance(value, str):
        return len(re.sub(r'data:[^\s]+;base64,[A-Za-z0-9+/=]+', '', value))
    if isinstance(value, list): return sum(visible_chars(x) for x in value)
    if isinstance(value, dict):
        if value.get('type') in ('image', 'image_url', 'audio'): return 0
        return sum(visible_chars(v) for k, v in value.items() if k in ('text', 'content', 'output'))
    return 0


def custom_kind(name, args):
    leaf=re.split(r'__|\.',name)[-1].lower()
    if leaf == 'skill': return 'skill', str(args.get('skill') or 'Unknown skill')
    if name.startswith(('mcp__claude__','mcp__claude.')) and leaf in {'bash','read','write','edit','glob','grep','notebookedit','todowrite','taskoutput','taskstop','websearch','webfetch'}: return None
    if name.startswith(('mcp__', 'mcp.', 'mcp_')): return 'tool', name
    return None


def executable_js(code):
    # Ignore quoted examples, shell commands and comments, preserving only code.
    return re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/", ' ', code)


def scan_calls(paths):
    calls, results, failed_files = {}, {}, 0
    for filename in sorted(set(paths)):
        try:
            with open(filename) as f:
                for line in f:
                    try: d = json.loads(line)
                    except ValueError: continue
                    ts = d.get('timestamp') or ''
                    msg = d.get('message') or {}
                    payload = d.get('payload') or {}
                    blocks = msg.get('content') if isinstance(msg.get('content'), list) else []
                    if d.get('type') == 'response_item': blocks = [payload]
                    for b in blocks:
                        if not isinstance(b, dict): continue
                        kind = b.get('type')
                        if kind in ('tool_result', 'function_call_output', 'custom_tool_call_output'):
                            cid = b.get('tool_use_id') or b.get('call_id')
                            results[cid] = (visible_chars(b.get('content', b.get('output', ''))), bool(b.get('is_error')))
                        if kind not in ('tool_use', 'function_call', 'custom_tool_call'): continue
                        name = b.get('name') or ''
                        namespace = b.get('namespace') or ''
                        if namespace and not name.startswith(namespace+'.'): name=namespace+'.'+name
                        args = b.get('input', b.get('arguments', {}))
                        raw = args if isinstance(args, str) else json.dumps(args)
                        if isinstance(args, str):
                            try: args = json.loads(args)
                            except ValueError: args = {}
                        if not isinstance(args, dict): args = {}
                        cid = b.get('call_id') or b.get('id') or hashlib.sha256((name+raw+ts).encode()).hexdigest()
                        names = [(name, 'direct call')]
                        if name in ('functions.exec', 'exec'):
                            names = [(n, 'nested reference') for n in re.findall(r'\btools\.(mcp__[\w]+)\s*\(', executable_js(raw))]
                        for num, (tool, evidence) in enumerate(names):
                            classified = custom_kind(tool, args)
                            if not classified: continue
                            category, label = classified
                            calls[(cid, num)] = dict(name=label, kind=category, day=day_of(ts), call_id=cid,
                                result_chars=None, failed=False, cost_usd=None, evidence=evidence)
        except OSError:
            failed_files += 1
    for call in calls.values():
        if call['evidence'] == 'direct call' and call['call_id'] in results:
            call['result_chars'], call['failed'] = results[call['call_id']]
    return dict(calls=list(calls.values()), missing_files=failed_files)


def cached_calls(meta):
    paths = meta.get('files_all') or [meta.get('file')]
    paths = [p for p in paths if p]
    signatures = []
    for p in paths:
        try:
            st = Path(p).stat(); signatures.append((p, st.st_mtime_ns, st.st_size))
        except OSError: signatures.append((p, None, None))
    signature = tuple(signatures)
    with _lock: hit = _file_cache.get(signature)
    if hit is not None: return hit
    result = scan_calls(paths)
    with _lock:
        if len(_file_cache) > 15000: _file_cache.clear()
        _file_cache[signature] = result
    return result


def analytics(days=30, cwd='', host='', include_ops=False):
    today = dt.datetime.now().astimezone().date()
    lo, hi = (today-dt.timedelta(days=days-1)).isoformat(), today.isoformat()
    index = load_index(); enrich = load_json(DATA/'enrich.json', {})
    selected = {}
    for key, meta in index.items():
        root = index.get(meta.get('root_key') or key, meta)
        models={b.get('model') for b in meta.get('usage_buckets',[])}
        if not include_ops and models == {'codex-auto-review'}: continue
        if not include_ops and (automation_kind(root) or (enrich.get(meta.get('root_key') or key) or {}).get('is_ops')): continue
        if cwd and meta.get('cwd') != cwd: continue
        if host and meta.get('node', 'mac') != host: continue
        if (day_of(meta.get('ts_last')) or '9999') < lo: continue
        selected[key] = meta
    groups = {}; missing = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (key, meta), result in zip(selected.items(), pool.map(cached_calls, selected.values())):
            missing += result['missing_files']
            for call in result['calls']:
                if not lo <= call['day'] <= hi: continue
                ident = (call['kind'], call['name'])
                row = groups.setdefault(ident, dict(kind=call['kind'], name=call['name'], calls=0, failures=0,
                    result_chars=0, measured_results=0, references=0, cost_usd=None, sessions=set()))
                row['calls'] += call['evidence'] == 'direct call'
                row['references'] += call['evidence'] != 'direct call'
                row['failures'] += call['failed']
                if call['result_chars'] is not None:
                    row['result_chars'] += call['result_chars']; row['measured_results'] += 1
                row['sessions'].add(meta.get('root_key') or key)
    agents = {}
    for key, meta in selected.items():
        if not meta.get('is_child'): continue
        parent = index.get(meta.get('parent_key'), {})
        edge = next((c for c in parent.get('spawned_children', []) if c.get('agent_id') == meta.get('agent_id')), {})
        name = edge.get('agent_type') or meta.get('agent_role') or meta.get('agent_path') or 'Unnamed delegated agent'
        usage = Counter()
        for day, u in (meta.get('usage_days') or {}).items():
            if lo <= day <= hi: usage.update({f:int(u.get(f) or 0) for f in ('in','out','cache_read','cache_create','turns')})
        if not usage: continue
        row = agents.setdefault(name, dict(name=name, kind='agent', runs=0, usage=Counter(), cost_usd=None, sessions=set(), pricing_buckets=[]))
        priced_buckets=[b for b in meta.get('usage_buckets',[]) if lo <= b.get('day','') <= hi]
        row['pricing_buckets'].extend(priced_buckets or [dict(usage,model='unknown')])
        row['runs'] += 1; row['usage'].update(usage); row['sessions'].add(meta.get('root_key') or key)
    def finish(row):
        if 'pricing_buckets' in row:
            row['estimate']=estimate_cost(row.pop('pricing_buckets'))
            if not row['estimate']['total_tokens']: row['estimate']['coverage']=0
        row['sessions'] = [dict(key=k, title=(enrich.get(k) or {}).get('title') or index.get(k, {}).get('title_seed') or k) for k in sorted(row['sessions'])]
        return row
    return dict(from_day=lo, to_day=hi, tools=[finish(v) for v in sorted(groups.values(), key=lambda r:-(r['calls']+r['references']))],
        agents=[finish(v) for v in sorted(agents.values(), key=lambda r:-sum(r['usage'].values()))],
        scanned_sessions=len(selected), missing_files=missing, workspaces=workspace_rows(index), price_sources=PRICE_SOURCES, prices_checked='2026-11-14',
        notice='Agent dollar estimates use current standard API rates, not subscription bills. Ranges reflect unknown cache-write duration; unpriced models remain excluded. Tool output size is characters ÷ 4, not billed tokens. Nested references may not have executed.')


def resource_route(req, query, body):
    q=query or {}; return catalog(q.get('cwd',''),q.get('host','mac'))


def preview_route(req, query, body):
    q=query or {}; return preview(q.get('id',''),q.get('cwd',''),q.get('host','mac'))


def analytics_route(req, query, body):
    q=query or {}
    try: days=max(1,min(365,int(q.get('days',30))))
    except (ValueError,TypeError): days=30
    args=(days,q.get('cwd',''),q.get('host',''),str(q.get('include_ops','')) in ('1','true'))
    with _lock: hit=_cache.get(args)
    if hit and not q.get('refresh') and time.monotonic()-hit[0]<120: return hit[1]
    result=analytics(*args)
    with _lock: _cache[args]=(time.monotonic(),result)
    return result

ROUTES={('GET','/api/settings/resources'):resource_route,('GET','/api/settings/resource'):preview_route,
        ('GET','/api/settings/tool-analytics'):analytics_route}
BACKGROUND=[]
