"""One notification feed: agent requests, monitor items and app notices (ledger 3 to 7, 25).

GET /api/notifications builds the list on demand from the broker's pending requests,
the monitor's data folder (read only) and the notice store (notices.py). Nothing is
copied into a second store: read, seen, archive and answered marks live in the notice
file under "feed" (notices.feed_state / feed_update). Contract: docs/redesign-2026-11-22/API.md.
"""
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import debug_agent
import json_cache
import notices

MONITOR = os.environ.get('LIBRARY_MONITOR_DIR', str(Path.home() / '.library-monitor'))
MONITOR_URL = 'http://127.0.0.1:8769'
OWNER_STATE = os.environ.get('LIBRARY_MONITOR_OWNER_STATE', 'for_owner')  # monitor card state that means "waiting on the owner"
EVENT_KINDS = {'stalled': 'is stalled', 'dead': 'went silent', 'blocked': 'is blocked',
               'input': 'needs input', 'decision': 'needs a decision', 'quiet': 'has been quiet'}
ASK_EVENTS = ('input', 'decision')  # events that need an answer from her
EVENT_SECONDS = 48 * 3600
CLEARED_SECONDS = 48 * 3600
CACHE_SECONDS = 1.5
BUSY = ('idle', '')
APPROVALS = (('accept', 'Allow'), ('acceptForSession', 'Allow for this session'), ('decline', 'Deny'))
NOTICE_TITLES = {'usage_high': 'Usage is high', 'limit_reached': 'Usage limit reached',
                 'rate_limited': 'Rate limited', 'auth_expired': 'Sign-in expired',
                 'not_logged_in': 'Not signed in', 'login_expiring': 'Sign-in expiring soon'}
WINDOW_TITLES = {'7d': 'Weekly limit reached', '5h': 'Session limit reached', 'credits': 'Credits used up'}
RECOVERY_KINDS = ('limit_reached', 'rate_limited', 'auth_expired', 'not_logged_in', 'other')
_FILES = {}
_STORES = {}
_CACHE = {'at': 0, 'value': None}
_FIRST = {}  # signed-out items: when this process first saw the account signed out
_WAITING = {}  # live sessions waiting for her: session id -> when this process first saw it waiting
_LOCK = threading.RLock()


# ---------------------------------------------------------------- small helpers
def _data():
    import accounts
    return accounts.DATA


def _path():
    return os.path.join(_data(), 'notices.json')


def _config():
    import accounts
    return json_cache.latest(accounts.CONFIG_FILE, {})


def _monitor():
    config = _config()
    config = config if isinstance(config, dict) else {}
    return Path(config.get('monitor_dir') or MONITOR), (config.get('monitor_url') or MONITOR_URL).rstrip('/')


def _short(text, size):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= size else text[:size - 1].rstrip() + '...'


def _digest(text):
    return hashlib.sha1(str(text).encode()).hexdigest()[:10]


def _read(path):
    """Parsed JSON file, re-read only when its stamp changes. Shared: never mutate."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _FILES.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    _FILES[path] = (stamp, value)
    return value


def _events(path):
    """Wake events of the kinds we list, parsed once per file stamp."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _FILES.get(('events', path))
    if hit and hit[0] == stamp:
        return hit[1]
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            if '"wake": true' not in line and '"wake":true' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('wake') is True and event.get('kind') in EVENT_KINDS:
                rows.append(event)
    except OSError:
        return []
    _FILES[('events', path)] = (stamp, rows)
    return rows


def _chat_title(key):
    data = _data()
    meta = (json_cache.latest(os.path.join(data, 'index.json'), {}) or {}).get(key)
    if not isinstance(meta, dict):
        return None
    generated = ((json_cache.latest(os.path.join(data, 'enrich.json'), {}) or {}).get(key) or {}).get('title')
    manual = ((json_cache.latest(os.path.join(data, 'chat_flags.json'), {}) or {}).get('titles') or {}).get(key)
    return (manual or meta.get('desktop_title') or generated or meta.get('title_seed')
            or (meta.get('first_prompt') or '')[:60] or key)


def _chat_of_session(session):
    """A Library chat only when the data names its session id and the index has it."""
    if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', session):
        return None, None
    for prefix in ('claude-', 'codex-'):
        title = _chat_title(prefix + session)
        if title:
            return prefix + session, title
    return None, None


# ---------------------------------------------------------------- seams (patched in tests)
def _attention():
    import broker
    return broker.instance().attention().get('items') or []


def _account_rows():
    import accounts
    payload = accounts.payload()
    return (payload.get('accounts') or []) + (payload.get('pending_accounts') or [])


def _running(key):
    """True when a broker run or a fresh live row says the chat is working now."""
    import broker
    if any(run.get('status') not in broker.TERMINAL
           for run in broker.instance().store.list(summaries=True, key=key)):
        return True
    import live
    chat_id = key.split('-', 1)[-1]
    rows = (json_cache.latest(live.LIVE_JSON, {}) or {}).get('sessions') or []
    now = time.time()
    return any(row.get('session_id') == chat_id and row.get('alive') and row.get('status') not in BUSY
               and now - (row.get('observed_at') or 0) < 180 for row in rows)


def _live_rows():
    import sidebar
    return sidebar._live_sessions()


def _live_status(row, now):
    import live
    return live.session_status(row, now)


def _failed_run(run_id):
    import broker
    return broker.instance().store.get(run_id)


def _recover(body):
    import auth_recovery
    import broker
    return auth_recovery.recover(broker.instance(), body)


def _control(body):
    import broker
    return broker.control_run(None, {}, body)


def _create_run(body):
    import broker
    return broker.create_run(None, {}, body)


def _start_login(aid):
    import accounts
    return accounts.start_login(aid)


def _orch_store(root):
    store = _STORES.get(str(root))
    if store is None:
        spec = importlib.util.spec_from_file_location('_library_orch_store', Path(root) / 'store.py')
        store = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(store)
        _STORES[str(root)] = store
    return store


# ---------------------------------------------------------------- items
def _item(id, source, kind, title, at, **extra):
    return dict({'id': id, 'source': source, 'kind': kind, 'title': title, 'body': '', 'at': at,
                 'origin': 'library', 'options': [], 'allow_text': False, 'needs_answer': False,
                 'state': 'open', 'answer': None, 'link': '', 'seen': False, 'shown': False}, **extra)


def _summary(payload):
    command = payload.get('command')
    if isinstance(command, list):
        command = ' '.join(map(str, command))
    tool = payload.get('input') if isinstance(payload.get('input'), dict) else {}
    command = command or tool.get('command') or tool.get('file_path') or tool.get('path') or tool.get('description')
    if not command and tool:
        command = json.dumps(tool, ensure_ascii=False)
    return _short(command or payload.get('reason') or '', 400)


def _broker_items(warnings):
    items, routes = [], {}
    try:
        rows = _attention()
    except Exception as error:
        warnings.append(f'broker: {type(error).__name__}: {error}')
        return items, routes
    for row in rows:
        if not row.get('actionable'):
            continue
        payload, rid, req = row.get('payload') or {}, row['run_id'], row['request_id']
        key = row.get('key') or ''
        id = f'broker:{rid}:{req}'
        extra = {'origin': 'library', 'needs_answer': True, 'chat_key': key or None,
                 'chat_title': _chat_title(key) if key else None, 'run_id': rid}
        if row['kind'] == 'approval':
            name = payload.get('name') or payload.get('tool_name')
            decisions = payload.get('availableDecisions')
            offered = [(d, label) for d, label in APPROVALS
                       if (d in decisions if isinstance(decisions, list) else d != 'acceptForSession')]
            what = ('Allow ' + name + '?') if name else 'Allow this file change?' if 'fileChange' in str(payload.get('method')) \
                else 'Allow this command?'
            items.append(_item(id, 'agent', 'approval', what, notices._epoch(row.get('created_at')) or time.time(), body=_summary(payload),
                               options=[label for _, label in offered], **extra))
            routes[id] = {'type': 'broker', 'run_id': rid, 'request_id': req, 'kind': 'approval',
                          'decisions': {label: d for d, label in offered}}
        else:
            questions = [q for q in payload.get('questions') or [] if isinstance(q, dict)]
            first = questions[0] if len(questions) == 1 else None
            title = _short((questions[0].get('question') or questions[0].get('header')) if questions
                           else 'The agent needs your answer.', 200)
            body = '\n'.join(_short(q.get('question') or q.get('header'), 200) for q in questions) if len(questions) > 1 else ''
            options = [str(o.get('label') if isinstance(o, dict) else o) for o in (first or {}).get('options') or []]
            items.append(_item(id, 'agent', 'question', title, notices._epoch(row.get('created_at')) or time.time(), body=body, options=options,
                               allow_text=first is not None, **extra))
            routes[id] = {'type': 'broker', 'run_id': rid, 'request_id': req, 'kind': 'question',
                          'question': (first or {}).get('id') or (first or {}).get('question') or 'answer' if first else None}
    return items, routes


def _monitor_items(now, warnings):
    root, base = _monitor()
    data = root / 'data'
    items, routes = [], {}
    if not data.is_dir():
        return items, routes
    verify_dir = data / 'monitor' / 'verify'
    open_cards = set()
    for path in sorted(verify_dir.glob('*.json')) if verify_dir.is_dir() else []:
        card = _read(str(path))
        if isinstance(card, dict) and card.get('state') == OWNER_STATE:
            open_cards.add('V' + str(card.get('id')))
    for path in sorted((data / 'threads').glob('*/status.json')):
        status = _read(str(path))
        if not isinstance(status, dict):
            continue
        tid, link = path.parent.name, f'{base}/t/{path.parent.name}'
        entries = (_read(str(data / 'responses' / f'{tid}.json')) or {}).get('entries') or {}
        at = notices._epoch(status.get('updated_at')) or now
        for d in status.get('decisions') or []:
            did = str(d.get('id') or '')
            if not did or d.get('status') != 'open':
                continue
            if re.fullmatch(r'V[0-9a-f]{6,}', did) and verify_dir.is_dir() and did not in open_cards:
                continue  # the card was answered or closed; the page republishes later
            id = f'mon:{tid}:dec:{did}'
            body = f"Recommendation: {d.get('recommendation') or 'none'}\n\nReason: {d.get('reason') or 'none given'}"
            item = _item(id, 'agent', 'decision', _short(d.get('question'), 300), notices._epoch(d.get('at')) or at,
                         body=_short(body, 2500), origin='monitor', thread=tid, link=link, needs_answer=True,
                         options=[str(o) for o in d.get('options') or []], allow_text=True)
            entry = entries.get(f'decision:{did}')
            if entry:
                value = entry.get('value') if isinstance(entry.get('value'), dict) else {'choice': entry.get('value')}
                item.update(state='answered', answer={'choice': value.get('choice') or '', 'text': value.get('text') or '',
                                                      'at': notices._epoch(entry.get('ts'))})
            items.append(item)
            routes[id] = {'type': 'mdec', 'thread': tid, 'decision': did, 'card': did in open_cards}
        for a in status.get('attention') or []:
            text = str(a.get('text') or '').strip()
            if text:
                id = f'mon:{tid}:attn:{_digest(text)}'
                items.append(_item(id, 'agent', 'attention', _short(text, 90), notices._epoch(a.get('at')) or at,
                                   body=text, origin='monitor', thread=tid, link=link))
                routes[id] = {'type': 'mtext', 'chat_key': None}
    latest = {}
    for path in sorted((data / 'events').glob('*.jsonl')):
        upto = (_read(str(path.with_suffix('.ack.json'))) or {}).get('upto')
        for event in _events(str(path)):
            when = notices._epoch(event.get('ts'))
            if when is None or now - when > EVENT_SECONDS or str(upto or '').isdigit() and str(event.get('id') or '').isdigit() \
                    and int(event['id']) <= int(upto):
                continue
            key = (event.get('thread') or path.stem, event.get('agent') or '-', event['kind'])
            if key not in latest or when >= latest[key][0]:
                latest[key] = (when, event)
    for (tid, agent, kind), (when, event) in latest.items():
        chat_key, chat_title = _chat_of_session(event.get('session'))
        id = f'mon:{tid}:ev:{agent}:{kind}'
        who = event.get('desc') or event.get('agent_type') or 'An agent'
        items.append(_item(id, 'agent', kind, f'{_short(who, 70)} {EVENT_KINDS[kind]}', when, body=str(event.get('text') or ''),
                           origin='monitor', thread=tid, link=f'{base}/t/{tid}', needs_answer=kind in ASK_EVENTS,
                           chat_key=chat_key, chat_title=chat_title))
        routes[id] = {'type': 'mtext', 'chat_key': chat_key}
    return items, routes


def _account_name(account, rows):
    """The name the in-chat recovery card gives an account (authRecovery.js accountName)."""
    email = account.get('email') or ''
    label = account.get('label') if account.get('label') != email else ''
    peers = [x for x in rows if not x.get('duplicate_of') and x['id'] != account['id'] and x.get('tool') == account.get('tool')]
    if email and not any(x.get('email') == email for x in peers):
        return email
    name = f'{label} ({email})' if label and email else label or email or account['id']
    return name + ' \u00b7 ' + account['id'] if any((x.get('email') or '') == email and (x.get('label') or '') == (account.get('label') or '')
                                                   for x in peers) else name


def _has_capacity(account):
    return not any(isinstance(w.get('percent'), (int, float)) and w['percent'] >= 100 for w in account.get('windows') or [])


def _recovery(n, rows, by_id):
    """(options, actions) for a failed-run notice: the actions the in-chat recovery card offers,
    each run through auth_recovery.recover. Empty when the failure cannot be recovered from here."""
    run_id, key, kind = n.get('run_id'), n.get('chat_key'), n.get('kind')
    if kind not in RECOVERY_KINDS or not (run_id or key):
        return [], {}
    account = by_id.get(n.get('account_id'))
    aid = str(n.get('account_id') or '')
    provider = (account or {}).get('tool') or (aid.partition('unattributed-')[2] if aid.startswith('unattributed-')
                                               else (key or '').split('-', 1)[0])
    if provider not in ('claude', 'codex'):
        return [], {}
    remote = False
    if run_id:
        try:
            run = _failed_run(run_id)
        except Exception:
            run = None
        if run and run.get('status') != 'failed':
            return [], {}
        remote = bool(run) and (run.get('host_id') or 'mac') not in ('mac', 'local')
    options, actions = [], {}

    def add(label, **action):
        options.append(label)
        actions[label] = action
    auth = kind in ('auth_expired', 'not_logged_in')
    if auth and not (account and account.get('logged_in')):
        add('Sign in again', action='login')
    else:
        add('Retry message', action='retry')
    if kind in ('limit_reached', 'auth_expired', 'not_logged_in'):
        if not remote:
            for other in rows:
                if other.get('tool') == provider and other['id'] != n.get('account_id') and not other.get('duplicate_of') \
                        and other.get('logged_in') and other.get('email') and not other.get('disabled') and _has_capacity(other):
                    add('Continue on ' + _account_name(other, rows), action='account', account_id=other['id'])
        target = 'claude' if provider == 'codex' else 'codex'
        add('Move to ' + target.title(), action='handoff', provider=target)
    return options, {'ref': {'id': run_id} if run_id else {'key': key}, 'actions': actions}


def _app_title(n):
    kind = n.get('kind') or 'other'
    window = notices._family_of(n).partition(':usage:')[2]
    if kind == 'limit_reached' and window:
        return WINDOW_TITLES['7d' if window.startswith('7d') else window] if window.startswith('7d') or window in WINDOW_TITLES \
            else NOTICE_TITLES[kind]
    if kind == 'other':
        return 'Run failed' if n.get('run_id') else _short(n.get('message'), 80) or 'Notice'
    return NOTICE_TITLES.get(kind) or 'Sign-in needs attention'


def _app_items(now, rows):
    items, routes = [], {}
    listing = notices.listing(_path(), now)['notices']
    by_id = {row['id']: row for row in rows}
    signed_out = {row['id'] for row in rows if row.get('previously_used') and not row.get('logged_in')
                  and not row.get('duplicate_of')}
    active, ended = set(), {}
    for n in listing:
        if n.get('alias'):
            continue
        archived = n.get('archived_at') is not None
        usage = notices.category(n) == 'usage'
        closed = n.get('resolved_at') is not None or usage and n.get('at') is not None and n['at'] <= now
        family = notices._family_of(n)
        if n.get('kind') in ('usage_high', 'limit_reached') and n.get('account_id'):
            if closed:
                ended[family] = max(ended.get(family, 0), n.get('resolved_at') or n['at'])
            else:
                active.add(family)
        if closed and not archived or not usage and n.get('account_id') in signed_out and n.get('kind') != 'other':
            continue
        key = n.get('chat_key')
        options, recover = _recovery(n, rows, by_id) if not archived and not closed else ([], {})
        item = _item('notice:' + n['key'], 'app', n.get('kind') or 'other', _app_title(n), n.get('first_seen') or now,
                     body=n.get('message') or '', chat_key=key, chat_title=_chat_title(key) if key else None,
                     state='archived' if archived else 'open', seen='read_at' in n, shown='shown_at' in n,
                     account_id=n.get('account_id'), options=options, needs_answer=bool(options))
        if n.get('run_id'):
            item['run_id'] = n['run_id']
        items.append(item)
        routes[item['id']] = {'type': 'notice', 'key': n['key'], 'recover': recover}
    for family, end in ended.items():
        account_id, _, window = family.partition(':usage:')
        row = by_id.get(account_id)
        win = next((w for w in (row or {}).get('windows') or [] if w.get('kind') == window), None)
        percent = (win or {}).get('percent')
        if family in active or now - end > CLEARED_SECONDS or not isinstance(percent, (int, float)) or percent >= 90:
            continue
        item = _item(f'cleared:{family}:{int(end)}', 'app', 'limit_cleared',
                     f"{row.get('label') or account_id}: {win.get('label') or window} is back under the limit", end,
                     body=f"Usage is at {round(percent)} percent.", account_id=account_id)
        items.append(item)
        routes[item['id']] = {'type': 'feed'}
    for gone in set(_FIRST) - signed_out:
        del _FIRST[gone]
    for row in rows:
        if row['id'] in signed_out:
            tool = str(row.get('tool') or '').title()
            item = _item('signedout:' + row['id'], 'app', 'signed_out', f"{tool}: {row.get('label') or row['id']} is signed out",
                         _FIRST.setdefault(row['id'], now), body=row.get('email') or '', options=['Sign in', 'Not now'], needs_answer=True,
                         account_id=row['id'])
            items.append(item)
            routes[item['id']] = {'type': 'signedout', 'account_id': row['id']}
    return items, routes


def _waiting_items(now, broker_items, warnings):
    """One 'Waiting for you' item per live session whose status is waiting and that has no broker
    item (the broker's own approval or question already covers a Library run)."""
    items, routes = [], {}
    try:
        rows = _live_rows()
    except Exception as error:
        warnings.append(f'live: {type(error).__name__}: {error}')
        return items, routes
    covered = {item.get('chat_key') for item in broker_items if item.get('chat_key')}
    current = {}
    for row in rows:
        sid = row.get('session_id')
        if not sid or _live_status(row, now) != 'waiting':
            continue
        key = row.get('chat_key') or ''
        if key and key in covered or f"{row.get('tool')}-{sid}" in covered:
            continue
        current[sid] = row
    for gone in set(_WAITING) - set(current):
        del _WAITING[gone]
    for sid, row in current.items():
        id = 'live:' + sid + ':waiting'
        key = row.get('chat_key') or None
        items.append(_item(id, 'agent', 'waiting', 'Waiting for you', _WAITING.setdefault(sid, now),
                           body=row.get('name') or '', chat_key=key, chat_title=_chat_title(key) if key else None,
                           link='' if key else '/#live-sessions', tool=row.get('tool'), live_id=row.get('id')))
        routes[id] = {'type': 'live'}
    return items, routes


def _build(now):
    """(items, routes, warnings): every item, newest first, with its stored marks applied."""
    warnings = []
    try:
        rows = _account_rows()
    except Exception as error:
        rows = []
        warnings.append(f'accounts: {type(error).__name__}: {error}')
    live, routes = [], {}
    parts = [_broker_items(warnings), _monitor_items(now, warnings), _app_items(now, rows)]
    parts.append(_waiting_items(now, parts[0][0], warnings))
    for part in parts:
        live += part[0]
        routes.update(part[1])
    # signed-out marks use the first moment we saw the condition
    feed = notices.feed_state(_path())
    out, seen_ids = [], set()
    for item in live:
        seen_ids.add(item['id'])
        route, mark = routes[item['id']], feed.get(item['id']) or {}
        if item['id'].startswith('signedout:') and mark.get('first_seen'):
            item['at'] = mark['first_seen']
        if route['type'] != 'notice':
            if (item['origin'] == 'monitor' and item['kind'] in EVENT_KINDS or item['kind'] == 'waiting') and mark \
                    and item['at'] > max(mark.get('archived_at') or 0, (mark.get('answered') or {}).get('at') or 0,
                                         mark.get('seen_at') or 0):
                mark = {}  # a newer repeat of the event reopens it
            if mark.get('seen_at'):
                item['seen'] = True
            if mark.get('shown_at'):
                item['shown'] = True
            if mark.get('archived_at'):
                item['state'] = 'archived'
            elif mark.get('answered') and route['type'] != 'mdec':
                item.update(state='answered', answer=mark['answered'])
        elif route.get('recover') and mark.get('answered') and item['state'] == 'open':
            item.update(state='answered', answer=mark['answered'])
        if route['type'] == 'mtext' and item['state'] == 'open' and item.get('chat_key'):
            try:
                item['allow_text'] = not _running(item['chat_key'])
            except Exception as error:
                warnings.append(f'running check: {type(error).__name__}: {error}')
        if item['state'] != 'open':
            item['seen'] = True
        item['debuggable'] = debug_agent.debuggable(item)
        item['debug'] = debug_agent.status((feed.get(item['id']) or {}).get('debug'), debug_agent.progress_path(_data(), item['id']), now)
        out.append(item)
    for id, mark in feed.items():
        snap = mark.get('snapshot')
        if id in seen_ids or not snap or id.startswith('signedout:') or not (mark.get('archived_at') or mark.get('answered')):
            continue
        item = dict(snap, seen=True, state='archived' if mark.get('archived_at') else 'answered',
                    answer=mark.get('answered'), options=snap.get('options') or [])
        item.update(allow_text=False, debuggable=False, debug=None)
        out.append(item)
        routes[id] = {'type': 'gone'}
    out.sort(key=lambda item: (item.get('at') or 0, item['id']), reverse=True)
    return out, routes, warnings


def _counts(items):
    open_items = [item for item in items if item['state'] == 'open']
    # open = open items that need an answer, open app items and open "waiting" items; seen never changes it
    return {'open': sum(item['needs_answer'] or item['source'] == 'app' or item['kind'] == 'waiting' for item in open_items),
            'unseen': sum(not item['seen'] for item in open_items),
            'open_agent': sum(item['source'] == 'agent' for item in open_items),
            'open_app': sum(item['source'] == 'app' for item in open_items)}


def _revision(items):
    return hashlib.sha1(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()[:16]


def feed(archived=False, now=None):
    """The response body of GET /api/notifications."""
    with _LOCK:
        if now is None and _CACHE['value'] and time.time() - _CACHE['at'] < CACHE_SECONDS:
            items, warnings = _CACHE['value']
        else:
            items, _, warnings = _build(time.time() if now is None else now)
            if now is None:
                _CACHE.update(at=time.time(), value=(items, warnings))
    shown = items if archived else [item for item in items if item['state'] != 'archived']
    return {'ok': True, 'revision': _revision(items), 'counts': _counts(items), 'items': shown,
            'settings': notices.settings(_path()), 'warnings': warnings}


# ---------------------------------------------------------------- actions
def _error(message, code, status=400):
    return {'ok': False, 'error': message, 'code': code}, status


def _result(id, now, **extra):
    items, _, _ = _build(now)
    return dict({'ok': True, 'item': next((i for i in items if i['id'] == id), None),
                 'counts': _counts(items), 'revision': _revision(items)}, **extra)


def _mark(id, item, now, **fields):
    def change(feed):
        entry = feed.setdefault(id, {'first_seen': item['at'] if id.startswith('signedout:') else now})
        entry.update(fields)
        if 'archived_at' in fields or 'answered' in fields:
            entry['snapshot'] = {k: v for k, v in item.items() if k not in ('state', 'answer', 'seen', 'shown', 'debug', 'debuggable')}
    notices.feed_update(_path(), change, now)


def _answer(id, item, route, body, now):
    choice, text = body.get('choice'), body.get('text')
    choice = choice if isinstance(choice, str) and choice else ''
    text = text.strip() if isinstance(text, str) else ''
    if not choice and not text:
        return _error('Choose an option or write an answer.', 'empty_answer')
    if choice and item['options'] and choice not in item['options']:
        return _error('That is not one of the options.', 'bad_choice')
    if choice and not item['options']:
        return _error('This item has no options.', 'bad_choice')
    if text and not item['allow_text']:
        return _error('This item does not take typed text.', 'text_not_allowed', 409)
    answer = {'choice': choice, 'text': text, 'at': now}
    kind, extra = route['type'], {}
    if kind == 'broker':
        control = {'id': route['run_id'], 'action': 'answer', 'request_id': route['request_id']}
        if route['kind'] == 'approval':
            if not choice:
                return _error('Choose Allow or Deny.', 'empty_answer')
            control['decision'] = route['decisions'][choice]
        elif route['question'] is None:
            return _error('This request has several questions. Answer it in the chat.', 'multi_question', 409)
        else:
            control['answers'] = {route['question']: text or choice}
        result = _control(control)
        if isinstance(result, tuple):
            return result
    elif kind == 'mdec':
        if item['state'] == 'answered' and not body.get('replace'):
            return _error('Already answered; pass replace to change it.', 'already_answered', 409)
        if route['card'] and not choice:
            return _error('A verification card needs one of its options.', 'empty_answer')
        root, _ = _monitor()
        try:
            _orch_store(root).save_response(route['thread'], 'decision', route['decision'],
                                            {'choice': choice, 'text': text}, data=root / 'data')
        except (OSError, ValueError) as error:
            return _error(f'The monitor did not take the answer: {error}', 'monitor_write_failed', 502)
    elif kind == 'mtext':
        key = route.get('chat_key')
        if not key:
            return _error('This item names no Library chat; use its link.', 'text_not_allowed', 409)
        if not text or choice:
            return _error('Write the message to send.', 'empty_answer')
        if _running(key):
            return _error('That chat is running; answer inside it.', 'chat_running', 409)
        result = _create_run({'key': key, 'message': text, 'request_id': 'notification-' + uuid.uuid4().hex})
        if isinstance(result, tuple):
            return result
        extra['run_id'] = (result.get('run') or {}).get('id')
    elif kind == 'notice' and route.get('recover'):
        recover = route['recover']
        try:
            result = _recover(dict(recover['ref'], **recover['actions'][choice]))
        except Exception as error:
            import broker
            if isinstance(error, broker.SessionBusy):
                return _error('That chat is running; answer inside it.', 'chat_running', 409)
            return _error(str(error) or 'Recovery failed.', 'recovery_failed', 409)
        if recover['actions'][choice]['action'] == 'login':
            return _result(id, now, login=result.get('login'))  # stays open: the Retry option appears once signed in
        extra['run_id'] = (result.get('run') or {}).get('id')
    elif kind == 'signedout':
        if choice == 'Not now':
            _mark(id, item, now, archived_at=now)
            return _result(id, now)
        try:
            extra['login'] = _start_login(route['account_id'])
        except ValueError as error:
            return _error(str(error), 'unknown_account', 404)
        except RuntimeError as error:
            return _error(str(error), 'login_failed', 502)
    else:
        return _error('This item takes no answer.', 'not_answerable', 409)
    _mark(id, item, now, answered=answer)
    return _result(id, now, **extra)


def act(body, now=None):
    """The body of POST /api/notifications/act: ({...}, status) on error, else a dict."""
    now = time.time() if now is None else now
    action = body.get('action')
    if action not in ('answer', 'archive', 'seen', 'shown', 'debug'):
        return _error('action must be answer, archive, seen, shown or debug', 'bad_action')
    ids = body.get('ids') if isinstance(body.get('ids'), list) else [body.get('id')]
    ids = [i for i in ids if isinstance(i, str) and i]
    if not ids or action in ('answer', 'debug') and len(ids) != 1:
        return _error('id is required', 'bad_request')
    with _LOCK:
        items, routes, _ = _build(now)
        found = {item['id']: item for item in items}
        missing = [i for i in ids if i not in found]
        if missing and action in ('answer', 'debug'):
            return _error('This item is gone: it was answered elsewhere or closed.', 'not_found', 404)
        ids = [i for i in ids if i in found]
        result = _act(action, ids, found, routes, body, now)
        _CACHE['value'] = None
        return result


def _debug(id, item, route, now):
    if not item.get('debuggable'):
        return _error('This item cannot be debugged.', 'not_debuggable', 400)
    if (item.get('debug') or {}).get('state') in debug_agent.ACTIVE:
        return {'ok': False, 'error': 'debug_running', 'code': 'debug_running'}, 409
    entry, error = debug_agent.dispatch(item, route, _data(), now)
    if error:
        return _error(*error)
    notices.feed_update(_path(), lambda feed: feed.setdefault(id, {'first_seen': now}).update(debug=entry), now)
    return _result(id, now)


def _act(action, ids, found, routes, body, now):
    if action == 'debug':
        return _debug(ids[0], found[ids[0]], routes[ids[0]], now)
    if action == 'answer':
        id = ids[0]
        item = found[id]
        if item['state'] == 'archived' or item['state'] == 'answered' and routes[id]['type'] not in ('signedout', 'mdec'):
            return _error('This item is already closed.', 'item_closed', 409)
        return _answer(id, item, routes[id], body, now)
    claimed, keys = [], [routes[i]['key'] for i in ids if routes[i]['type'] == 'notice']
    others = [i for i in ids if routes[i]['type'] != 'notice']
    if keys:
        notice = {'seen': 'read', 'archive': 'archive', 'shown': 'shown'}[action]
        notices.act(_path(), {'action': notice, 'keys': keys, 'claimant': body.get('claimant')}, now)
        claimed += ['notice:' + k for k in keys]
    claimant = body.get('claimant') if isinstance(body.get('claimant'), str) and body.get('claimant') else None

    def change(feed):
        for id in others:
            item = found[id]
            entry = feed.get(id)
            if action == 'shown':
                if item['state'] != 'open' or entry and entry.get('shown_at') \
                        and not (claimant and entry.get('claimant') == claimant):
                    continue
                entry = feed.setdefault(id, {'first_seen': now})
                entry.setdefault('shown_at', now)
                if claimant:
                    entry['claimant'] = claimant
                claimed.append(id)
                continue
            entry = feed.setdefault(id, {'first_seen': now})
            entry.setdefault('seen_at', now)
            if action == 'archive':
                entry.setdefault('archived_at', now)
                entry['snapshot'] = {k: v for k, v in item.items() if k not in ('state', 'answer', 'seen', 'shown', 'debug', 'debuggable')}
    if others:
        notices.feed_update(_path(), change, now)
    if action == 'shown':
        return {'ok': True, 'claimed': claimed, 'duration_seconds': notices.settings(_path())['duration_seconds']}
    return _result(ids[0], now) if len(ids) == 1 else dict(_result(ids[0], now), item=None)


# ---------------------------------------------------------------- routes
def _get(req, query, body):
    return feed(archived=query.get('archived') in ('1', 'true'))


def _post(req, query, body):
    return act(body or {})


ROUTES = {
    ('GET', '/api/notifications'): _get,
    ('POST', '/api/notifications/act'): _post,
}
