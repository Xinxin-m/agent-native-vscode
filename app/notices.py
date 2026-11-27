"""Durable notification inbox, archive and incident deduplication."""
from functools import lru_cache
import datetime
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid

RESET_TOLERANCE = 3600
KEEP_SECONDS = 30 * 86400
DEFAULT_SETTINGS = {'usage': True, 'signin': True, 'duration_seconds': 4}
_LOCK = threading.RLock()


def _epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (value / 1000 if value >= 1e12 else value) if math.isfinite(value) else None
    try:
        stamp = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _read(path):
    try:
        with open(path) as fh:
            value = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError('Invalid notice store')
    return value


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.notices-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(value, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


USAGE_KINDS = ('usage_high', 'limit_reached', 'rate_limited', 'account_switch', 'pool_usage', 'pool_switch', 'provider_switch')
UNKNOWN_RESET_SECONDS = 6 * 3600


def category(warning):
    return 'usage' if warning.get('kind') in USAGE_KINDS else 'signin'


def reset_text(at, now=None):
    """A reset time as she reads it: "3:40 PM" today, "Sun 3:40 PM" within a week, else the date."""
    now = time.time() if now is None else now
    stamp, today = time.localtime(at), time.localtime(now)
    clock = time.strftime('%-I:%M %p', stamp)
    if stamp[:3] == today[:3]:
        return clock
    return time.strftime('%a ', stamp) + clock if at - now < 6 * 86400 else time.strftime('%b %-d ', stamp) + clock


def _family(account_id, warning):
    if category(warning) == 'signin':
        return f'{account_id}:login'
    if warning.get('kind') == 'rate_limited':
        return f'{account_id}:rate'
    return f"{account_id}:usage:{warning.get('window') or 'unknown'}"


def _family_of(record):
    family = record.get('family', '')
    # Existing stores kept a separate record per escalation threshold.
    return re.sub(r':(?:90|limit|expired|expiring)$', '', family)


def _window(failure, account, at, now):
    if failure.get('window'):
        return failure['window'], None
    raw = str(failure.get('raw') or '').lower()
    model = str(failure.get('model') or '').lower()
    tier = next((tier for tier in ('fable', 'opus', 'sonnet', 'haiku')
                 if tier in raw or tier in model), '')
    credits = 'workspace credit' in raw
    weekly = 'weekly' in raw or '7-day' in raw
    session = bool(re.search(r'5.hours?|session limit', raw))
    candidates = []
    for window in (account or {}).get('windows') or []:
        kind = window.get('kind')
        if not kind or (credits and kind != 'credits'):
            continue
        if (weekly and not kind.startswith('7d')) or (session and kind != '5h'):
            continue
        if '_' in kind and tier and kind.startswith('7d_') and kind != '7d_' + tier:
            continue
        reset = _epoch(window.get('resets_at'))
        if reset is not None and reset <= now:
            continue
        distance = abs(reset - at) if reset is not None and at is not None else None
        if at is not None and (distance is None or distance > RESET_TOLERANCE):
            continue
        percent = window.get('percent')
        high = isinstance(percent, (int, float)) and percent >= 90
        if at is None and not high:
            continue
        candidates.append(((not high, kind != '7d_' + tier, distance or 0), kind, reset))
    candidates.sort(key=lambda row: row[0])
    if candidates and (len(candidates) == 1 or candidates[0][0] < candidates[1][0]):
        return candidates[0][1:]
    return ('credits' if credits else '5h' if session else
            '7d_' + tier if weekly and tier else '7d' if weekly else None), None


def _incident(records, account_id, warning, now, observed=None):
    family = _family(account_id, warning)
    at = _epoch(warning.get('resets_at') or warning.get('expires_at'))
    is_signin = category(warning) == 'signin'
    is_rate = warning.get('kind') == 'rate_limited'
    compatible = []
    for key, record in records.items():
        if record.get('alias'):
            continue
        old_family = _family_of(record)
        same = old_family == family
        unknown_window = old_family.startswith(f'{account_id}:usage:') and family.startswith(f'{account_id}:usage:') and (
            old_family.endswith(':unknown') or old_family.endswith(':None') or family.endswith(':unknown'))
        if not (same or unknown_window):
            continue
        closed, old_at = record.get('resolved_at'), record.get('at')
        if closed is not None:
            if observed is not None and observed <= closed:
                return None
            if observed is None and is_signin and at is not None and old_at is not None and abs(old_at - at) <= 60:
                return key
            continue
        if is_rate and old_at is None and now - record['first_seen'] >= 600:
            continue
        if old_at is not None and at is not None:
            # Expiry identifies a credential. Quota resets can drift by up to an hour.
            if abs(old_at - at) > (60 if is_signin else RESET_TOLERANCE):
                continue
        elif old_at is not None and not is_signin and old_at <= now:
            continue
        elif old_at is None and at is not None and not is_signin and at <= now:
            continue
        compatible.append((key, record, same, old_at))
    exact = [match for match in compatible if match[2] and match[3] is not None and at is not None]
    if exact:
        # Keep an earlier dismissal even when a pre-upgrade record used another threshold.
        primary = min(exact, key=lambda match: abs(match[3] - at))
        exact = [match for match in exact if abs(match[3] - primary[3]) <= (60 if is_signin else RESET_TOLERANCE)]
        matches = exact + [match for match in compatible if match[3] is None]
    elif at is None and not is_signin:
        live_matches = [match for match in compatible if match[3] is not None]
        matches = [max(live_matches, key=lambda match: match[1]['first_seen'])] if live_matches else compatible
        if live_matches and len({_family_of(match[1]) for match in live_matches}) > 1:
            matches = [match for match in compatible if match[2] and match[3] is None]
    else:
        matches = compatible
    if not matches:
        key = uuid.uuid4().hex
        records[key] = {'family': family, 'at': at, 'first_seen': now, 'account_id': account_id,
                        'kind': warning['kind'], 'message': warning.get('message')}
        return key
    key, record, _, old_at = min(matches, key=lambda match: match[1]['first_seen'])
    if old_at is None and at is not None:
        record['at'] = at
    if not family.endswith(':unknown'):
        record['family'] = family
    if warning.get('kind') in ('usage_high', 'login_expiring') or not record.get('message'):
        record['message'] = warning.get('message')
    for alias, other, _, _ in matches:
        if alias == key:
            continue
        for field in ('shown_at', 'dismissed_at', 'read_at', 'archived_at'):
            if field in other and field not in record:
                record[field] = other[field]
                if field == 'shown_at' and 'claimant' in other:
                    record['claimant'] = other['claimant']
        if (not (record.get('chat_key') or record.get('run_id'))
                or other.get('failure_at', 0) > record.get('failure_at', 0)):
            for field in ('chat_key', 'run_id', 'official', 'failure_at'):
                record.pop(field, None)
                if field in other:
                    record[field] = other[field]
        if record.get('at') is None and other.get('at') is not None:
            record['at'] = other['at']
        other['alias'] = key
    return key


def _prune(records, now):
    for key, record in list(records.items()):
        if record.get('archived_at') or record.get('dismissed_at'):
            continue
        end = record.get('resolved_at')
        if end is None and category(record) == 'usage':
            end = record.get('at')
        if end is None and record.get('kind') == 'other':
            end = record['first_seen']
        if (end is not None and now - end > KEEP_SECONDS
                or record.get('kind') == 'rate_limited' and now - record['first_seen'] > KEEP_SECONDS):
            del records[key]


def settings(path):
    return {**DEFAULT_SETTINGS, **(_read(path).get('settings') or {})}


def annotate(accounts, path, now=None):
    """Annotate account warnings with their incident and popup eligibility."""
    if not any(a.get('warnings') or a.get('windows') for a in accounts):
        return accounts
    now = time.time() if now is None else now
    with _LOCK:
        state = _read(path)
        records = state.setdefault('notices', {})
        before = json.dumps(records, sort_keys=True)
        prefs = {**DEFAULT_SETTINGS, **(state.get('settings') or {})}
        _prune(records, now)
        for account in accounts:
            # A fresh observed drop ends an undated, previously high window. Retain its
            # identity so a replayed failed run cannot resurrect it.
            fetched = _epoch(account.get('fetched_at'))
            if fetched is not None:
                for window in account.get('windows') or []:
                    percent = window.get('percent')
                    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or percent >= 90:
                        continue
                    family = _family(account['id'], {'kind': 'usage_high', 'window': window.get('kind')})
                    for record in records.values():
                        if (_family_of(record) == family and record.get('at') is None
                                and record.get('first_seen', now) < fetched and 'resolved_at' not in record):
                            record['resolved_at'] = fetched
            for warning in account.get('warnings') or []:
                key = _incident(records, account['id'], warning, now)
                record = records[key]
                warning.update(key=key, shown='shown_at' in record,
                               dismissed='dismissed_at' in record or 'archived_at' in record or 'resolved_at' in record,
                               muted=not prefs[category(warning)])
        if before != json.dumps(records, sort_keys=True):
            _write(path, state)
    return accounts


def resolve_signin(path, account_id, now=None):
    """Close a sign-in incident after a verified interactive login."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _read(path)
        matches = [r for r in state.get('notices', {}).values() if _family_of(r) == f'{account_id}:login'
                   and 'resolved_at' not in r]
        if matches:
            for record in matches:
                record['resolved_at'] = now
            _write(path, state)


def _archived(record):
    return record.get('archived_at', record.get('dismissed_at'))


def _visible(record, prefs, now):
    return not record.get('alias') and (_archived(record) is not None or (
        record.get('first_seen', 0) >= now - KEEP_SECONDS
        and (record.get('kind') == 'other' or prefs[category(record)])))


@lru_cache(maxsize=8)
def _snapshot(path, revision):
    state = _read(path)
    return state.get('notices', {}), {**DEFAULT_SETTINGS, **(state.get('settings') or {})}


def _state(path, now):
    try:
        revision = str(os.stat(path).st_mtime_ns)
    except FileNotFoundError:
        revision = '0'
    records, prefs = _snapshot(os.fspath(path), revision)
    unread = sum(_visible(r, prefs, now) and _archived(r) is None and 'read_at' not in r for r in records.values())
    return records, prefs, {'revision': revision, 'unread': unread}


def summary(path, now=None):
    with _LOCK:
        try:
            return _state(path, time.time() if now is None else now)[2]
        except (OSError, ValueError, KeyError):
            return None


def listing(path, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        records, prefs, center = _state(path, now)
        rows = []
        for key, record in records.items():
            if _visible(record, prefs, now):
                row = {field: value for field, value in record.items() if field != 'claimant'}
                if _archived(record) is not None:
                    row.setdefault('archived_at', _archived(record))
                    row.setdefault('read_at', _archived(record))
                rows.append(dict(row, key=key))
        rows.sort(key=lambda row: row['first_seen'], reverse=True)
        return {'ok': True, 'settings': prefs, 'notices': rows, 'notice_center': center}


def _failure_incident(records, failure, account, now):
    kind = failure.get('kind')
    if kind not in ('limit_reached', 'rate_limited', 'auth_expired', 'not_logged_in', 'other'):
        return None
    provider = failure.get('provider')
    if provider not in ('claude', 'codex'):
        return None
    account_id = (account or {}).get('id') or failure.get('account_id') or f'unattributed-{provider}'
    if not isinstance(account_id, str) or len(account_id) > 250:
        return None
    at = _epoch(failure.get('resets_at'))
    observed = _epoch(failure.get('observed_at'))
    if (kind in ('rate_limited', 'limit_reached') and at is not None and at <= now
            or observed is not None and now - observed > (600 if kind == 'rate_limited' else 86400)):
        return None
    if kind == 'other':
        run_id = failure.get('run_id')
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', run_id):
            return None
        key = 'run:' + run_id
        records.setdefault(key, {'kind': kind, 'first_seen': observed or now,
                                 'account_id': account_id, 'message': f'{provider.title()} run failed.'})
        return key
    warning = {'kind': kind}
    who = (account or {}).get('label') or account_id
    if kind == 'limit_reached':
        window, reset = _window(failure, account, at, now)
        warning.update(window=window, resets_at=at if at is not None else reset,
                       message=f'{provider.title()} usage limit reached for {who}.')
    elif kind == 'rate_limited':
        warning.update(resets_at=at, message=f'{provider.title()} is temporarily rate limited.')
    else:
        warning.update(expires_at=(account or {}).get('loginExpiresAt'),
                       message=f'{provider.title()} sign-in needs attention for {who}.')
    return _incident(records, account_id, warning, now, observed)


def note(path, key, message, now=None):
    """Add a center-only notice once per key; archiving it keeps it quiet."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _read(path)
        records = state.setdefault('notices', {})
        if key not in records:
            records[key] = {'kind': 'other', 'first_seen': now, 'message': message}
            _write(path, state)
    return key


def announce(path, key, kind, message, at=None, account_id=None, now=None, **fields):
    """Add a quota notice once per key, so one account and window gives one notice; True when new.
    It closes at its reset time; without one it closes after UNKNOWN_RESET_SECONDS. fields (run_id,
    chat_key, provider) let a notice offer the chat's recovery actions."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _read(path)
        records = state.setdefault('notices', {})
        if key in records:
            return False
        records[key] = {'kind': kind, 'first_seen': now, 'message': message,
                        'at': at if at is not None else now + UNKNOWN_RESET_SECONDS, **fields}
        if account_id:
            records[key]['account_id'] = account_id
        _write(path, state)
    return True


def act(path, body, now=None, account=None):
    """Collect incidents and update shared notification state."""
    now = time.time() if now is None else now
    action = body.get('action')
    with _LOCK:
        state = _read(path)
        records = state.setdefault('notices', {})
        prefs = {**DEFAULT_SETTINGS, **(state.get('settings') or {})}
        if action == 'settings':
            for name in ('usage', 'signin'):
                if isinstance(body.get(name), bool):
                    prefs[name] = body[name]
            duration = body.get('duration_seconds')
            if type(duration) is int and 0 <= duration <= 60:
                prefs['duration_seconds'] = duration
            state['settings'] = prefs
            _write(path, state)
            return listing(path)
        if action not in ('collect', 'read', 'archive', 'shown', 'dismiss'):
            raise ValueError('Unsupported notice action')
        before = json.dumps(records, sort_keys=True)
        keys = body.get('keys') if isinstance(body.get('keys'), list) else [body.get('key')]
        failure = body.get('failure')
        if action in ('collect', 'shown') and isinstance(failure, dict):
            key = _failure_incident(records, failure, account, now)
            keys = [key]
            if key and action == 'collect':
                record = records[key]
                identity = {field: value for field in ('run_id', 'chat_key')
                            if isinstance(value := failure.get(field), str)
                            and re.fullmatch(r'[A-Za-z0-9_-]{1,220}', value)}
                observed = _epoch(failure.get('observed_at'))
                if identity and (not (record.get('run_id') or record.get('chat_key'))
                                 or observed is not None and observed > record.get('failure_at', 0)):
                    for field in ('run_id', 'chat_key', 'official'):
                        record.pop(field, None)
                    record.update(identity)
                    if observed is not None:
                        record['failure_at'] = observed
                    if failure.get('official') is True:
                        record['official'] = True
        if action == 'collect':
            if before != json.dumps(records, sort_keys=True):
                _write(path, state)
            return {'ok': True}

        claimant = body.get('claimant')
        claimant = claimant if isinstance(claimant, str) and 0 < len(claimant) <= 128 else None
        canonical = dict.fromkeys(k for k in keys if isinstance(k, str) and k in records)
        claimed = []
        for key in canonical:
            record = records[key]
            while record.get('alias') in records:
                key = record['alias']
                record = records[key]
            if action in ('read', 'archive'):
                record.setdefault('read_at', now)
                if action == 'archive':
                    record.setdefault('archived_at', _archived(record) or now)
                continue
            if _archived(record) is not None or key in claimed:
                continue
            if action == 'shown':
                expired = category(record) == 'usage' and record.get('at') is not None and record['at'] <= now
                if not prefs[category(record)] or 'resolved_at' in record or expired:
                    continue
                if 'shown_at' in record and not (claimant and record.get('claimant') == claimant):
                    continue
                record.setdefault('shown_at', now)
                if claimant:
                    record['claimant'] = claimant
            else:
                record['dismissed_at'] = now
                record.setdefault('shown_at', now)
            claimed.append(key)
        if before != json.dumps(records, sort_keys=True):
            _write(path, state)
        if action in ('read', 'archive'):
            return listing(path, now)
        return {'ok': True, 'claimed': claimed, 'duration_seconds': prefs['duration_seconds']}


# ---- feed state: read, seen, archive and answer marks of the unified feed (notifications.py)
# Stored beside the notices in the same file under "feed", keyed by feed item id. Old
# readers ignore the key, and annotate/act keep it because they rewrite the whole state.

@lru_cache(maxsize=8)
def _feed_snapshot(path, revision):
    return _read(path).get('feed', {})


def feed_state(path):
    try:
        revision = str(os.stat(path).st_mtime_ns)
    except FileNotFoundError:
        revision = '0'
    try:
        return _feed_snapshot(os.fspath(path), revision)
    except (OSError, ValueError):
        return {}


def feed_update(path, change, now=None):
    """Run change(feed_dict) under the store lock and save; old entries are dropped."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _read(path)
        feed = state.setdefault('feed', {})
        change(feed)
        for key, entry in list(feed.items()):
            marks = [entry.get(f) for f in ('archived_at', 'seen_at', 'shown_at') if entry.get(f)]
            marks.append((entry.get('answered') or {}).get('at'))
            if now - max([m for m in marks if m] or [now]) > KEEP_SECONDS:
                del feed[key]
        _write(path, state)
