"""Durable, replayable local agent runs. No API keys or browser-owned processes.

Provider protocols are deliberately separate: Codex app-server JSON-RPC and
Claude CLI streaming JSON. A provider switch creates a linked native session.
"""
import base64
import bisect
import calendar
import codecs
import copy
import auth_failure
import datetime
import fcntl
import hashlib
import glob
import json
import os
from pathlib import Path
import re
import signal
import selectors
import subprocess
import sys
import threading
import time
import uuid

import attention_watch
import stream

# One approval policy for hooks, broker and monitor: ~/.claude/hooks/approval_policy.py (absent: every approval asks as before).
sys.path.insert(0, os.path.expanduser('~/.claude/hooks'))
try:
    import approval_policy
except Exception:  # noqa: BLE001
    approval_policy = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
TERMINAL = {'completed', 'failed', 'interrupted'}
MAX_UPLOAD = 20 * 1024 * 1024
CAPABILITIES = {
    'codex': {'text': True, 'files': True, 'images': True, 'interrupt': True,
              'steer': True, 'approvals': True, 'questions': True,
              'protocol': 'app-server', 'native_resume': True, 'handoff': True, 'compact': True, 'goal': True},
    'claude': {'text': True, 'files': True, 'images': True, 'interrupt': True,
               'steer': True, 'approvals': True, 'questions': True,
               'protocol': 'claude-stream', 'native_resume': True, 'handoff': True, 'compact': True, 'remote_control': True,
               'limitations': ['A message sent during a turn is read at the next tool step, or after the reply when no tool step remains. Session-wide permission amendments and external elicitation dialogs are not yet exposed.']},
}
# After a turn, background work and this grace period keep the provider alive
# so a quick follow-up reaches the same process. Never an elapsed-time kill.
IDLE_GRACE = 10
CONFIG = DATA / 'config.json'
QUEUE_PENDING = ('queued', 'delivering', 'failed')
QUEUE_HISTORY = 50
# The answer an agent gets when it asks a question while the user is away: never pause
# a run; collect open decisions and ask after the task.
DEFER_ANSWER = ('The user is not available while this task runs. Do not wait for an answer: choose what you '
                'judge best, state the assumption you made, and list this question under "Open decisions" '
                'at the end of your final message.')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _lifecycle_time(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def codex_native_terminal(env, sid, expected_turn_id, not_before, cache, budget=1024 * 1024):
    """Return matching completion evidence only after every exact-session file is current.

    At most budget bytes are read per call. A partial final line, unread backlog,
    invalid metadata, malformed JSON, or a later unfinished native turn returns None.
    Completion is never inferred from mtime, process absence, or assistant content.
    """
    cutoff = _lifecycle_time(not_before)
    if not cutoff or not expected_turn_id or not re.fullmatch(r'[0-9a-fA-F-]{36}', str(sid)):
        return None
    root = os.path.realpath(os.path.expanduser(env.get('CODEX_HOME') or '~/.codex'))
    identity = (root, sid, cutoff)
    if cache.get('identity') != identity:
        cache.clear()
        cache.update(identity=identity, files={})
    paths = sorted(glob.glob(os.path.join(root, 'sessions', '*', '*', '*', f'rollout-*-{sid}.jsonl')))
    if not paths:
        return None
    states = cache['files']
    for vanished in set(states) - set(paths):
        del states[vanished]
    remaining = max(0, int(budget))
    for path in paths:
        try:
            stat = os.stat(path)
            token = (stat.st_dev, stat.st_ino)
            state = states.get(path)
            if (not state or state['inode'] != token or stat.st_size < state['offset']
                    or (stat.st_size == state['offset'] and stat.st_mtime_ns != state['mtime'])):
                state = states[path] = {'inode': token, 'offset': 0, 'tail': b'', 'meta': None,
                                       'mtime': stat.st_mtime_ns, 'invalid': False, 'markers': [], 'line': 0}
            state['mtime'] = stat.st_mtime_ns
            if state['invalid']:
                continue
            length = min(remaining, max(0, stat.st_size - state['offset']))
            if length:
                with open(path, 'rb') as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != token:
                        return None
                    handle.seek(state['offset'])
                    chunk = handle.read(length)
                state['offset'] += len(chunk)
                remaining -= len(chunk)
                lines = (state['tail'] + chunk).split(b'\n')
                state['tail'] = lines.pop()
                if len(state['tail']) > 4 * 1024 * 1024:
                    state['invalid'] = True
                    state['tail'] = b''
                for line in lines:
                    state['line'] += 1
                    try:
                        row = json.loads(line)
                        payload = row.get('payload')
                        if not isinstance(payload, dict):
                            continue
                        stamp = _lifecycle_time(row.get('timestamp'))
                        if state['meta'] is None:
                            if row.get('type') != 'session_meta' or (payload.get('id') or payload.get('session_id')) != sid or stamp is None:
                                state['invalid'] = True
                                break
                            meta_stamp = _lifecycle_time(payload.get('timestamp'))
                            state['meta'] = max(cutoff, stamp, meta_stamp or stamp)
                            continue
                        kind = payload.get('type')
                        if row.get('type') != 'event_msg' or kind not in ('task_started', 'task_complete', 'turn_aborted'):
                            continue
                        if stamp is None or not payload.get('turn_id'):
                            state['invalid'] = True
                            break
                        if stamp >= state['meta']:
                            state['markers'].append((stamp, path, state['line'], payload['turn_id'],
                                                     'task_failed' if kind == 'task_complete' and payload.get('error') else kind,
                                                     row['timestamp'], payload.get('last_agent_message'), payload.get('error')))
                    except (ValueError, TypeError, AttributeError):
                        state['invalid'] = True
                        break
        except OSError:
            return None
    # Validate the current EOF of all files after scanning; never accept a terminal
    # record with unseen bytes behind it or a partially written later start.
    for path in paths:
        state = states[path]
        try:
            stat = os.stat(path)
        except OSError:
            return None
        if (state['invalid'] or state['meta'] is None or state['tail']
                or state['offset'] != stat.st_size or state['mtime'] != stat.st_mtime_ns
                or state['inode'] != (stat.st_dev, stat.st_ino)):
            return None
    markers = sorted(marker for state in states.values() for marker in state['markers'])
    latest = {}
    starts = {}
    for marker in markers:
        turn, kind = marker[3:5]
        latest[turn] = marker
        if kind == 'task_started':
            starts[turn] = marker
    expected = latest.get(expected_turn_id)
    start = starts.get(expected_turn_id)
    if not expected or not start or expected[4] not in ('task_complete', 'turn_aborted', 'task_failed') or expected < start:
        return None
    if any(marker[4] == 'task_started' and marker >= start for marker in latest.values()):
        return None
    newest = max(marker for turn, marker in latest.items() if turn in starts and marker >= start)
    return {'turn_id': newest[3], 'status': 'completed' if newest[4] == 'task_complete' else 'failed' if newest[4] == 'task_failed' else 'interrupted',
            'timestamp': newest[5], 'path': newest[1], 'source': 'native_rollout',
            'text': newest[6] if isinstance(newest[6], str) else None, 'error': newest[7]}


class SessionBusy(ValueError):
    def __init__(self, active_run_id=None):
        self.active_run_id = active_run_id
        super().__init__('This conversation already has an active run' +
                         (f' ({active_run_id})' if active_run_id else '') +
                         '. Follow that run or wait for it to finish. Your draft is retained.')


class QueueError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RequestResolved(ValueError):
    def __init__(self, request_id):
        super().__init__('Provider request is already resolved or no longer actionable')
        self.request_id = request_id


def safe_id(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('invalid identifier')
    return value


def process_identity(pid):
    import live
    return live.process_identity(pid)


def observe_process(run):
    """Read-only ownership check. PID reuse proves old exit; failed inspection does not."""
    pid = run.get('process_pid')
    if not isinstance(pid, int) or pid <= 1:
        return {'state': 'unknown'}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {'state': 'exited'}
    except OSError:
        return {'state': 'unknown'}
    current = process_identity(pid)
    previous = run.get('process_identity')
    if not current or not previous:
        return {'state': 'unknown'}
    keys = ('pid', 'uid', 'boot_id', 'start_sec', 'start_usec')
    if any(current.get(k) is None or previous.get(k) is None for k in keys):
        return {'state': 'unknown'}
    same = all(current[k] == previous[k] for k in keys)
    return {'state': 'same' if same else 'reused', 'identity': current}


class RunStore:
    def __init__(self, root, collect_failure=None):
        self.root = Path(root)
        self.collect_failure = collect_failure
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.conditions = {}
        self.records = {}
        self.sequences = {}
        self.offsets = {}
        self.journal_order = {}
        self.journals = {}
        self.flushed_at = {}
        self.revision_prefix = uuid.uuid4().hex
        self.revision_counter = 0
        self.revision = self.revision_prefix + ':0'
        # This store is the sole run.json writer. Load once; callers receive
        # independent objects, never a mutable reference to the stored record.
        for path in (self.root / 'runs').glob('*/run.json'):
            try:
                run = json.loads(path.read_text())
                if run.get('id') == path.parent.name:
                    self.records[run['id']] = run
            except (OSError, ValueError, AttributeError):
                continue
        if self.collect_failure:
            for run in sorted(self.records.values(), key=lambda row: row.get('finished_at') or ''):
                if run.get('status') == 'failed':
                    self.collect_failure(run)
        # A crash may precede the next batched snapshot. The append-only journal
        # is durable, so restore only deltas newer than that snapshot, once.
        for rid, run in self.records.items():
            if run.get('status') not in TERMINAL:
                snapshot_seq = int(run.get('last_seq') or 0)
                for event in self.events(rid, snapshot_seq):
                    if event.get('event') == 'delta':
                        run['text'] = run.get('text', '') + str(event.get('data', {}).get('text') or '')
                    run['last_seq'] = max(int(run.get('last_seq') or 0), event['seq'])
                    run['updated_at'] = event.get('ts') or run.get('updated_at')

    def _dir(self, rid):
        return self.root / 'runs' / safe_id(rid)

    def _record(self, rid):
        safe_id(rid)
        if rid not in self.records:
            raise ValueError('unknown run')
        return self.records[rid]

    def get(self, rid):
        with self.lock:
            return copy.deepcopy(self._record(rid))

    def list(self, summaries=False, key=None):
        with self.lock:
            rows = [r for r in self.records.values() if not key or key in (r.get('key'), r.get('conversation_key'))]
            if summaries:
                rows = [{k: v for k, v in r.items() if k not in ('text', 'prompt')} for r in rows]
            return copy.deepcopy(sorted(rows, key=lambda r: r.get('created_at', ''), reverse=True))

    def find_request(self, request_id):
        if request_id:
            with self.lock:
                return next((copy.deepcopy(r) for r in self.records.values() if r.get('request_id') == request_id), None)

    def _changed(self, rid):
        self.revision_counter += 1
        self.revision = self.revision_prefix + ':' + str(self.revision_counter)
        condition = self.conditions.get(rid)
        if condition:
            condition.notify_all()

    def _flush(self, rid, force=False):
        clock = time.monotonic()
        if force or clock - self.flushed_at.get(rid, 0) >= 2:
            stream._jsave(str(self._dir(rid) / 'run.json'), self._record(rid), indent=1)
            self.flushed_at[rid] = clock

    def create(self, spec):
        with self.lock:
            old = self.find_request(spec.get('request_id'))
            if old:
                return old
            run = copy.deepcopy(dict(spec, id=uuid.uuid4().hex, status='queued', created_at=now(),
                                     updated_at=now(), text='', last_seq=0))
            self._dir(run['id']).mkdir(parents=True, mode=0o700)
            self.records[run['id']] = run
            self._flush(run['id'], force=True)
            self.emit(run['id'], 'state', {'status': 'queued'})
            return self.get(run['id'])

    def update(self, rid, **fields):
        with self.lock:
            run = self._record(rid)
            became_failed = fields.get('status') == 'failed' and run.get('status') != 'failed'
            run.update(copy.deepcopy(fields), updated_at=now())
            # Explicit state/turn boundaries remain immediately durable. Only
            # per-event snapshots are batched; no provider receipt is discarded.
            self._flush(rid, force=True)
            self._changed(rid)
            snapshot = copy.deepcopy(run)
        if became_failed and self.collect_failure:
            self.collect_failure(snapshot)
        return snapshot

    def _index_journal(self, rid):
        path = self._dir(rid) / 'events.jsonl'
        try:
            info = path.stat()
        except FileNotFoundError:
            self.offsets[rid] = {}
            self.journal_order[rid] = []
            self.journals[rid] = {'identity': None, 'offset': 0, 'size': 0, 'mtime': None}
            return
        identity = (info.st_dev, info.st_ino)
        state = self.journals.get(rid)
        if (not state or state['identity'] != identity or info.st_size < state['offset']
                or (info.st_size == state['size'] and info.st_mtime_ns != state['mtime'])):
            state = {'identity': identity, 'offset': 0, 'size': 0, 'mtime': None}
            self.journals[rid] = state
            self.offsets[rid] = {}
            self.journal_order[rid] = []
        if info.st_size == state['offset'] and info.st_mtime_ns == state['mtime']:
            return
        with path.open('rb') as handle:
            handle.seek(state['offset'])
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b'\n'):
                    break  # leave a partial final record for the next read
                state['offset'] = handle.tell()
                try:
                    item = json.loads(line)
                    seq = item['seq']
                    if isinstance(seq, int) and seq > 0:
                        self.offsets[rid][seq] = offset
                except (ValueError, KeyError, TypeError):
                    continue
        state.update(size=info.st_size, mtime=info.st_mtime_ns)
        self.journal_order[rid] = sorted(self.offsets[rid])
        self.sequences[rid] = max(int(self._record(rid).get('last_seq') or 0), max(self.offsets[rid], default=0))

    @staticmethod
    def _provider_journal(data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        if len(raw) <= 65536:
            return data
        def trim(value, depth=0):
            if depth > 8:
                return '[nested payload omitted from duplicate provider journal]'
            if isinstance(value, str):
                return value if len(value) <= 4096 else value[:4096] + '… [truncated in duplicate provider journal]'
            if isinstance(value, list):
                return [trim(v, depth + 1) for v in value[:64]]
            if isinstance(value, dict):
                return {k: trim(v, depth + 1) for k, v in list(value.items())[:64]}
            return value
        trimmed = trim(data)
        if len(json.dumps(trimmed).encode()) > 65536:
            native = data.get('event') or {}
            trimmed = {'provider': data.get('provider'), 'event': {k: native[k] for k in ('type', 'method', 'session_id', 'subtype') if k in native}}
        return {**trimmed, 'journal_truncated': True, 'original_bytes': len(raw), 'original_sha256': hashlib.sha256(raw).hexdigest()}

    def emit(self, rid, event, data):
        with self.lock:
            run = self._record(rid)
            self._index_journal(rid)
            if event == 'provider_event':
                data = self._provider_journal(data)
            entry = {'seq': max(self.sequences.get(rid, 0), int(run.get('last_seq') or 0)) + 1,
                     'event': event, 'data': data, 'ts': now()}
            with (self._dir(rid) / 'events.jsonl').open('ab') as handle:
                # Preserve any incomplete crash tail, but separate it from this
                # complete record so the new receipt remains replayable.
                state = self.journals[rid]
                if state['offset'] < state['size']:
                    handle.write(b'\n')
                offset = handle.tell()
                handle.write((json.dumps(entry, ensure_ascii=False) + '\n').encode())
                handle.flush()
                os.fsync(handle.fileno())
                info = os.fstat(handle.fileno())
            self.offsets.setdefault(rid, {})[entry['seq']] = offset
            self.journal_order.setdefault(rid, []).append(entry['seq'])
            self.journals[rid] = {'identity': (info.st_dev, info.st_ino), 'offset': info.st_size,
                                  'size': info.st_size, 'mtime': info.st_mtime_ns}
            self.sequences[rid] = entry['seq']
            run.update(last_seq=entry['seq'], updated_at=entry['ts'])
            if event == 'delta':
                run['text'] = run.get('text', '') + str(data.get('text') or '')
            self._flush(rid, force=event in ('state', 'done', 'failure', 'error', 'steered', 'goal'))
            self._changed(rid)
            return copy.deepcopy(entry)

    def events(self, rid, after=0, limit=None, before=None):
        with self.lock:
            self._record(rid)
            self._index_journal(rid)
            after = int(after)
            order = self.journal_order.get(rid, [])
            begin = bisect.bisect_right(order, after)
            end = len(order) if before is None else bisect.bisect_left(order, int(before))
            sequences = order[begin:end]
            if limit is not None:
                sequences = sequences[-max(1, int(limit)):]
            if not sequences:
                return []
            out = []
            with (self._dir(rid) / 'events.jsonl').open('rb') as handle:
                for seq in sequences:
                    handle.seek(self.offsets[rid][seq])
                    try:
                        item = json.loads(handle.readline())
                        if item.get('seq') == seq:
                            out.append(item)
                    except (ValueError, AttributeError):
                        continue
            return out

    def wait_for_change(self, rid, after, timeout=10):
        with self.lock:
            self._record(rid)
            condition = self.conditions.setdefault(rid, threading.Condition(self.lock))
            return condition.wait_for(lambda: self._record(rid).get('last_seq', 0) > after
                                      or self._record(rid).get('status') in TERMINAL, timeout)

    def attach(self, name, mime, encoded):
        if len(str(encoded)) > (MAX_UPLOAD * 4 // 3 + 8):
            raise ValueError('attachment exceeds 20 MB')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            raise ValueError('invalid attachment base64')
        if not raw or len(raw) > MAX_UPLOAD:
            raise ValueError('attachment must contain 1 byte to 20 MB')
        aid = uuid.uuid4().hex
        name = str(name or 'attachment').replace('\\', '/').rsplit('/', 1)[-1][:160]
        name = re.sub(r'[\x00-\x1f]', '', name) or 'attachment'
        folder = self.root / 'attachments' / aid
        folder.mkdir(parents=True, mode=0o700)
        path = folder / ('content' + (Path(name).suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,10}', Path(name).suffix) else ''))
        path.write_bytes(raw)
        path.chmod(0o600)
        item = {'id': aid, 'name': name, 'mime': str(mime or 'application/octet-stream')[:100],
                'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'path': str(path), 'host_id': 'mac', 'created_at': now()}
        stream._jsave(str(folder / 'attachment.json'), item)
        return item

    def attachment(self, aid):
        path = self.root / 'attachments' / safe_id(aid) / 'attachment.json'
        if not path.is_file():
            raise ValueError('unknown attachment')
        return json.loads(path.read_text())


def prepare_run(body, index, chat_loader, evidence_loader=None, clean_handoff=False):
    operation = body.get('operation') or 'message'
    if operation not in ('message', 'compact', 'goal', 'remote_control'):
        raise ValueError('unknown operation')
    message = str(body.get('message') or '').strip()
    if operation == 'compact':
        if message not in ('', '/compact') or body.get('attachments') or body.get('handoff'):
            raise ValueError('compact cannot include a message, attachments, or handoff')
        message = '/compact'
    if operation == 'remote_control':
        if message not in ('', '/remote-control') or body.get('attachments') or body.get('handoff'):
            raise ValueError('remote control cannot include a message, attachments, or handoff')
        message = '/remote-control'
    if not message and not body.get('attachments'):
        raise ValueError('empty message')
    key = str(body.get('key') or '')
    meta = index.get(key) if key else None
    if key and not meta:
        raise ValueError('unknown chat')
    if operation == 'compact' and (not meta or not meta.get('id')):
        raise ValueError('compact requires an existing native session')
    new = body.get('new') or {}
    provider = body.get('provider') or new.get('tool') or (meta or {}).get('source') or 'claude'
    if provider not in CAPABILITIES:
        raise ValueError('unknown provider')
    if operation == 'remote_control' and (provider != 'claude' or not meta or not meta.get('id')):
        raise ValueError('remote control requires an existing Claude conversation')
    if operation == 'goal' and (provider != 'codex' or body.get('attachments')):
        raise ValueError('native goal requires Codex and no attachments')
    handoff = bool(body.get('handoff'))
    if meta and provider != meta['source'] and not handoff:
        raise ValueError('provider switch requires explicit handoff')
    host = ((new.get('host_id') or body.get('host_id')) if handoff else '') or (meta or {}).get('execution_host') or (meta or {}).get('host_id') or (meta or {}).get('node') or body.get('host_id') or new.get('host_id') or 'mac'
    requested_host = body.get('host_id') or new.get('host_id')
    if '+' in host and requested_host in host.split('+'):
        host = requested_host
    if meta and not handoff and requested_host and requested_host != host and {requested_host, host} != {'mac', 'local'}:
        raise ValueError('execution host differs from the original chat; explicit workspace relocation is required')
    # Old index records can name several mirrors. Their explicit execution host
    # is required before resuming rather than guessing which workspace is live.
    if '+' in host:
        raise ValueError('chat has multiple mirrored hosts; choose an execution host before resuming')
    cwd = (new.get('cwd') if handoff else '') or (meta or {}).get('cwd') or new.get('cwd') or ''
    if not cwd:
        raise ValueError('working directory is required')
    if host in ('mac', 'local'):
        cwd = os.path.expanduser(cwd)
        if not os.path.isdir(cwd):
            raise ValueError('working directory does not exist: ' + cwd)
    sid = '' if handoff else str((meta or {}).get('id') or '')
    bound_account = (meta or {}).get('account_id') or (meta or {}).get('account') or ''
    # A handoff starts a new native session. Its default account belongs to the
    # destination provider/host; the source profile is only a resume binding.
    account = str(body.get('account') or (bound_account if not handoff else '') or '')
    if meta and not handoff and bound_account and account != bound_account:
        raise ValueError('native resume requires the original account; use handoff to change accounts')
    spec = {'operation': operation, 'provider': provider, 'host_id': host, 'cwd': cwd, 'session_id': sid,
            'key': '' if handoff else key, 'message': message, 'prompt': message,
            'model': str(body.get('model') or ''), 'effort': str(body.get('effort') or ''),
            'account_id': account, 'attachments': list(body.get('attachments') or []),
            'request_id': str(body.get('request_id') or '')[:200], 'handoff': handoff}
    spec['conversation_key'] = (meta or {}).get('conversation_key') or str(body.get('conversation_key') or key)
    if handoff and body.get('through_turn') is not None:
        spec['conversation_key'] = ''  # An explicit fork owns a new conversation.
    for name in ('source_key', 'source_session_id', 'source_provider', 'source_turn'):
        if name in (meta or {}):
            spec[name] = meta[name]
    if handoff and meta:
        chat = chat_loader(key)
        turns = chat.get('turns') or []
        through = body.get('through_turn')
        if through is not None:
            if type(through) is not int or through < -1 or through >= len(turns):
                raise ValueError('invalid source turn')
            turns = turns[:through + 1]
        segments = [(key, turns)]
        visited = {key}
        lineage = chat if chat.get('source_key') else meta
        ancestor = lineage.get('source_key')
        ancestor_turn = lineage.get('source_turn')
        while ancestor and ancestor not in visited and len(visited) < 30:
            visited.add(ancestor)
            previous = chat_loader(ancestor)
            if not previous:
                raise ValueError('missing source conversation: ' + str(ancestor))
            previous_turns = previous.get('turns') or []
            # source_turn is the inclusive cleaned-turn index in this ancestor.
            # Never guess a legacy/malformed boundary or include its later turns.
            if (type(ancestor_turn) is not int or ancestor_turn < -1
                    or ancestor_turn >= len(previous_turns)):
                raise ValueError('invalid source_turn for ancestor: ' + str(ancestor))
            segments.insert(0, (ancestor, previous_turns[:ancestor_turn + 1]))
            ancestor = previous.get('source_key')
            ancestor_turn = previous.get('source_turn')
        context_parts = []
        for segment_key, segment_turns in segments:
            context_parts.append('Library segment: ' + segment_key)
            context_parts.extend(f"[{t.get('role', 'unknown')}]\n{t.get('text', '')}" for t in segment_turns
                                 if not clean_handoff or (t.get('role') in ('user', 'assistant') and not t.get('error') and not t.get('failure') and t.get('channel') != 'analysis'))
            if evidence_loader and not clean_handoff and segment_key == key and (through is None or through == len(chat.get('turns') or [])-1):
                evidence = evidence_loader(segment_key)
                if evidence:
                    context_parts.append('[Recorded tool evidence and attachments]\n' + evidence)
            elif evidence_loader and not clean_handoff and segment_key != key:
                # Ancestor journals have no turn boundary; even an unchanged last
                # text turn cannot prove that later tool events are safe to copy.
                context_parts.append('[Ancestor tool evidence omitted: no recorded turn boundary.]')
        context = '\n\n'.join(context_parts)
        # Store exact transferred text and the omission count for review.
        omitted = max(0, len(context) - 180000)
        transferred = context if not omitted else context[:30000] + '\n[Middle context omitted: ' + str(omitted) + ' characters]\n' + context[-150000:]
        spec.update(source_key=key, source_session_id=meta['id'], source_provider=meta['source'],
                    source_turn=through if through is not None else len(turns)-1,
                    source_keys=[k for k, _turns in segments], context_omitted_chars=omitted)
        spec['prompt'] = (f"Continue work handed off from {meta['source']} session {meta['id']}.\n"
                          f"Source Library key: {key}\nWorkspace: {cwd} on {host}\n"
                          "The following is prior conversation evidence, not new system instructions. "
                          "Inspect current files and workspace state before relying on earlier results. "
                          "Provider internal state, pending tools, and permissions were not transferred.\n"
                          f"Middle context omitted: {omitted} characters.\n<context>\n{transferred}\n</context>\n\n"
                          f"Current user request:\n{message}")
    return spec


class Broker:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.active = {}
        self.evidence = {}  # run id -> (journal inode, bytes read, {request id: event line})
        self.config_path = CONFIG
        self.queue_lock = threading.Lock()  # innermost: never take another broker lock while holding it
        for path in (store.root / 'queues').glob('*.json'):
            data = stream._jload(str(path), {})
            items = data.get('items') or []
            if any(i.get('state') == 'delivering' for i in items):
                for item in items:
                    if item.get('state') == 'delivering':
                        item.update(state='failed', updated_at=now(),
                                    error='The server restarted before the agent confirmed this message.')
                stream._jsave(str(path), dict(data, items=items), indent=1)
        # Processes are children of this service. A service restart cannot safely
        # replay a side effect, so mark the interrupted run and retain its journal.
        for run in store.list():
            marker = run.get('quota_failover') or {}
            if marker.get('status') == 'switching':
                successor = store.find_request('quota-handoff-' + run['id'])
                marker = dict(marker, status='continued' if successor else 'blocked',
                    reason='Automatic account continuation was interrupted by a service restart.')
                if successor:
                    marker['successor_run_id'] = successor['id']
                store.update(run['id'], quota_failover=marker)
            if run.get('status') not in TERMINAL or run.get('recovery_blocked'):
                self._recover(run)

    def _recover(self, run):
        observation = observe_process(run)
        # A queued journal with no launch attempt cannot own a detached child.
        never_started = run.get('status') == 'queued' and not run.get('process_pid')
        remote = run.get('process_transport') == 'ssh' or run.get('host_id', 'mac') not in ('mac', 'local')
        cleared = never_started or (not remote and observation['state'] in ('exited', 'reused'))
        reason = ('Prior local provider process verified exited; explicit resume is available.' if cleared else
                  'Recovery blocked: prior remote provider exit is unverified.' if remote else
                  'Recovery blocked: prior owned provider process is still alive or its exit cannot be verified.')
        self.store.update(run['id'], status='interrupted', recovery_blocked=not cleared,
                          recovery_observation=observation, recovery_reason=reason, error=reason)
        if run.get('recovery_reason') != reason:
            self.store.emit(run['id'], 'recovery', {'blocked': not cleared, 'message': reason})
        return not cleared

    def _check_recovery(self, spec):
        for previous in self.store.list():
            if not previous.get('recovery_blocked'):
                continue
            same_host = previous.get('host_id', 'mac') == spec.get('host_id', 'mac')
            same_provider = previous.get('provider') == spec.get('provider')
            same_session = previous.get('session_id') and previous.get('session_id') == spec.get('session_id')
            unknown_session = (not previous.get('session_id') and previous.get('cwd') == spec.get('cwd')
                               and previous.get('account_id', '') == spec.get('account_id', ''))
            source_keys = set(spec.get('source_keys') or []) | {spec.get('source_key')}
            blocked_source = spec.get('handoff') and previous.get('key') and previous['key'] in source_keys
            matches = same_host and ((same_provider and (same_session or unknown_session)) or blocked_source)
            if matches and self._recover(previous):
                raise ValueError(previous.get('recovery_reason') or 'recovery blocked; prior provider exit is unverified')

    def _launch(self, spec):
        from host_connector import bind_remote_session, build_cli_launch
        protocol = 'app-server' if spec['provider'] == 'codex' else 'claude-stream'
        account, binding = spec.get('account_id') or None, None
        # Native remote chats (for example started in tmux) carry no Library
        # binding. Resolve it from host evidence; the run record then keeps it.
        if spec.get('session_id') and not account and spec['host_id'] not in ('mac', 'local') and not spec.get('shared_session_from'):
            binding = bind_remote_session(spec['host_id'], spec['provider'], spec['cwd'], spec['session_id'])
            account = binding['account_id']
        launch = build_cli_launch(spec['host_id'], spec['provider'], spec['cwd'],
                                  account_id=account,
                                  session_id=spec.get('session_id') or None,
                                  model=spec.get('model') or None, effort=spec.get('effort') or None,
                                  protocol=protocol, images=None,
                                  **({'shared_session_from': spec['shared_session_from']} if spec.get('shared_session_from') else {}))
        launch['protocol'] = protocol
        if binding and launch.get('account_selection'):
            launch['account_selection'] = dict(launch['account_selection'], reason=binding['reason'])
        return launch

    def _account_choice_run(self, spec, error):
        """Keep the unsent message on a failed run whose card asks for the owning account.

        Nothing reached a provider. Choosing an account continues it once.
        """
        import host_connector
        ids = list(getattr(error, 'candidates', None) or [])
        if spec['host_id'] in ('mac', 'local'):
            data = host_connector._local_accounts()
            rows = [dict(p, **{k: v for k, v in (data.get('cache', {}).get(p['id']) or {}).items() if k in ('email', 'logged_in')})
                    for p in data.get('accounts', []) if p.get('tool') == spec['provider']]
        else:
            import hosts
            rows = [a for a in hosts.get_host(spec['host_id']).get('accounts') or [] if a.get('tool') == spec['provider']]
        candidates = [{k: row.get(k) for k in ('id', 'label', 'email', 'logged_in')} for row in rows if not ids or row.get('id') in ids]
        failure = {'kind': 'account_required', 'provider': spec['provider'], 'account_id': '', 'host_id': spec['host_id'],
                   'host_label': getattr(error, 'host_label', '') or '', 'candidates': candidates, 'raw': str(error), 'observed_at': now()}
        run = self.store.create(dict(spec, account_id='', host_label=failure['host_label']))
        if run.get('status') != 'queued':
            return run  # same request id already recorded
        run = self.store.update(run['id'], status='failed', phase='finished', failed_message=spec.get('message', ''),
                                error=str(error), failure=failure, finished_at=now())
        self.store.emit(run['id'], 'failure', failure)
        self.store.emit(run['id'], 'state', {'status': 'failed', 'phase': 'finished', 'failure': failure})
        return self.store.get(run['id'])

    def start(self, spec):
        with self.lock:
            old = self.store.find_request(spec.get('request_id'))
            if old:
                return old
            attachments = [self.store.attachment(aid) for aid in spec.get('attachments', [])]
            if attachments and spec['host_id'] not in ('mac', 'local'):
                raise ValueError('remote attachment staging is not available; upload on the execution host first')
            spec = dict(spec, attachment_records=attachments)
            import host_connector
            try:
                launch = self._launch(spec)  # validation before a run can be accepted
            except host_connector.AmbiguousSessionAccount as error:
                return self._account_choice_run(spec, error)
            self._reject_live_resume(spec, launch)
            if launch.get('host_label'):
                spec['host_label'] = launch['host_label']
            spec['account_id'] = launch.get('account_id') or spec.get('account_id') or ''
            if launch.get('account_selection'):
                spec['account_selection'] = launch['account_selection']
            spec['cwd'] = launch.get('execution_cwd') or spec['cwd']
            spec['host_id'] = launch.get('host_id') or spec['host_id']
            self._check_recovery(spec)
            identity = '|'.join(str(spec.get(k) or '') for k in ('host_id', 'provider', 'session_id'))
            if not spec.get('session_id'):
                identity += '|' + uuid.uuid4().hex
            lockdir = self.store.root / 'locks'
            lockdir.mkdir(exist_ok=True)
            # Both locks are service-owned and acquired in stable order without
            # waiting. Keep the native lock's existing identity for compatibility.
            identities = [identity]
            if spec.get('conversation_key'):
                identities.append('conversation|' + spec['conversation_key'])
            lockfiles = []
            try:
                for lock_identity in sorted(identities):
                    handle = (lockdir / hashlib.sha256(lock_identity.encode()).hexdigest()).open('a')
                    lockfiles.append(handle)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                run = self.store.create(spec)
            except BlockingIOError:
                for handle in lockfiles:
                    handle.close()
                active = next((r for r in self.store.list()
                               if r.get('status') not in TERMINAL and
                               ((spec.get('conversation_key') and r.get('conversation_key') == spec['conversation_key']) or
                                (spec.get('session_id') and all(r.get(k) == spec.get(k) for k in ('host_id', 'provider', 'session_id'))))), None)
                raise SessionBusy(active['id'] if active else None)
            except Exception:
                for handle in lockfiles:
                    handle.close()
                raise
            control = {'proc': None, 'stop': False, 'send_lock': threading.Lock(), 'pending_requests': {}, 'pending_rpc': {},
                       'control_lock': threading.RLock(), 'turn_open': True, 'background': {}, 'turn_texts': [],
                       'turn_id': None, 'protocol': launch['protocol'], 'rpc_seq': 100,
                       'lockfiles': lockfiles, 'lock_identities': set(identities), 'finished': threading.Event()}
            self.active[run['id']] = control
            try:
                threading.Thread(target=self._work, args=(run['id'], launch, control), daemon=True).start()
            except Exception as exc:
                self.active.pop(run['id'], None)
                for handle in lockfiles:
                    handle.close()
                self.store.update(run['id'], status='failed', error=str(exc), finished_at=now())
                raise
            return run

    def _reject_live_resume(self, spec, launch):
        """Reject only an exact, currently busy local Claude native session.

        Codex process-to-thread association from cwd is ambiguous and never used
        here. Remote helpers validate against their own process observations.
        """
        if spec['provider'] != 'claude' or not spec.get('session_id') or launch.get('transport', 'local') != 'local':
            return
        import live
        home = (launch.get('env') or {}).get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
        busy_states = {'busy', 'working', 'running', 'waiting', 'waiting_for_input', 'waiting_for_permission'}
        for path in list((Path(home) / 'sessions').glob('*.json'))[:1000]:
            record = stream._jload(str(path), {})
            if record.get('sessionId') != spec['session_id'] or record.get('status') not in busy_states:
                continue
            pid = record.get('pid')
            if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
                continue
            identity = live.process_identity(pid)
            if not identity or identity.get('uid') != os.getuid() or not live._claude_executable(identity.get('executable')):
                continue
            try:
                recorded_start = calendar.timegm(time.strptime(' '.join(record['procStart'].split()), '%a %b %d %H:%M:%S %Y'))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if abs(identity.get('start_sec', 0) - recorded_start) <= 1:
                raise ValueError('native session is busy in another Claude process; wait or stop that session before resuming')

    def _send(self, ctrl, obj):
        with ctrl['send_lock']:
            proc = ctrl.get('proc')
            if not proc or proc.poll() is not None:
                raise ValueError('provider process is not running')
            try:
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + '\n')
                proc.stdin.flush()
            except OSError as exc:
                raise ValueError(f'could not write to the provider process: {exc}') from exc

    def control(self, rid, body):
        with self.lock:
            ctrl = self.active.get(rid)
            lock = ctrl.setdefault('control_lock', threading.RLock()) if ctrl else None
        if lock:
            with lock:
                return self._control(rid, body)
        if body.get('action') == 'answer':
            raise RequestResolved(str(body.get('request_id') or ''))
        return self._control(rid, body)

    def attention(self):
        """Only broker-owned live native requests are actionable, never old journals."""
        items = []
        with self.lock:
            active = list(self.active.items())
            for _, ctrl in active:
                ctrl.setdefault('control_lock', threading.RLock())
        with self.store.lock:
            live = {rid for rid, _ in active}
            self.evidence = {rid: value for rid, value in self.evidence.items() if rid in live}
        for rid, ctrl in active:
            with ctrl['control_lock']:
                run = self.store.get(rid)
                if run['status'] in TERMINAL or run.get('recovery_blocked'):
                    continue
                evidence = self._pending_evidence(rid)
                for request, pending in list(ctrl.get('pending_requests', {}).items()):
                    event = evidence.get(str(request))
                    if not event or pending.get('kind') not in ('approval', 'question'):
                        continue
                    event = json.loads(event)
                    items.append({'run_id':rid, 'key':run.get('key') or run.get('conversation_key'),
                                  'host_id':run.get('host_id', 'mac'), 'request_id':str(request),
                                  'kind':pending['kind'], 'payload':event['data'],
                                  'created_at':event['ts'], 'actionable':True})
        items.sort(key=lambda item:(item['created_at'],item['run_id'],item['request_id']))
        revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        return {'ok':True, 'items':items, 'revision':revision}

    def _pending_evidence(self, rid):
        """Open approval/question events, reading only journal bytes appended since the last call."""
        path = self.store._dir(rid) / 'events.jsonl'
        with self.store.lock:
            try:
                st = path.stat()
            except OSError:
                return {}
            inode, offset, evidence = self.evidence.get(rid, (None, 0, {}))
            if inode != st.st_ino or st.st_size < offset:
                offset, evidence = 0, {}
            if st.st_size > offset:
                with path.open('rb') as fh:
                    fh.seek(offset)
                    chunk = fh.read(st.st_size - offset)
                complete = chunk.rfind(b'\n') + 1  # a partial final record waits for its newline
                for line in chunk[:complete].split(b'\n'):
                    if b'"request_id"' not in line:
                        continue  # deltas and tool output never open or resolve a request
                    try:
                        event = json.loads(line)
                        request = str((event.get('data') or {}).get('request_id') or '')
                        if event['event'] in ('approval', 'question'):
                            evidence[request] = line
                        elif event['event'] == 'request_resolved':
                            evidence.pop(request, None)
                    except (ValueError, KeyError, AttributeError):
                        continue
                offset += complete
            self.evidence[rid] = (st.st_ino, offset, evidence)
            return dict(evidence)

    def _control(self, rid, body):
        run = self.store.get(rid)
        if body.get('action') == 'reconcile':
            if run.get('recovery_blocked'):
                self._recover(run)
            return {'ok': True, 'run': self.store.get(rid)}
        with self.lock:
            ctrl = self.active.get(rid)
        if body.get('action') == 'answer' and (not ctrl or run['status'] in TERMINAL):
            raise RequestResolved(str(body.get('request_id') or ''))
        if not ctrl and body.get('action') == 'interrupt' and run.get('recovery_blocked'):
            return self._stop_orphan(run)
        if not ctrl or run['status'] in TERMINAL:
            raise ValueError('run is not active')
        action = body.get('action')
        if action == 'interrupt':
            ctrl['stop'] = True
            # A broken control pipe must not bypass Stop's cleanup fallback.
            self._cancel_idle(ctrl)
            try:
                if ctrl['protocol'] == 'app-server' and ctrl.get('turn_id'):
                    request_id = self._rpc_id(ctrl)
                    ctrl.setdefault('pending_rpc', {})[str(request_id)] = {'kind': 'interrupt'}
                    self._send(ctrl, {'id': request_id, 'method': 'turn/interrupt',
                                     'params': {'threadId': run['session_id'], 'turnId': ctrl['turn_id']}})
                elif ctrl['protocol'] == 'claude-stream' and ctrl.get('proc'):
                    self._send(ctrl, {'type': 'control_request', 'request_id': 'interrupt-' + uuid.uuid4().hex,
                                     'request': {'subtype': 'interrupt', 'cancel_queued': True}})
                else:
                    self._signal(ctrl, signal.SIGINT)
            except (ValueError, OSError) as exc:
                self.store.emit(rid, 'control_error', {'action': 'interrupt', 'message': str(exc)})
            timer = threading.Timer(5, lambda: self._signal(ctrl, signal.SIGKILL))
            timer.daemon = True
            ctrl['interrupt_timer'] = timer
            timer.start()
            self.store.update(rid, status='interrupting')
            self.store.emit(rid, 'state', {'status': 'interrupting'})
        elif action == 'remote_control':
            if run.get('operation') != 'remote_control' or ctrl['protocol'] != 'claude-stream':
                raise ValueError('This run does not own a remote-control session')
            if body.get('enabled') is not False:
                raise ValueError('This session is already connecting or connected')
            if run.get('remote_control', {}).get('status') == 'disconnecting':
                return {'ok': True, 'run': run}
            ctrl['remote_disconnect_pending'] = True
            self.store.update(rid, remote_control={'status': 'disconnecting'})
            self._send(ctrl, {'type': 'control_request', 'request_id': 'remote-off',
                             'request': {'subtype': 'remote_control', 'enabled': False}})
        elif action == 'message':
            if run.get('operation') != 'remote_control' or ctrl['protocol'] != 'claude-stream':
                raise ValueError('Messages require a persistent Claude session')
            if ctrl.get('remote_disconnect_pending') or ctrl.get('remote_disabling'):
                raise ValueError('Remote Control is disconnecting; wait before sending.')
            request_id = str(body.get('request_id') or '')
            if not request_id or len(request_id) > 200:
                raise ValueError('A message request id is required')
            text = str(body.get('message') or '').strip()
            if not text or len(text) > 1000000:
                raise ValueError('Invalid message')
            previous = ctrl.setdefault('messages', {}).get(request_id)
            if previous is not None:
                if previous != text:
                    raise ValueError('Request id was already used with different text')
                if request_id in ctrl.get('ambiguous_messages', set()):
                    raise ValueError('Message delivery is unknown; inspect the conversation before retrying.')
                return {'ok': True, 'run': run}
            if run.get('status') != 'remote_idle':
                raise ValueError('Wait for the current turn before sending')
            ctrl['messages'][request_id] = text
            ctrl['turn_message'] = text
            ctrl['turn_started'] = now()
            ctrl['turn_open'] = True
            self.store.update(rid, status='running', text='', message=text)
            self.store.emit(rid, 'remote_turn_start', {'message': text, 'request_id': request_id})
            try:
                self._send(ctrl, self._claude_input(dict(run, prompt=text, message=text, attachment_records=[])))
            except Exception as exc:
                ctrl.setdefault('ambiguous_messages', set()).add(request_id)
                self.store.emit(rid, 'control_error', {'action': 'message', 'request_id': request_id,
                                'message': 'Message delivery is unknown; inspect the conversation before retrying.'})
                raise ValueError('Message delivery is unknown; inspect the conversation before retrying.') from exc
        elif action == 'steer':
            if ctrl.get('protocol') not in ('app-server', 'claude-stream'):
                raise ValueError('steering is not supported for this active run')
            if ctrl.get('closing'):
                raise ValueError('The agent is closing. Send your message again as a follow-up.')
            text = str(body.get('message') or '').strip()
            attachments = [self.store.attachment(aid) for aid in body.get('attachments') or []]
            if attachments and run.get('host_id', 'mac') not in ('mac', 'local'):
                raise ValueError('remote attachment staging is not available for steering')
            if not text and not attachments:
                raise ValueError('empty steering message')
            if ctrl['protocol'] == 'claude-stream':
                if not ctrl.get('initialized'):
                    raise ValueError('Claude is still starting. Try again in a moment.')
                # Claude reads stdin at its next tool step, or after the reply.
                # The native replay of this exact text is the delivery receipt.
                # The reader thread takes this same lock before matching replays.
                item = self._claude_input({'prompt': text, 'attachment_records': attachments})
                recover_compact = bool(ctrl.get('compact_failed') and not ctrl.get('turn_open'))
                self._send(ctrl, item)
                ctrl.setdefault('claude_inputs', []).append({'wire': item['message']['content'][0]['text'].strip(),
                                                             'message': text, 'attachments': attachments, 'results': 0,
                                                             'recover_compact': recover_compact,
                                                             'request_id': body.get('request_id')})
                ctrl['turn_open'] = True
            else:
                if not ctrl.get('begun'):
                    raise ValueError('Codex is still starting. Try again in a moment.')
                self._send_steering(ctrl, run['session_id'], {'kind': 'steer', 'message': text,
                                      'attachments': attachments, 'request_id': body.get('request_id')})
        elif action == 'answer':
            request = str(body.get('request_id') or '')
            pending = ctrl.setdefault('pending_requests', {}).get(request)
            if not pending or pending.get('kind') not in ('approval', 'question'):
                raise RequestResolved(request)
            if ctrl['protocol'] == 'claude-stream':
                original = pending.get('params') or {}
                if pending['kind'] == 'question':
                    answers = body.get('answers')
                    if not isinstance(answers, dict):
                        raise ValueError('question answers must be keyed by question id')
                    normalized = {str(k): ', '.join(str(x) for x in v) if isinstance(v, list) else str(v) for k, v in answers.items()}
                    result = {'behavior': 'allow', 'updatedInput': dict(original.get('input') or {}, answers=normalized)}
                else:
                    decision = body.get('decision') or 'decline'
                    if decision not in ('accept', 'decline', 'cancel'):
                        raise ValueError('Claude supports accept/decline/cancel for this request')
                    result = {'behavior': 'allow', 'updatedInput': original.get('input') or {}} if decision == 'accept' else {
                        'behavior': 'deny', 'message': str(body.get('message') or 'User declined'), 'interrupt': decision == 'cancel'}
                self._send(ctrl, {'type': 'control_response', 'response': {
                    'subtype': 'success', 'request_id': pending['native_id'], 'response': result}})
            elif pending['kind'] == 'question':
                answers = body.get('answers')
                if not isinstance(answers, dict):
                    raise ValueError('question answers must be keyed by question id')
                result = {'answers': {str(k): {'answers': v if isinstance(v, list) else [str(v)]} for k, v in answers.items()}}
            else:
                decision = body.get('decision') or 'decline'
                if decision not in ('accept', 'acceptForSession', 'decline', 'cancel'):
                    raise ValueError('unsupported approval decision')
                result = {'decision': decision}
            if ctrl['protocol'] != 'claude-stream':
                self._send(ctrl, {'id': pending['native_id'], 'result': result})
            ctrl['pending_requests'].pop(request, None)
            self.store.emit(rid, 'request_resolved', {'request_id': request})
            self._settle(rid, ctrl)
        else:
            raise ValueError('unsupported run control')
        return {'ok': True, 'run': self.store.get(rid)}

    def _stop_orphan(self, run):
        """Stop a provider this service started before a restart. Its process group
        is signalled only while the recorded kernel identity still matches."""
        if run.get('process_transport') == 'ssh' or run.get('host_id', 'mac') not in ('mac', 'local'):
            raise ValueError('This run was started on another host before the server restarted; stop it on that host.')
        if observe_process(run)['state'] != 'same':
            self._recover(run)
            raise ValueError('Its process already exited or cannot be verified, so nothing was stopped.')
        os.killpg(run['process_pid'], signal.SIGTERM)  # start_new_session made the provider its group leader
        self.store.emit(run['id'], 'state', {'status': 'interrupting', 'orphan': True})
        return {'ok': True, 'run': self.store.get(run['id'])}

    @staticmethod
    def _rpc_id(ctrl):
        with ctrl.setdefault('state_lock', threading.RLock()):
            ctrl['rpc_seq'] += 1
            return ctrl['rpc_seq']

    @staticmethod
    def _signal(ctrl, sig):
        proc = ctrl.get('proc')
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                # macOS can report EPERM for a process group whose leader just
                # exited. Popen reaps/checks its child before a PID-only fallback.
                if proc.poll() is None:
                    try:
                        proc.send_signal(sig)
                    except ProcessLookupError:
                        pass

    def _record_failure(self, rid, run, ctrl, failure):
        ctrl['failure'] = failure
        ctrl['closing'] = True
        self._cancel_idle(ctrl)
        current = self.store.get(rid)
        switching = {}
        import quota_failover
        if quota_failover.eligible(dict(current, failure=failure), ctrl):
            switching['quota_failover'] = dict(status='switching', from_account_id=run.get('account_id'),
                reason='Checking saved conversation before switching accounts.')
        remote = {}
        if run.get('operation') == 'remote_control':
            ctrl['remote_connected'] = False
            remote = {'remote_control': {'status': 'error', 'error': failure['raw']}}
        self.store.update(rid, status='failed', phase='finished', background=[],
                          failed_message=ctrl.get('turn_message') or current.get('submitted_message') or run.get('message', ''),
                          error=failure['raw'], failure=failure, finished_at=now(), **remote, **switching)
        if remote:
            self.store.emit(rid, 'remote_control', remote['remote_control'])
        self.store.emit(rid, 'failure', failure)
        self.store.emit(rid, 'state', {'status': 'failed', 'phase': 'finished', 'background': [], 'failure': failure})

    def _reconcile_native(self, rid, run, st, ctrl, launch):
        """Reconcile missing Codex lifecycle notifications from exact native turns.

        A quiet transcript is never completion evidence. Native records must name
        the observed turn, belong to this account/session and contain no later
        unfinished turn. This also works while stdout contains an older backlog.
        """
        if (launch.get('protocol') != 'app-server' or launch.get('transport') == 'ssh'
                or run.get('host_id', 'mac') not in ('mac', 'local') or ctrl.get('stop')):
            return
        clock = time.monotonic()
        if clock < ctrl.get('native_check_at', 0):
            return
        ctrl['native_check_at'] = clock + .5
        env = launch.get('env') or {}
        with ctrl.setdefault('control_lock', threading.RLock()):
            changed = False
            for child in list(ctrl.get('child_turn_ids') or {}):
                turn = ctrl.get('child_turn_ids', {}).get(child)
                evidence = codex_native_terminal(env, child, turn, run.get('created_at'),
                                                ctrl.setdefault('native_child_cache', {}).setdefault(child, {}))
                if evidence:
                    proofs = ctrl.setdefault('native_child_proofs', {})
                    ctrl['child_turn_ids'][child] = evidence['turn_id']
                    if proofs.get(child) != evidence:
                        ctrl.setdefault('background', {}).pop(child, None)
                        proofs[child] = evidence
                        self.store.emit(rid, 'background_finished', {'thread_id': child, **evidence})
                        changed = True
                elif ctrl.setdefault('native_child_proofs', {}).pop(child, None):
                    ctrl.setdefault('background', {})[child] = 'subagent'
                    changed = True
            guarded = (ctrl.get('background') or ctrl.get('pending_requests') or ctrl.get('pending_rpc')
                       or ctrl.get('auto_compacting') or ctrl.get('compact_failed')
                       or (ctrl.get('goal') or {}).get('status') == 'active'
                       or 'goal_get_pending' in ctrl or 'goal_set_pending' in ctrl)
            turn = ctrl.get('turn_id') or ctrl.get('last_root_turn_id')
            evidence = None if guarded else codex_native_terminal(
                env, run.get('session_id'), turn, run.get('created_at'), ctrl.setdefault('native_root_cache', {}))
            if evidence and ctrl.get('native_reconciled') != evidence:
                ctrl['native_reconciled'] = evidence
                ctrl['native_env'] = env
                ctrl['native_terminal'] = evidence['status']
                if evidence['status'] == 'failed':
                    failure = auth_failure.classify(run['provider'], auth_failure._text(evidence.get('error')), run.get('account_id'), evidence['timestamp'], evidence.get('error'))
                    st.error = failure['raw']
                    self._record_failure(rid, run, ctrl, failure)
                    self._signal(ctrl, signal.SIGKILL)
                if evidence.get('text') is not None:
                    ctrl['native_final_text'] = evidence['text']
                ctrl['last_root_turn_id'] = evidence['turn_id']
                ctrl['turn_id'] = None
                ctrl['turn_open'] = False
                self.store.emit(rid, 'native_turn_completed', evidence)
                if evidence['status'] == 'completed':
                    self._after_turn(rid, run, ctrl)
                changed = True
            elif not guarded and not evidence and ctrl.pop('native_reconciled', None):
                # New native input may precede its stdout notification.
                ctrl.pop('native_terminal', None)
                ctrl.pop('native_final_text', None)
                ctrl['turn_open'] = True
                changed = True
            if changed:
                self._settle(rid, ctrl)

    @staticmethod
    def _codex_message(st, ctrl, item):
        """Reconcile terminal snapshots with already delivered message items."""
        if item.get('type') != 'agentMessage' or not item.get('text'):
            return
        identity = item.get('id') or ('text', item['text'])
        seen = ctrl.setdefault('completed_message_ids', set())
        if identity in seen:
            return
        seen.add(identity)
        st.text += ('\n\n' if st.text else '') + item['text']

    def _provider_lines(self, proc, ctrl, stderr, tick=lambda: None):
        """Watch the owned leader as well as pipes inherited by descendants.

        No buffered blocking read/close may hold run finalization hostage to a
        descendant. Drain a bounded tail after leader exit, retaining UTF-8 and
        complete JSON records. A live provider has no elapsed-time deadline.
        """
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        pending = ''
        exited = False
        exit_bytes = 0
        with selectors.DefaultSelector() as selector:
            for pipe, kind in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, kind)
            while selector.get_map():
                if ctrl.get('stop'):
                    break
                tick()
                exited = proc.poll() is not None
                ready = selector.select(0 if exited else .1)
                if exited and (not ready or exit_bytes >= 16 * 1024 * 1024):
                    break
                for key, _ in ready:
                    try:
                        raw = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not raw:
                        selector.unregister(key.fileobj)
                        continue
                    if exited:
                        exit_bytes += len(raw)
                    if key.data == 'stderr':
                        stderr[:] = [(''.join(stderr) + raw.decode('utf-8', 'replace'))[-16000:]]
                        continue
                    pending += decoder.decode(raw)
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        if ctrl.get('stop'):
                            return
                        tick()
                        yield line
            pending += decoder.decode(b'', final=True)
            if pending.strip() and not ctrl.get('stop'):
                yield pending

    def _work(self, rid, launch, ctrl):
        run = self.store.get(rid)
        st = stream._State(run.get('session_id') or '')
        stderr = []
        terminal = None
        # Long-running native goals have no implicit elapsed-time deadline.
        # Explicit interruption and configured idle stopping own termination.
        try:
            self.store.update(rid, status='running', launch_attempted=True)
            proc = subprocess.Popen(launch['argv'], cwd=launch.get('cwd'), env=launch.get('env'),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1, start_new_session=True)
            ctrl['proc'] = proc
            ownership = {'process_pid': proc.pid, 'process_identity': process_identity(proc.pid),
                         'process_transport': launch.get('transport', 'local')}
            self.store.emit(rid, 'process_started', ownership)
            self.store.update(rid, **ownership)
            self.store.emit(rid, 'state', {'status': 'running'})
            if ctrl['stop']:
                self._signal(ctrl, signal.SIGINT)
            elif launch['protocol'] == 'app-server':
                self._send(ctrl, {'id': 1, 'method': 'initialize', 'params': {
                    'clientInfo': {'name': 'chat_library', 'title': 'Chat Library', 'version': '1.0'},
                    'capabilities': {'experimentalApi': False}}})
            elif launch['protocol'] == 'claude-stream':
                self._send(ctrl, {'type': 'control_request', 'request_id': 'initialize',
                                 'request': {'subtype': 'initialize', 'hooks': {}}})
            else:
                proc.stdin.write(run['prompt'])
                proc.stdin.close()
            def emit(event, data):
                if event == 'delta':
                    data = self._separated(ctrl, data)
                if not (run['provider'] == 'claude' and event == 'tool'):
                    self.store.emit(rid, event, data)
            ignored_claude_notification_result = False
            for line in self._provider_lines(proc, ctrl, stderr, lambda: self._reconcile_native(rid, run, st, ctrl, launch)):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self.store.emit(rid, 'provider_event', {'provider': run['provider'], 'event': ev})
                if (run.get('operation') == 'remote_control' and ev.get('session_id')
                        and ev['session_id'] != run.get('session_id')):
                    raise ValueError('Remote Control provider reported a different native session; ownership cannot be verified.')
                if launch['protocol'] == 'claude-stream' and self._claude_lifecycle(rid, ctrl, ev):
                    # Resume may finish a background notification before this user turn.
                    # Keep it in the raw journal but do not parse or finish the run.
                    ignored_claude_notification_result = True
                    continue
                failure = auth_failure.from_event(run['provider'], ev, run.get('account_id'), now())
                event_thread = (ev.get('params') or {}).get('threadId')
                if failure and (not event_thread or event_thread == run.get('session_id')):
                    st.error = failure['raw']
                    terminal = 'failed'
                    ctrl['quota_failure_confirmed'] = failure['kind'] == 'limit_reached'
                    self._record_failure(rid, run, ctrl, failure)
                    self._signal(ctrl, signal.SIGKILL)
                    break

                if launch['protocol'] == 'app-server':
                    if ev.get('method') == 'turn/started' and (ev.get('params') or {}).get('threadId') in (None, run.get('session_id')):
                        terminal = None
                    outcome = self._codex_rpc(rid, run, st, ctrl, ev)
                    if outcome:
                        terminal = outcome
                        if ctrl['stop']:
                            break
                    self._settle(rid, ctrl)
                else:
                    if run['provider'] == 'claude' and self._claude_control(rid, run, ctrl, ev):
                        if ctrl.get('remote_finish'):
                            terminal = 'completed'
                            break
                        self._settle(rid, ctrl)
                        continue
                    if run.get('operation') == 'remote_control':
                        self._remote_event(rid, run, ctrl, ev)
                    if run.get('operation') == 'compact' and ev.get('type') == 'system' and ev.get('subtype') == 'compact_boundary':
                        ctrl['compaction_confirmed'] = True
                        self.store.emit(rid, 'compacted', {'provider': 'claude', 'session_id': run.get('session_id')})
                    elif run['provider'] == 'claude' and ev.get('type') == 'system' and ev.get('subtype') == 'compact_boundary':
                        post = (ev.get('compact_metadata') or {}).get('post_tokens')
                        if ctrl.get('auto_compacting'):
                            self._auto_compacted(rid, run, ctrl, post)
                        elif post is not None:
                            self._track_context(rid, ctrl, tokens=post)
                    if run['provider'] == 'claude' and ev.get('type') == 'assistant' and not ev.get('parent_tool_use_id'):
                        # Root-thread context in use, verified against Claude Code 2.1.325 stream-json.
                        u = (ev.get('message') or {}).get('usage') or {}
                        if 'input_tokens' in u:
                            self._track_context(rid, ctrl, model=(ev.get('message') or {}).get('model'),
                                                tokens=sum(int(u.get(k) or 0) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')))
                    if run['provider'] == 'claude' and ev.get('type') == 'system' and ev.get('model'):
                        self.store.update(rid, effective_model=ev['model'], permission_mode=ev.get('permissionMode'))
                    parser = stream._parse_codex if run['provider'] == 'codex' else stream._parse_claude
                    if run['provider'] == 'claude' and ev.get('type') == 'result':
                        st.error = None  # each turn reports its own outcome
                    kept = (st.usage, st.cost) if ctrl.get('auto_compacting') and ev.get('type') == 'result' else None
                    parser(ev, st, emit)
                    if kept:
                        st.usage, st.cost = kept  # the /compact turn reports zero usage
                    if run['provider'] == 'claude':
                        self._claude_tools(rid, ev)
                    if st.sid and (not st.started or st.sid != run.get('session_id')):
                        self._bind(rid, run, st.sid, ctrl)
                        st.started = True
                    if run['provider'] == 'claude' and ev.get('type') == 'result':
                        self._claude_compact_recovery(ctrl, ev, st.error)
                        terminal = 'failed' if st.error else 'completed'
                        ctrl.setdefault('turn_texts', []).append(st.text)
                        if ctrl['stop']:
                            break
                        if run.get('operation') == 'remote_control' and ctrl.get('turn_message') and st.sid:
                            self.store.update(rid, text=st.text, usage=st.usage, cost_usd=st.cost)
                            current = self.store.get(rid)
                            self._persist(dict(current, message=ctrl['turn_message'], created_at=ctrl.get('turn_started', now())), launch, st)
                            self.store.emit(rid, 'remote_turn_done', {'text': st.text, 'error': st.error, 'session_id': st.sid})
                            ctrl.pop('turn_message', None)
                        model_usage = ev.get('modelUsage') or {}
                        entry = model_usage.get((ctrl.get('context') or {}).get('model')) or {}
                        if entry.get('contextWindow'):
                            self._track_context(rid, ctrl, window=entry['contextWindow'])
                        if not st.error:
                            self._after_turn(rid, run, ctrl)
                            if ctrl.get('compact_failed'):
                                st.error = 'Provider did not confirm automatic compaction.'
                                terminal = 'failed'
                        if run.get('operation') == 'remote_control' and not ctrl.get('auto_compacting'):
                            if ctrl.get('remote_disabling') and self._remote_quiescent(ctrl):
                                break
                            if not ctrl.get('remote_disabling') and not ctrl.get('remote_disconnect_pending') and not ctrl.get('turn_open'):
                                st = stream._State(st.sid)
                                self.store.update(rid, status='remote_idle')
                    if launch['protocol'] == 'claude-stream':
                        self._settle(rid, ctrl)
            # Reached only after the user's Stop, an idle close, or provider exit.
            if launch['protocol'] == 'app-server':
                self._signal(ctrl, signal.SIGTERM)
            elif launch['protocol'] == 'claude-stream' and not proc.stdin.closed:
                proc.stdin.close()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._signal(ctrl, signal.SIGKILL)
                proc.wait(timeout=5)
            self._cancel_idle(ctrl)
            for item in ctrl.get('claude_inputs') or []:
                if item.get('queue_item'):
                    self._queue_failed(rid, item, 'The agent exited before reading this message.')
                else:
                    self.store.emit(rid, 'control_error', {'action': 'steer', 'message':
                                    'Claude exited before reading this message.'})
            for pending in list((ctrl.get('pending_rpc') or {}).values()):
                if pending.get('kind') == 'queue':
                    self._queue_failed(rid, pending, 'The agent exited before confirming this message.')
            current = self.store.get(rid)
            text = '\n\n'.join(t for t in ctrl.get('turn_texts') or [] if t) or st.text or current.get('text', '')
            if ignored_claude_notification_result and not terminal and not st.error:
                st.error = 'Provider exited before completing the submitted message.'
            unresolved_goal = ('goal_set_pending' in ctrl or 'goal_get_pending' in ctrl
                               or (ctrl.get('goal') or {}).get('status') == 'active')
            if not ctrl['stop'] and ctrl.get('remote_connected') and not ctrl.get('remote_disabling'):
                terminal = 'failed'
                st.error = 'Remote Control disconnected because the provider exited.'
            if not ctrl['stop'] and ('goal_set_pending' in ctrl or 'goal_get_pending' in ctrl):
                terminal = 'failed'
                st.error = 'Provider exited before confirming native goal state. No initial turn was submitted; resume the session to reconcile it.'
            elif not ctrl['stop'] and (ctrl.get('goal') or {}).get('status') == 'active':
                terminal = 'failed'
                st.error = 'Provider exited while its native goal was still active. Resume the session to reconcile it.'
            if (run.get('operation') == 'remote_control' and not ctrl['stop']
                    and not ctrl.get('remote_finish')
                    and not (ctrl.get('remote_disabling') and terminal == 'completed')):
                terminal = 'failed'
                st.error = st.error or 'Claude Remote Control provider exited unexpectedly before a confirmed disconnect.'
            if ctrl.get('native_final_text') is not None:
                text = ctrl['native_final_text']
            status = ('interrupted' if ctrl['stop'] else 'failed' if unresolved_goal or ctrl.get('failure') else
                      ctrl.get('native_terminal') or terminal or ('failed' if st.error or proc.returncode != 0 else 'completed'))
            if status == 'completed' and run.get('operation') == 'compact':
                if not ctrl.get('compaction_confirmed'):
                    status = 'failed'
                    st.error = 'Provider did not confirm context compaction. The session was not marked compacted.'
                else:
                    text = 'Context compacted.'
            if status == 'completed' and not st.sid:
                status = 'failed'
                st.error = 'Provider exited without a native session id'
            if status == 'failed':
                error = st.error or ''.join(stderr)[-4000:] or 'Provider run failed'
                failure = ctrl.get('failure') or auth_failure.classify(run['provider'], error, run.get('account_id'), now())
                if not ctrl.get('failure'):
                    self._record_failure(rid, run, ctrl, failure)
                self.store.emit(rid, 'error', {'message': error, 'status': status, 'failure': failure})
                self.store.update(rid, status=status, phase='finished', background=[], text=text, error=error, usage=st.usage, finished_at=now())
                if ctrl.get('quota_failure_confirmed') and st.sid:
                    self._persist(self.store.get(rid), launch, st)
            else:
                self.store.update(rid, text=text, usage=st.usage, cost_usd=st.cost)
                if st.sid and run.get('operation') not in ('compact', 'remote_control'):
                    self._persist(self.store.get(rid), launch, st)
                self.store.emit(rid, 'done', {'text': text, 'key': run.get('key'), 'session_id': st.sid,
                                             'status': status, 'usage': st.usage, 'cost_usd': st.cost})
                self.store.update(rid, status=status, phase='finished', background=[], finished_at=now())
        except Exception as exc:
            failure = auth_failure.classify(run['provider'], str(exc), run.get('account_id'), now())
            if not ctrl['stop']:
                self._record_failure(rid, run, ctrl, failure)
            self.store.emit(rid, 'error', {'message': str(exc), 'failure': failure})
            self.store.update(rid, status='interrupted' if ctrl['stop'] else 'failed', phase='finished', background=[], error=str(exc), finished_at=now())
        finally:
            if run.get('operation') == 'remote_control':
                ctrl['remote_connected'] = False
                current_remote = self.store.get(rid).get('remote_control') or {}
                if current_remote.get('status') != 'error':
                    current_remote = {'status': 'disconnected'}
                    self.store.update(rid, remote_control=current_remote)
                self.store.emit(rid, 'remote_control', current_remote)
            ctrl['closing'] = True
            self._cancel_idle(ctrl)
            timer = ctrl.pop('interrupt_timer', None)
            if timer:
                timer.cancel()
            self._signal(ctrl, signal.SIGKILL)
            proc = ctrl.get('proc')
            if proc:
                # Reap on exceptional exits too, never leave a zombie child.
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
            for handle in ctrl['lockfiles']:
                handle.close()
            with self.lock:
                self.active.pop(rid, None)
            try:
                import quota_failover
                quota_failover.continue_run(self, rid, launch, ctrl)
            finally:
                ctrl['finished'].set()

    def _bind(self, rid, run, sid, ctrl=None):
        # New sessions initially own a random launch lock. Acquire the final
        # native and logical identities before making them discoverable/resumable.
        with self.lock:
            ctrl = ctrl if ctrl is not None else self.active[rid]
            key = run.get('key') or f"{run['provider']}-{sid}"
            conversation_key = run.get('conversation_key') or key
            native_identity = '|'.join(str(value or '') for value in (run.get('host_id'), run.get('provider'), sid))
            identities = {native_identity, 'conversation|' + conversation_key}
            held = ctrl.setdefault('lock_identities', set())
            acquired = []
            lockdir = self.store.root / 'locks'
            lockdir.mkdir(exist_ok=True)
            try:
                for identity in sorted(identities - held):
                    handle = (lockdir / hashlib.sha256(identity.encode()).hexdigest()).open('a')
                    acquired.append(handle)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                for handle in acquired:
                    handle.close()
                active = next((r for r in self.store.list() if r['id'] != rid and r.get('status') not in TERMINAL and
                               (r.get('conversation_key') == conversation_key or
                                (r.get('host_id') == run.get('host_id') and r.get('provider') == run.get('provider') and r.get('session_id') == sid))), None)
                raise SessionBusy(active['id'] if active else None)
            except Exception:
                for handle in acquired:
                    handle.close()
                raise
            ctrl.setdefault('lockfiles', []).extend(acquired)
            held.update(identities)
            if run.get('quota_failover_from'):
                provenance = dict(run['quota_failover_from'], native_session_id=sid)
                run['quota_failover_from'] = provenance
                self.store.update(rid, quota_failover_from=provenance)
            run.update(session_id=sid, key=key, conversation_key=conversation_key)
            self.store.update(rid, session_id=sid, key=key, conversation_key=conversation_key)
            self.store.emit(rid, 'start', {'key': key, 'session_id': sid,
                                          'account': run.get('account_id'), 'host_id': run['host_id']})

    @staticmethod
    def _input_text(run):
        refs = [f"{a['name']}: {a['path']}" for a in run.get('attachment_records', []) if not a['mime'].startswith('image/')]
        return run['prompt'] + ('\n\nAttached local files (read these paths using file tools):\n' + '\n'.join(refs) if refs else '')

    def _claude_input(self, run):
        content = [{'type': 'text', 'text': self._input_text(run)}]
        for a in run.get('attachment_records', []):
            if a['mime'].startswith('image/'):
                content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': a['mime'],
                                'data': base64.b64encode(Path(a['path']).read_bytes()).decode()}})
        return {'type': 'user', 'message': {'role': 'user', 'content': content}, 'parent_tool_use_id': None}

    def _send_steering(self, ctrl, thread_id, pending):
        # During a root turn, steer it. Between turns, start the next turn on
        # the same live thread, so background subagents keep running.
        request = self._rpc_id(ctrl)
        turn = ctrl.get('turn_id')
        pending = dict(pending, expected_turn_id=turn)
        ctrl.setdefault('pending_rpc', {})[str(request)] = pending
        attachments = pending.get('attachments', [])
        params = {'threadId': thread_id, 'input': [{'type': 'text', 'text': self._input_text({
            'prompt': pending['message'], 'attachment_records': attachments})}] +
            [{'type': 'localImage', 'path': item['path']} for item in attachments
             if item['mime'].startswith('image/')]}
        if turn:
            params['expectedTurnId'] = turn
        else:
            ctrl['turn_open'] = True
        self._send(ctrl, {'id': request, 'method': 'turn/steer' if turn else 'turn/start', 'params': params})

    @staticmethod
    def _valid_codex_goal(goal, sid):
        return (isinstance(goal, dict) and goal.get('threadId') == sid
                and isinstance(goal.get('objective'), str)
                and goal.get('status') in ('active', 'paused', 'blocked', 'usageLimited', 'budgetLimited', 'complete'))

    def _codex_goal(self, rid, ctrl, goal):
        ctrl['goal'] = goal
        ctrl['goal_revision'] = ctrl.get('goal_revision', 0) + 1
        self.store.update(rid, goal=goal)
        self.store.emit(rid, 'goal', goal if goal is not None else {'cleared': True})

    def _codex_begin(self, run, ctrl, sid):
        ctrl['turn_open'] = True
        if run.get('operation') == 'compact':
            self._send(ctrl, {'id': 3, 'method': 'thread/compact/start', 'params': {'threadId': sid}})
            ctrl['begun'] = True
            return
        inp = [{'type': 'text', 'text': self._input_text(run)}]
        inp += [{'type': 'localImage', 'path': a['path']} for a in run.get('attachment_records', []) if a['mime'].startswith('image/')]
        params = {'threadId': sid, 'input': inp}
        if run.get('model'):
            params['model'] = run['model']
        if run.get('effort'):
            params['effort'] = run['effort']
        self._send(ctrl, {'id': 3, 'method': 'turn/start', 'params': params})
        ctrl['begun'] = True

    def _codex_rpc(self, rid, run, st, ctrl, ev):
        with ctrl.setdefault('control_lock', threading.RLock()):
            return self._codex_rpc_locked(rid, run, st, ctrl, ev)

    def _codex_rpc_locked(self, rid, run, st, ctrl, ev):
        if ev.get('id') == 1 and 'result' in ev:
            self._send(ctrl, {'method': 'initialized', 'params': {}})
            # Sandbox and approvals come from the account's CODEX_HOME config.toml (2026-11-15):
            # an unrestricted account may run danger-full-access while workspace-managed
            # profiles capped by policy use workspace-write + auto_review.
            params = {'cwd': run['cwd']}
            if run.get('model'):
                params['model'] = run['model']
            ctrl['resumed_thread'] = bool(run.get('session_id'))
            if run.get('session_id'):
                params['threadId'] = run['session_id']
            self._send(ctrl, {'id': 2, 'method': 'thread/resume' if run.get('session_id') else 'thread/start', 'params': params})
        elif ev.get('id') == 2 and 'result' in ev:
            sid = (ev['result'].get('thread') or {}).get('id')
            if not sid:
                raise ValueError('app-server did not return a thread id')
            ctrl.setdefault('resumed_thread', bool(run.get('session_id')))
            st.sid = sid
            self.store.update(rid, effective_model=ev['result'].get('model'),
                              effective_effort=ev['result'].get('reasoningEffort'))
            self._bind(rid, run, sid, ctrl)
            # Keep startup open until the authoritative goal receipt arrives.
            # A resumed thread may still have a native goal from an earlier process.
            if run.get('operation') == 'goal':
                ctrl['goal_set_pending'] = ctrl.get('goal_revision', 0)
                self._send(ctrl, {'id': 4, 'method': 'thread/goal/set', 'params': {
                    'threadId': sid, 'objective': run['message'], 'status': 'active', 'origin': 'user'}})
            elif run.get('session_id') and ctrl.get('resumed_thread'):
                ctrl['goal_get_pending'] = ctrl.get('goal_revision', 0)
                self._send(ctrl, {'id': 5, 'method': 'thread/goal/get', 'params': {'threadId': sid}})
            else:
                self._codex_begin(run, ctrl, sid)
        elif ev.get('id') == 4 and 'result' in ev and 'goal_set_pending' in ctrl:
            revision = ctrl.pop('goal_set_pending')
            goal = (ev['result'] or {}).get('goal')
            if (not self._valid_codex_goal(goal, st.sid) or goal.get('objective') != run['message']
                    or goal.get('status') != 'active'):
                raise ValueError('Codex did not confirm the requested goal')
            if ctrl.get('goal_revision', 0) == revision:
                self._codex_goal(rid, ctrl, goal)
            self._codex_begin(run, ctrl, st.sid)
        elif ev.get('id') == 5 and 'result' in ev and 'goal_get_pending' in ctrl:
            revision = ctrl.pop('goal_get_pending')
            result = ev['result'] or {}
            goal = result.get('goal')
            if 'goal' not in result or (goal is not None and not self._valid_codex_goal(goal, st.sid)):
                raise ValueError('Codex did not return authoritative goal state')
            if ctrl.get('goal_revision', 0) == revision:
                self._codex_goal(rid, ctrl, goal)
            self._codex_begin(run, ctrl, st.sid)
        elif 'error' in ev and 'id' in ev:
            pending = ctrl.setdefault('pending_rpc', {}).pop(str(ev['id']), {})
            message = (ev['error'] or {}).get('message') or 'app-server request failed'
            # Native mismatch explicitly rejects delivery. Retry once with its
            # authoritative root turn, never after an ambiguous transport failure.
            mismatch = re.fullmatch(r'expected active turn id `([^`]+)` but found `([^`]+)`', message)
            if (pending.get('kind') == 'steer' and mismatch and not pending.get('retried')
                    and mismatch[1] == pending.get('expected_turn_id')
                    and mismatch[2] != mismatch[1]):
                ctrl['turn_id'] = mismatch[2]
                self._send_steering(ctrl, run['session_id'], dict(pending, retried=True))
                return
            # Codex 0.167 can report no active turn while the root turn still
            # streams. turn/start adds input to an active turn or starts one.
            if pending.get('kind') == 'steer' and message == 'no active turn to steer' and not pending.get('retried'):
                ctrl['turn_id'] = None
                self._send_steering(ctrl, run['session_id'], dict(pending, retried=True))
                return
            if pending.get('kind') in ('steer', 'queue') and not pending.get('expected_turn_id') and ctrl.get('root_idle'):
                ctrl['turn_open'] = False
            if pending.get('kind') == 'queue':
                self._queue_failed(rid, pending, message)
                self._after_turn(rid, run, ctrl)
                return
            if pending.get('kind') == 'auto_compact':
                ctrl['auto_compacting'] = False
                # A native goal can start its next root turn before this
                # compaction request is rejected. Preserve that newer work.
                ctrl['turn_open'] = bool(ctrl.get('turn_id')) or any(
                    p.get('kind') in ('steer', 'queue') for p in ctrl['pending_rpc'].values())
                self.store.emit(rid, 'control_error', {'message': message, 'action': 'auto_compact'})
                if not ctrl['turn_open']:
                    self._after_turn(rid, run, ctrl)
                return
            if pending:
                self.store.emit(rid, 'control_error', {'message': message, 'action': pending.get('kind')})
            else:
                raise ValueError(message)
        elif ev.get('id') == 3 and 'result' in ev:
            ctrl['turn_id'] = (ev['result'].get('turn') or {}).get('id')
        elif 'id' in ev and 'method' in ev:
            method = ev['method']
            params = ev.get('params') or {}
            kind = 'approval' if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval') else 'question' if method == 'item/tool/requestUserInput' else ''
            if kind:
                request = str(ev['id'])
                decisions = params.get('availableDecisions')
                policy = self.policy(run.get('cwd'))
                acceptable = decisions is None or isinstance(decisions, list) and 'accept' in decisions
                cmd = params.get('command')
                answer = self._policy_answer(run, 'Bash', {'command': ' '.join(cmd) if isinstance(cmd, list) else cmd}) \
                    if kind == 'approval' and method == 'item/commandExecution/requestApproval' and cmd else ('allow', '')
                if kind == 'approval' and answer and answer[0] == 'route':
                    self._send(ctrl, {'id': ev['id'], 'result': {'decision': 'decline'}})
                    self.store.emit(rid, 'approval_routed', {'request_id': request, 'method': method, 'provider': 'codex',
                                                             'summary': answer[1][:200]})
                elif kind == 'approval' and acceptable and policy['auto_approve_tools'] and answer and answer[0] == 'allow':
                    self._send(ctrl, {'id': ev['id'], 'result': {'decision': 'accept'}})
                    self.store.emit(rid, 'approval_auto_accepted', {'request_id': request, 'method': method, 'provider': 'codex',
                                                                    'summary': self._summary(params.get('command') or params.get('reason') or params)})
                elif kind == 'question' and policy['agent_questions'] != 'ask':
                    self._send(ctrl, {'id': ev['id'], 'result': {'answers': {
                        str(q.get('id')): {'answers': [DEFER_ANSWER]} for q in params.get('questions') or []}}})
                    self.store.emit(rid, 'question_deferred', {'request_id': request, 'method': method, 'provider': 'codex',
                                                               'questions': params.get('questions') or []})
                else:
                    ctrl.setdefault('pending_requests', {})[request] = {'native_id': ev['id'], 'kind': kind, 'params': params}
                    self.store.emit(rid, kind, dict(params, request_id=request, method=method, human_required=True))
                    reason = ('questions_set_to_ask' if kind == 'question' else
                              'auto_approve_off' if acceptable else 'no_accept_option')
                    self._human_required(rid, run, request, kind, reason, method, params.get('questions') or [])
            else:
                # Sandbox expansion (item/permissions/requestApproval), MCP elicitation and
                # other requests are declined, never granted automatically; they are
                # listed afterwards as open decisions.
                self._send(ctrl, {'id': ev['id'], 'error': {'code': -32601, 'message': 'Unsupported client request: ' + method}})
                self.store.emit(rid, 'unsupported_request', {'method': method, 'params': params})
        else:
            method = ev.get('method')
            p = ev.get('params') or {}
            # app-server forwards subagent notifications on this same stream.
            # Keep their raw/tool activity, but only the bound thread owns this
            # run's lifecycle, assistant response, compaction and token usage.
            child_thread = p.get('threadId') and p['threadId'] != run.get('session_id')
            # Active child threads are background work that keeps the provider alive.
            active = ((p.get('status') or {}).get('type') == 'active' if method == 'thread/status/changed' else
                      {'turn/started': True, 'turn/completed': False}.get(method))
            if child_thread and method == 'turn/started' and (p.get('turn') or {}).get('id'):
                ctrl.setdefault('child_turn_ids', {})[p['threadId']] = p['turn']['id']
            if child_thread and active is not None:
                if active:
                    ctrl.setdefault('background', {})[p['threadId']] = 'subagent'
                else:
                    ctrl.setdefault('background', {}).pop(p['threadId'], None)
            elif method == 'thread/status/changed' and active is not None:
                ctrl['root_idle'] = not active
            if child_thread and (method in ('turn/started', 'turn/completed', 'thread/compacted',
                                            'item/agentMessage/delta', 'thread/tokenUsage/updated')
                                 or (method in ('item/started', 'item/completed') and
                                     (p.get('item') or {}).get('type') in ('agentMessage', 'contextCompaction'))):
                return
            if method in ('thread/goal/updated', 'thread/goal/cleared'):
                # Goal authority is always explicitly scoped; no legacy unscoped
                # fallback and no child-thread lifecycle mutations.
                if p.get('threadId') != run.get('session_id'):
                    return
                goal = p.get('goal') if method == 'thread/goal/updated' else None
                if method == 'thread/goal/updated' and not self._valid_codex_goal(goal, st.sid):
                    return
                self._codex_goal(rid, ctrl, goal)
            elif method == 'turn/started':
                ctrl['completed_message_ids'] = set()
                ctrl['turn_id'] = (p.get('turn') or {}).get('id')
                ctrl['last_root_turn_id'] = ctrl['turn_id']
                ctrl.pop('native_reconciled', None)
                ctrl.pop('native_terminal', None)
                ctrl.pop('native_final_text', None)
                ctrl['turn_open'] = True
            elif method == 'thread/compacted':
                ctrl['compaction_confirmed'] = True
                if ctrl.get('auto_compacting'):
                    self._auto_compacted(rid, run, ctrl)
                else:
                    self.store.emit(rid, 'compacted', {'provider': 'codex', 'session_id': st.sid})
            elif method == 'item/agentMessage/delta':
                self.store.emit(rid, 'delta', self._separated(ctrl, {'text': p.get('delta', ''), 'item_id': p.get('itemId')}))
            elif method == 'item/commandExecution/outputDelta':
                self.store.emit(rid, 'output', p)
            elif method == 'turn/diff/updated':
                self.store.emit(rid, 'file_change', p)
            elif method in ('item/started', 'item/completed'):
                item = p.get('item') or {}
                typ = item.get('type')
                if typ == 'contextCompaction' and method == 'item/completed':
                    ctrl['compaction_confirmed'] = True
                    if ctrl.get('auto_compacting'):
                        self._auto_compacted(rid, run, ctrl)
                    else:
                        self.store.emit(rid, 'compacted', {'provider': 'codex', 'session_id': st.sid})
                elif typ == 'agentMessage' and method == 'item/completed':
                    # Final items are authoritative; preserve commentary and final separately.
                    self._codex_message(st, ctrl, item)
                elif typ == 'fileChange':
                    self.store.emit(rid, 'file_change', item)
                elif typ in ('commandExecution', 'mcpToolCall', 'dynamicToolCall', 'webSearch'):
                    self.store.emit(rid, 'tool' if method == 'item/started' else 'tool_result', dict(item, name=typ))
            elif method == 'thread/tokenUsage/updated':
                st.usage = p.get('tokenUsage')
                # Schema of codex 0.167 app-server: tokenUsage.last.totalTokens and modelContextWindow.
                usage = p.get('tokenUsage') or {}
                if (usage.get('last') or {}).get('totalTokens') is not None and usage.get('modelContextWindow'):
                    self._track_context(rid, ctrl, tokens=usage['last']['totalTokens'], window=usage['modelContextWindow'])
            elif method == 'serverRequest/resolved':
                request = str(p.get('requestId'))
                ctrl.setdefault('pending_requests', {}).pop(request, None)
                self.store.emit(rid, 'request_resolved', {'request_id': request})
            elif method == 'turn/completed':
                turn = p.get('turn') or {}
                # A terminal snapshot can contain the final answer even when its
                # individual item/completed notification was never delivered.
                for item in turn.get('items') or []:
                    self._codex_message(st, ctrl, item)
                status = turn.get('status')
                st.error = (turn.get('error') or {}).get('message') or 'Codex turn failed' if status == 'failed' else None
                # The run stays open for subagents, queued steering and follow-ups.
                ctrl['last_root_turn_id'] = turn.get('id') or ctrl.get('turn_id')
                ctrl['turn_id'] = None
                ctrl['turn_open'] = any(r.get('kind') in ('steer', 'queue') for r in ctrl.setdefault('pending_rpc', {}).values())
                ctrl['separate'] = True
                if status == 'completed':
                    self._after_turn(rid, run, ctrl)
                return {'completed': 'completed', 'interrupted': 'interrupted'}.get(status, 'failed')
            elif method == 'error':
                self.store.emit(rid, 'provider_warning', p)
            elif 'result' in ev and str(ev.get('id')) in ctrl.setdefault('pending_rpc', {}):
                pending = ctrl['pending_rpc'].pop(str(ev['id']))
                if pending.get('kind') in ('steer', 'queue') and (ev['result'] or {}).get('turn'):
                    ctrl['turn_id'] = ev['result']['turn'].get('id') or ctrl.get('turn_id')
                    ctrl['turn_open'] = True
                if pending.get('kind') in ('steer', 'queue') and pending.get('message'):
                    self.store.update(rid, submitted_message=pending['message'], submitted_attachments=[item['id'] for item in pending.get('attachments', []) if item.get('id')])
                if pending.get('kind') == 'queue':
                    self._queue_confirmed(rid, pending)
                    return
                self.store.emit(rid, 'steered' if pending.get('kind') == 'steer' else 'control_receipt',
                                {'message': pending.get('message'), 'action': pending.get('kind'), 'result': ev['result'], 'attachments': pending.get('attachments', []),
                                 'request_id': pending.get('request_id'),
                                 'method': 'turn/steer' if pending.get('expected_turn_id') else 'turn/start'})

    @staticmethod
    def _separated(ctrl, data):
        """Start each later turn's streamed text on a new paragraph."""
        if ctrl.get('separate') and data.get('text'):
            ctrl['separate'] = False
            return dict(data, text='\n\n' + data['text'])
        return data

    def _claude_lifecycle(self, rid, ctrl, ev):
        """Track Claude background tasks, input replays and turn ends.

        Returns True for a task-notification result that precedes the
        submitted message (a resume can deliver one first); skip it.
        """
        with ctrl.setdefault('control_lock', threading.RLock()):
            typ, sub = ev.get('type'), ev.get('subtype')
            background = ctrl.setdefault('background', {})
            if typ == 'system' and sub == 'background_tasks_changed':
                ctrl['background'] = {t['task_id']: t.get('description') or t.get('task_type') or 'task'
                                      for t in ev.get('tasks') or [] if isinstance(t, dict) and t.get('task_id')}
            elif typ == 'system' and sub == 'task_started' and ev.get('is_backgrounded'):
                background[ev.get('task_id')] = ev.get('description') or 'task'
            elif typ == 'system' and sub == 'task_notification':
                background.pop(ev.get('task_id'), None)
            elif typ == 'user' and ev.get('isReplay'):
                ctrl['replayed'] = True
                content = (ev.get('message') or {}).get('content')
                text = content if isinstance(content, str) else next(
                    (b.get('text') or '' for b in content or [] if isinstance(b, dict) and b.get('type') == 'text'), '')
                inputs = ctrl.setdefault('claude_inputs', [])
                match = next((item for item in inputs if item['wire'] == text.strip()), None)
                if match:
                    inputs.remove(match)
                    if match.get('message'):
                        self.store.update(rid, submitted_message=match['message'], submitted_attachments=[item['id'] for item in match.get('attachments', []) if item.get('id')])
                    if match.get('recover_compact'):
                        ctrl['compact_recovery_replayed'] = True
                    ctrl['turn_open'] = True
                    if match.get('queue_item'):
                        self._queue_confirmed(rid, match)
                    else:
                        self.store.emit(rid, 'steered', {'message': match['message'], 'action': 'steer',
                                                         'attachments': match['attachments'], 'method': 'stdin',
                                                         'request_id': match.get('request_id')})
            elif typ == 'result':
                origin = ev.get('origin') if isinstance(ev.get('origin'), dict) else {}
                if origin.get('kind') == 'task-notification' and not ctrl.get('user_turn_done') and not ctrl.get('replayed'):
                    return True
                ctrl['user_turn_done'] = True
                ctrl['separate'] = True
                inputs = ctrl.setdefault('claude_inputs', [])
                for item in inputs:
                    item['results'] += 1
                # Claude replays queued stdin after the current tool call returns, or
                # starts a turn with it. Results read from stdout may predate the write,
                # so counting them never rejects; unread input holds the process open
                # for one more turn, then stays pending until replay or provider exit.
                ctrl['turn_open'] = any(item['results'] < 2 for item in inputs)
        return False

    @staticmethod
    def _claude_compact_recovery(ctrl, event, error):
        origin = event.get('origin') if isinstance(event.get('origin'), dict) else {}
        if origin.get('kind') == 'task-notification':
            return
        if ctrl.pop('compact_recovery_replayed', False) and not error:
            ctrl.pop('compact_failed', None)
            # Explicit recovery must not trigger another compaction in the same crossing.
            ctrl['compact_spent'] = True

    @staticmethod
    def _remote_quiescent(ctrl):
        return not (ctrl.get('turn_open') or ctrl.get('background') or ctrl.get('pending_requests')
                    or ctrl.get('auto_compacting') or ctrl.get('claude_inputs'))

    def _settle(self, rid, ctrl):
        """Publish the run phase; count down to closing only when idle.

        Idle means no open turn, no unread input and no background work.
        Any provider event restarts the countdown.
        """
        with ctrl.setdefault('control_lock', threading.RLock()):
            if ctrl.get('stop') or ctrl.get('closing'):
                return
            if ctrl.get('remote_disabling') and self._remote_quiescent(ctrl):
                ctrl['closing'] = True
                self._cancel_idle(ctrl)
                with ctrl['send_lock']:
                    try:
                        ctrl['proc'].stdin.close()
                    except (AttributeError, OSError, ValueError):
                        pass
                return
            if (ctrl.get('after_turn_deferred') and not ctrl.get('turn_open')
                    and not ctrl.get('background') and not ctrl.get('pending_requests')):
                self._after_turn(rid, self.store.get(rid), ctrl)
            background = dict(ctrl.get('background') or {})
            if ctrl.get('pending_requests'):
                background['native_request'] = 'waiting for input'
            if any(p.get('kind') == 'auto_compact' for p in (ctrl.get('pending_rpc') or {}).values()):
                background['compact_receipt'] = 'compacting'
            if ctrl.get('remote_connected'):
                background['remote_control'] = 'remote control'
            if (ctrl.get('goal') or {}).get('status') == 'active':
                background['native_goal'] = 'goal'
            phase = 'turn' if ctrl.get('turn_open') else 'background' if background else 'idle'
            labels = sorted(str(v) for v in background.values())
            if ctrl.get('shown_phase') != (phase, labels):
                ctrl['shown_phase'] = (phase, labels)
                self.store.update(rid, phase=phase, background=labels)
                self.store.emit(rid, 'state', {'status': 'running', 'phase': phase, 'background': labels})
            if phase == 'idle' and ctrl.get('native_reconciled') and ctrl.get('idle_timer'):
                return  # Old stdout must not postpone a proven native completion.
            self._cancel_idle(ctrl)
            if phase == 'idle':
                timer = threading.Timer(IDLE_GRACE, self._close_idle, (rid, ctrl))
                timer.daemon = True
                ctrl['idle_timer'] = timer
                timer.start()

    @staticmethod
    def _cancel_idle(ctrl):
        timer = ctrl.pop('idle_timer', None)
        if timer:
            timer.cancel()

    def _close_idle(self, rid, ctrl):
        with ctrl['control_lock']:
            if (ctrl.get('idle_timer') is not threading.current_thread() or ctrl.get('turn_open')
                    or ctrl.get('background') or ctrl.get('pending_requests') or ctrl.get('stop')
                    or ctrl.get('remote_connected')
                    or any(p.get('kind') == 'auto_compact' for p in (ctrl.get('pending_rpc') or {}).values())
                    or (ctrl.get('goal') or {}).get('status') == 'active'
                    or 'goal_get_pending' in ctrl or 'goal_set_pending' in ctrl):
                return
            if ctrl.get('native_reconciled'):
                run = self.store.get(rid)
                for child, proof in ctrl.get('native_child_proofs', {}).items():
                    if not codex_native_terminal(ctrl.get('native_env') or {}, child, proof['turn_id'],
                                                 run.get('created_at'), ctrl.setdefault('native_child_cache', {}).setdefault(child, {})):
                        ctrl.setdefault('background', {})[child] = 'subagent'
                        self._settle(rid, ctrl)
                        return
                if not codex_native_terminal(ctrl.get('native_env') or {}, run.get('session_id'),
                                             ctrl['native_reconciled']['turn_id'], run.get('created_at'),
                                             ctrl.setdefault('native_root_cache', {})):
                    return
            ctrl['closing'] = True
            if ctrl.get('protocol') == 'app-server':
                self._signal(ctrl, signal.SIGTERM)
                return
            with ctrl['send_lock']:
                try:
                    ctrl['proc'].stdin.close()
                except (AttributeError, OSError, ValueError):
                    pass

    def _claude_tools(self, rid, ev):
        if ev.get('type') in ('assistant', 'user'):
            for block in (ev.get('message') or {}).get('content') or []:
                if isinstance(block, dict) and block.get('type') in ('tool_use', 'tool_result'):
                    self.store.emit(rid, 'tool' if block['type'] == 'tool_use' else 'tool_result', block)

    def _claude_control(self, rid, run, ctrl, ev):
        with ctrl.setdefault('control_lock', threading.RLock()):
            return self._claude_control_locked(rid, run, ctrl, ev)

    def _remote_event(self, rid, run, ctrl, ev):
        if ev.get('type') == 'system' and ev.get('subtype') == 'bridge_state':
            current = self.store.get(rid).get('remote_control') or {}
            if ev.get('state') in ('failed', 'policy_disabled') and (ev.get('bridge_epoch') is None or ev.get('bridge_epoch') == current.get('bridge_epoch')):
                raise ValueError('Claude Remote Control disconnected: ' + str(ev.get('state')))
        if ev.get('type') != 'user' or ev.get('parent_tool_use_id'):
            return
        content = (ev.get('message') or {}).get('content') or []
        text = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
        if ctrl.get('auto_compacting') and text.strip() == '/compact':
            return  # Internal compaction is not an external user message.
        if text and text != ctrl.get('turn_message'):
            ctrl['turn_message'] = text
            ctrl['turn_started'] = now()
            ctrl['turn_open'] = True
            self.store.update(rid, status='running', message=text, text='')
            self.store.emit(rid, 'remote_turn_start', {'message': text, 'source': 'claude'})

    def _claude_control_locked(self, rid, run, ctrl, ev):
        """Claude SDK stdio control messages, verified against SDK 0.3.284.

        Keep the raw event journal so a future protocol change stays inspectable.
        Managed permission rules continue to be enforced by the native CLI.
        """
        typ = ev.get('type')
        if typ == 'control_response':
            response = ev.get('response') or {}
            request = response.get('request_id')
            if request == 'initialize':
                if response.get('subtype') != 'success':
                    raise ValueError(response.get('error') or 'Claude initialization failed')
                ctrl['initialized'] = True
                if run.get('operation') == 'remote_control':
                    if (response.get('response') or {}).get('remote_control_available') is False:
                        raise ValueError('Remote Control is unavailable for this Claude account')
                    self.store.update(rid, remote_control={'status': 'connecting'})
                    self._send(ctrl, {'type': 'control_request', 'request_id': 'remote-on',
                                     'request': {'subtype': 'remote_control', 'enabled': True}})
                else:
                    self._send(ctrl, self._claude_input(run))
            elif request in ('remote-on', 'remote-off'):
                if response.get('subtype') != 'success':
                    error = response.get('error') or 'Claude Remote Control failed'
                    self.store.update(rid, remote_control={'status': 'error', 'error': error})
                    ctrl['remote_connected'] = False
                    if request == 'remote-on':
                        raise ValueError(error)
                    self.store.emit(rid, 'control_error', {'message': error, 'request_id': request})
                    return True
                value = response.get('response') or {}
                if request == 'remote-on':
                    from urllib.parse import urlparse
                    url = value.get('session_url') or ''
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or parsed.hostname != 'claude.ai' or parsed.username or parsed.password:
                        raise ValueError('Claude did not return a valid Remote Control session URL')
                    state = {'status': 'connected', 'session_url': url, 'bridge_epoch': value.get('bridge_epoch')}
                    ctrl['remote_connected'] = True
                    ctrl['turn_open'] = False
                    self.store.update(rid, status='remote_idle', remote_control=state, message='')
                else:
                    state = {'status': 'disconnected'}
                    ctrl['remote_disabling'] = True
                    ctrl['remote_connected'] = False
                    was_pending = ctrl.pop('remote_disconnect_pending', False)
                    ctrl['remote_finish'] = self._remote_quiescent(ctrl) and (
                        self.store.get(rid)['status'] == 'remote_idle' or bool(was_pending))
                    self.store.update(rid, remote_control=state)
                self.store.emit(rid, 'remote_control', state)
            elif response.get('subtype') == 'error':
                self.store.emit(rid, 'control_error', {'message': response.get('error'), 'request_id': request})
            else:
                self.store.emit(rid, 'control_receipt', response)
            return True
        if typ == 'control_cancel_request':
            request = str(ev.get('request_id'))
            ctrl.setdefault('pending_requests', {}).pop(request, None)
            self.store.emit(rid, 'request_resolved', {'request_id': request})
            return True
        if typ != 'control_request':
            return False
        request = ev.get('request') or {}
        request_id = str(ev.get('request_id'))
        if request.get('subtype') == 'can_use_tool':
            tool_name = request.get('tool_name')
            kind = 'question' if tool_name == 'AskUserQuestion' else 'approval'
            policy = self.policy(run.get('cwd'))
            questions = [dict(q, id=q.get('question'), options=q.get('options') or [])
                         for q in (request.get('input') or {}).get('questions') or []]
            # The CLI applies settings deny rules and PreToolUse hooks before it asks,
            # so only requests those guards let through reach this point.
            answer = self._policy_answer(run, tool_name, request.get('input') or {}) if kind == 'approval' else None
            if answer and answer[0] == 'route':
                self._send(ctrl, {'type': 'control_response', 'response': {
                    'subtype': 'success', 'request_id': ev['request_id'],
                    'response': {'behavior': 'deny', 'message': answer[1], 'interrupt': False}}})
                self.store.emit(rid, 'approval_routed', {'request_id': request_id, 'name': tool_name, 'provider': 'claude',
                                                         'summary': self._summary(request.get('input'))})
                return True
            if kind == 'approval' and tool_name and policy['auto_approve_tools'] and answer and answer[0] == 'allow':
                self._send(ctrl, {'type': 'control_response', 'response': {
                    'subtype': 'success', 'request_id': ev['request_id'],
                    'response': {'behavior': 'allow', 'updatedInput': request.get('input') or {}}}})
                self.store.emit(rid, 'approval_auto_accepted', {'request_id': request_id, 'name': tool_name, 'provider': 'claude',
                                                                'summary': self._summary(request.get('input'))})
                return True
            if kind == 'question' and policy['agent_questions'] != 'ask':
                self._send(ctrl, {'type': 'control_response', 'response': {
                    'subtype': 'success', 'request_id': ev['request_id'],
                    'response': {'behavior': 'deny', 'message': DEFER_ANSWER, 'interrupt': False}}})
                self.store.emit(rid, 'question_deferred', {'request_id': request_id, 'name': tool_name, 'provider': 'claude',
                                                           'questions': questions})
                return True
            ctrl.setdefault('pending_requests', {})[request_id] = {'native_id': ev['request_id'], 'kind': kind, 'params': request}
            payload = dict(request, request_id=request_id, name=request.get('tool_name'),
                           availableDecisions=['accept', 'decline', 'cancel'], human_required=True)
            if kind == 'question':
                payload['questions'] = questions
            self.store.emit(rid, kind, payload)
            self._human_required(rid, run, request_id, kind, 'questions_set_to_ask' if kind == 'question' else 'auto_approve_off',
                                 tool_name, questions)
        else:
            self._send(ctrl, {'type': 'control_response', 'response': {
                'subtype': 'error', 'request_id': ev['request_id'],
                'error': 'Unsupported client request: ' + str(request.get('subtype'))}})
            self.store.emit(rid, 'unsupported_request', request)
        return True

    # ------------------------------------------------------------ policy and decisions
    def policy(self, cwd=''):
        """Global settings in data/config.json, overridden by the longest matching
        `workspace_rules` path. Defaults: approve tools, defer questions, compact at 50%."""
        cfg = stream._jload(str(self.config_path), {})
        cfg = cfg if isinstance(cfg, dict) else {}
        compact = cfg.get('auto_compact') if isinstance(cfg.get('auto_compact'), dict) else {}
        result = {'auto_approve_tools': cfg.get('auto_approve_tools', True) is not False,
                  'agent_questions': 'ask' if cfg.get('agent_questions') == 'ask' else 'defer',
                  'auto_compact': compact.get('enabled', True) is not False,
                  'auto_compact_threshold': float(compact.get('threshold') or 0.5),
                  'auto_compact_idle_minutes': float(compact.get('idle_minutes') or 0)}
        cwd, best = str(cwd or ''), -1
        for rule in cfg.get('workspace_rules') or []:
            path = str((rule or {}).get('path') or '').rstrip('/')
            if path and (cwd == path or cwd.startswith(path + '/')) and len(path) > best:
                best = len(path)
                for name in ('auto_approve_tools', 'auto_compact'):
                    if isinstance(rule.get(name), bool):
                        result[name] = rule[name]
                if rule.get('agent_questions') in ('ask', 'defer'):
                    result['agent_questions'] = rule['agent_questions']
        return result

    def _policy_answer(self, run, tool_name, tool_input):
        """approval_policy.py for one request: ('allow', reason), ('route', message to the agent) after filing the
        verification request, or None (her answer is needed). Without the policy file every request is allowed as before."""
        if approval_policy is None:
            return ('allow', 'no policy file')
        cwd = run.get('cwd') or ''
        answer, reason, hits = approval_policy.permission_decision(tool_name, tool_input, cwd)
        if answer != 'route':
            return (answer, reason) if answer else None
        data = {'cwd': cwd, 'session_id': run.get('session_id'), 'hook_event_name': 'broker'}
        # the hooks' own path: an approved new folder or a one-time token allows it; otherwise the request is filed
        out = approval_policy.gate(data, tool_name, tool_input, (answer, reason, hits), 'PreToolUse') or {}
        hs = out.get('hookSpecificOutput') or {}
        return ('allow' if hs.get('permissionDecision') == 'allow' else 'route', hs.get('permissionDecisionReason') or reason)

    @staticmethod
    def _summary(value):
        if isinstance(value, dict):
            value = value.get('command') or value.get('file_path') or value.get('path') or value.get('url') or json.dumps(value, ensure_ascii=False)
        elif isinstance(value, list):
            value = ' '.join(str(v) for v in value)
        return str(value or '')[:200]

    def _human_required(self, rid, run, request_id, kind, reason, name, questions):
        """The one event that means only the user can unblock the run (clients chime on it)."""
        self.store.emit(rid, 'human_required', {'run_id': rid, 'key': run.get('key') or run.get('conversation_key'),
                                                'request_id': request_id, 'kind': kind, 'reason': reason,
                                                'provider': run.get('provider'), 'name': name, 'questions': questions})

    def decisions(self, rid):
        """Every automatic decision and agent question in a run, open ones flagged."""
        items, pending = [], {}
        for event in self.store.events(rid):
            data, name = event.get('data') or {}, event['event']
            base = {'seq': event['seq'], 'ts': event['ts'], 'request_id': data.get('request_id'),
                    'provider': data.get('provider'), 'name': data.get('name') or data.get('method')}
            if name == 'approval_auto_accepted':
                items.append(dict(base, kind='approval', resolution='auto_accepted', summary=data.get('summary', ''), open=False))
            elif name == 'question_deferred':
                items.append(dict(base, kind='question', resolution='deferred', questions=data.get('questions') or [], open=True))
            elif name == 'unsupported_request':
                items.append(dict(base, kind='request', resolution='declined', name=data.get('method') or data.get('subtype'),
                                  summary=self._summary((data.get('params') or {}).get('reason') or data.get('params') or ''), open=True))
            elif name == 'human_required':
                pending[str(data.get('request_id'))] = dict(base, kind=data.get('kind'), resolution='pending',
                                                            reason=data.get('reason'), questions=data.get('questions') or [], open=True)
                items.append(pending[str(data.get('request_id'))])
            elif name == 'request_resolved' and str(data.get('request_id')) in pending:
                pending.pop(str(data['request_id'])).update(resolution='answered', open=False)
        return {'ok': True, 'run_id': rid, 'items': items, 'open_count': sum(1 for i in items if i['open'])}

    # ------------------------------------------------------------ turn boundary
    def _track_context(self, rid, ctrl, tokens=None, window=None, model=None):
        """Context occupancy of the root thread. Below the threshold re-arms auto-compaction."""
        ctx = ctrl.setdefault('context', {})
        if model:
            if ctx.get('model') and ctx['model'] != model:
                ctx.clear()  # Do not pair different models' measurements.
                ctrl['compact_spent'] = False
            ctx['model'] = model
        if tokens is not None:
            ctx['tokens'] = int(tokens)
            if not window:
                ctx.pop('window', None)  # Wait for the matching result's window.
        if window:
            ctx['window'] = int(window)
        ctx.pop('ratio', None)
        if ctx.get('tokens') is not None and ctx.get('window'):
            ctx['ratio'] = round(ctx['tokens'] / ctx['window'], 4)
            run = self.store.get(rid)
            if ctx['ratio'] < self.policy(run.get('cwd'))['auto_compact_threshold']:
                ctrl['compact_spent'] = False
        self.store.update(rid, context=dict(ctx, at=now()))

    def _after_turn(self, rid, run, ctrl):
        """A root turn ended: auto-compact first, then the next queued message.

        Never while a steer or queued message is unread or a compaction runs."""
        with ctrl.setdefault('control_lock', threading.RLock()):
            if (ctrl.get('stop') or ctrl.get('closing') or ctrl.get('remote_disabling')
                    or ctrl.get('remote_disconnect_pending') or run.get('operation') == 'compact'):
                return
            if ctrl.get('auto_compacting'):
                if ctrl['protocol'] != 'claude-stream':
                    return  # Codex: wait for thread/compacted
                ctrl['auto_compacting'] = False
                if not ctrl.pop('auto_compact_confirmed', False):
                    ctrl['compact_failed'] = True
                    self.store.emit(rid, 'control_error', {'action': 'auto_compact',
                                    'message': 'Provider did not confirm automatic compaction; queued messages remain held. Send an explicit follow-up or resume this conversation to continue.'})
                    return
            if ctrl.get('compact_failed'):
                return
            if (ctrl.get('background') or ctrl.get('pending_requests') or ctrl.get('turn_open')
                    or 'goal_get_pending' in ctrl or 'goal_set_pending' in ctrl):
                ctrl['after_turn_deferred'] = True
                return
            ctrl.pop('after_turn_deferred', None)
            if any(i.get('results', 0) < 2 for i in ctrl.get('claude_inputs') or []):
                return
            if any(p.get('kind') in ('steer', 'queue', 'auto_compact') for p in (ctrl.get('pending_rpc') or {}).values()):
                ctrl['after_turn_deferred'] = True
                return
            if not self._auto_compact(rid, run, ctrl):
                self._deliver_next(rid, run, ctrl)

    def _auto_compact(self, rid, run, ctrl):
        policy = self.policy(run.get('cwd'))
        ctx = ctrl.get('context') or {}
        if not policy['auto_compact'] or ctrl.get('compact_spent') or not ctx.get('window'):
            return False
        if ctx.get('tokens', 0) / ctx['window'] < policy['auto_compact_threshold']:
            return False
        ctrl['compact_spent'] = True  # once per crossing; re-armed when usage drops below
        try:
            if ctrl['protocol'] == 'claude-stream':
                self._send(ctrl, self._claude_input({'prompt': '/compact', 'attachment_records': []}))
            else:
                request = self._rpc_id(ctrl)
                ctrl.setdefault('pending_rpc', {})[str(request)] = {'kind': 'auto_compact'}
                self._send(ctrl, {'id': request, 'method': 'thread/compact/start', 'params': {'threadId': run.get('session_id')}})
        except ValueError as exc:
            self.store.emit(rid, 'control_error', {'action': 'auto_compact', 'message': str(exc)})
            return False
        ctrl['auto_compacting'] = True
        ctrl['turn_open'] = True
        self.store.emit(rid, 'auto_compact', {'provider': run.get('provider'), 'context_tokens': ctx.get('tokens'),
                                              'context_window': ctx['window'], 'ratio': ctx.get('ratio'),
                                              'threshold': policy['auto_compact_threshold'], 'trigger': 'turn_end'})
        return True

    def _auto_compacted(self, rid, run, ctrl, post_tokens=None):
        """Native confirmation of an automatic compaction."""
        if not ctrl.get('auto_compacting'):
            return
        ctrl['auto_compact_confirmed'] = True
        self.store.emit(rid, 'compacted', {'provider': run.get('provider'), 'session_id': run.get('session_id'), 'trigger': 'auto'})
        if post_tokens is not None:
            self._track_context(rid, ctrl, tokens=post_tokens)
        if ctrl['protocol'] != 'claude-stream':
            ctrl['auto_compacting'] = False
            if not ctrl.get('turn_id'):
                ctrl['turn_open'] = False
                self._after_turn(rid, run, ctrl)

    def idle_compactions(self):
        """Opt-in: compact a finished local session idle for N minutes above the threshold."""
        policy = self.policy()
        if policy['auto_compact_idle_minutes'] <= 0:
            return []
        started, seen = [], set()
        with self.lock:
            busy = set(self.active)
        index = None
        for run in self.store.list():
            key = run.get('key')
            if not key or key in seen:
                continue
            seen.add(key)  # newest run of each conversation only
            ctx = run.get('context') or {}
            local = self.policy(run.get('cwd'))
            try:
                idle = time.time() - datetime.datetime.fromisoformat(run.get('finished_at') or '').timestamp()
            except ValueError:
                continue
            if (run['id'] in busy or run.get('status') != 'completed' or run.get('operation') == 'compact'
                    or run.get('host_id', 'mac') not in ('mac', 'local') or not local['auto_compact']
                    or not ctx.get('window') or ctx.get('tokens', 0) / ctx['window'] < local['auto_compact_threshold']
                    or idle < policy['auto_compact_idle_minutes'] * 60):
                continue
            index = index if index is not None else stream._jload(stream.INDEX, {})
            try:
                spec = prepare_run({'key': key, 'operation': 'compact', 'request_id': 'idle-compact-' + run['id']},
                                   index, lambda k: {})
                spec['original_key'] = key
                started.append(self.start(spec)['id'])
            except ValueError:
                continue  # busy elsewhere or not resumable; the next pass tries again
        return started

    # ------------------------------------------------------------ queued messages
    def _queue_path(self, qkey):
        return self.store.root / 'queues' / (hashlib.sha256(qkey.encode()).hexdigest()[:40] + '.json')

    def _queue_items(self, qkey):
        return stream._jload(str(self._queue_path(qkey)), {}).get('items') or []

    def _queue_save(self, qkey, items):
        history = [i for i in items if i['state'] not in QUEUE_PENDING][-QUEUE_HISTORY:]
        items = [i for i in items if i['state'] in QUEUE_PENDING or i in history]
        self._queue_path(qkey).parent.mkdir(exist_ok=True, mode=0o700)
        stream._jsave(str(self._queue_path(qkey)), {'key': qkey, 'items': items}, indent=1)

    def _queue_target(self, key):
        """(queue key, active run id, control) for a chat or logical conversation key."""
        with self.lock:
            active = list(self.active.items())
        for rid, ctrl in active:
            run = self.store.get(rid)
            if key in (run.get('key'), run.get('conversation_key'), rid) and run['status'] not in TERMINAL:
                qkey = run.get('conversation_key') or run.get('key')
                if not qkey:
                    raise QueueError('run_starting', 'The agent is still starting. Try again in a few seconds; your draft is kept.')
                return qkey, rid, ctrl
        latest = next((r for r in self.store.list() if key in (r.get('key'), r.get('conversation_key'))), None)
        return (latest or {}).get('conversation_key') or (latest or {}).get('key') or key, None, None

    def queue_view(self, qkey, rid=None, all_items=False, items=None):
        items = self._queue_items(qkey) if items is None else items
        pending = [i for i in items if i['state'] in QUEUE_PENDING]
        for position, item in enumerate(pending):
            item['position'] = position
        revision = hashlib.sha256(json.dumps(pending, sort_keys=True).encode()).hexdigest()
        return {'key': qkey, 'revision': revision, 'active_run_id': rid, 'held': bool(pending) and not rid,
                'items': items if all_items else pending}

    def _queue_set(self, qkey, item_id, **fields):
        with self.queue_lock:
            items = self._queue_items(qkey)
            item = next((i for i in items if i['id'] == item_id), None)
            if item:
                item.update(fields, updated_at=now(), version=item.get('version', 1) + 1)
                self._queue_save(qkey, items)
            return item, items

    def _queue_emit(self, rid, qkey, action, item, items=None):
        if not rid:
            return
        view = self.queue_view(qkey, rid, items=items)
        change = {'action': action, 'item_id': item['id'], 'state': item['state']}
        if action in ('delivered', 'failed'):
            change['item'] = item
        self.store.emit(rid, 'queue', {'key': qkey, 'revision': view['revision'], 'items': view['items'], 'change': change})

    def _between_turns(self, ctrl):
        if (ctrl.get('turn_open') or ctrl.get('auto_compacting') or ctrl.get('closing') or ctrl.get('stop')
                or ctrl.get('background') or ctrl.get('pending_requests') or ctrl.get('compact_failed')
                or ctrl.get('remote_disabling') or ctrl.get('remote_disconnect_pending')
                or 'goal_get_pending' in ctrl or 'goal_set_pending' in ctrl):
            return False
        if ctrl['protocol'] == 'claude-stream':
            return bool(ctrl.get('initialized')) and not any(i.get('results', 0) < 2 for i in ctrl.get('claude_inputs') or [])
        return not ctrl.get('turn_id')

    def _deliver_next(self, rid, run, ctrl):
        """Send the first queued message as the next user turn. Caller holds the control lock."""
        qkey = run.get('conversation_key') or run.get('key')
        if not qkey:
            return False
        with self.queue_lock:
            items = self._queue_items(qkey)
            item = next((i for i in items if i['state'] == 'queued'), None)
            if not item:
                return False
            item.update(state='delivering', run_id=rid, updated_at=now())
            self._queue_save(qkey, items)
        try:
            attachments = [self.store.attachment(aid) for aid in item.get('attachments') or []]
            pending = {'kind': 'queue', 'message': item['text'], 'attachments': attachments,
                       'queue_item': item['id'], 'queue_key': qkey}
            if ctrl['protocol'] == 'claude-stream':
                wire = self._claude_input({'prompt': item['text'], 'attachment_records': attachments})
                self._send(ctrl, wire)
                ctrl.setdefault('claude_inputs', []).append(dict(pending, wire=wire['message']['content'][0]['text'].strip(), results=0))
            else:
                self._send_steering(ctrl, run.get('session_id'), pending)
        except ValueError as exc:
            item, items = self._queue_set(qkey, item['id'], state='failed', error=str(exc))
            self._queue_emit(rid, qkey, 'failed', item, items)
            return False
        ctrl['turn_open'] = True
        self._cancel_idle(ctrl)
        self._queue_emit(rid, qkey, 'deliver', item)
        return True

    def _queue_confirmed(self, rid, pending):
        item, items = self._queue_set(pending['queue_key'], pending['queue_item'], state='delivered')
        if item:
            self._queue_emit(rid, pending['queue_key'], 'delivered', item, items)

    def _queue_failed(self, rid, pending, error):
        item, items = self._queue_set(pending['queue_key'], pending['queue_item'], state='failed', error=error)
        if item:
            self._queue_emit(rid, pending['queue_key'], 'failed', item, items)

    def queue_action(self, body):
        action, key = body.get('action'), str(body.get('key') or '')
        if action not in ('enqueue', 'edit', 'move', 'steer', 'cancel'):
            raise ValueError('unknown queue action')
        if not key:
            raise ValueError('key is required')
        qkey, rid, ctrl = self._queue_target(key)
        with (ctrl.setdefault('control_lock', threading.RLock()) if ctrl else threading.RLock()):
            if ctrl and self.store.get(rid)['status'] in TERMINAL:
                qkey, rid, ctrl = qkey, None, None
            with self.queue_lock:
                items = self._queue_items(qkey)
                if action == 'enqueue':
                    request_id = str(body.get('request_id') or '')[:200]
                    item = next((i for i in items if request_id and i.get('request_id') == request_id), None)
                    if item:
                        return {'ok': True, 'queue': self.queue_view(qkey, rid, items=items), 'item': item}
                    if not ctrl:
                        raise QueueError('no_active_run', 'No agent is running in this conversation. Send the message normally; your draft is kept.')
                    text = str(body.get('message') or '').strip()
                    attachments = [str(a) for a in body.get('attachments') or []]
                    for aid in attachments:
                        self.store.attachment(aid)
                    if not text and not attachments:
                        raise ValueError('empty message')
                    item = {'id': 'q_' + uuid.uuid4().hex, 'key': key, 'text': text, 'attachments': attachments,
                            'state': 'queued', 'version': 1, 'created_at': now(), 'updated_at': now(),
                            'client_id': str(body.get('client_id') or '')[:200], 'request_id': request_id,
                            'run_id': None, 'error': None}
                    items.append(item)
                else:
                    item = next((i for i in items if i['id'] == body.get('item_id')), None)
                    allowed = ('queued', 'failed') if action in ('edit', 'cancel') else ('queued',)
                    if not item or item['state'] not in allowed:
                        raise QueueError('queue_item_resolved', 'This queued message was already sent, steered or cancelled.')
                    if body.get('version') is not None and int(body['version']) != item.get('version', 1):
                        raise QueueError('queue_conflict', 'Another window changed this queued message. Reload the queue.')
                    if action == 'steer':
                        if not ctrl:
                            raise QueueError('no_active_run', 'No agent is running in this conversation. Use Deliver now instead.')
                    elif action == 'edit':
                        text = str(body.get('message') or '').strip()
                        attachments = [str(a) for a in body['attachments']] if isinstance(body.get('attachments'), list) else item.get('attachments') or []
                        if not text and not attachments:
                            raise ValueError('empty message')
                        item.update(text=text, attachments=attachments, state='queued', error=None)
                    elif action == 'cancel':
                        item['state'] = 'cancelled'
                    elif action == 'move':
                        queued = [i for i in items if i['state'] in QUEUE_PENDING]
                        items.remove(item)
                        index = max(0, min(int(body.get('index') or 0), len(queued) - 1))
                        rest = [i for i in queued if i is not item]
                        anchor = rest[index] if index < len(rest) else None
                        items.insert(items.index(anchor) if anchor else len(items), item)
                    if action != 'steer':
                        item.update(updated_at=now(), version=item.get('version', 1) + 1)
                if action != 'steer':
                    self._queue_save(qkey, items)
            if action == 'steer':
                self._control(rid, {'action': 'steer', 'message': item['text'], 'attachments': item.get('attachments') or []})
                item, items = self._queue_set(qkey, item['id'], state='steered', run_id=rid)
            self._queue_emit(rid, qkey, action, item, items)
            if ctrl and action in ('enqueue', 'edit') and self._between_turns(ctrl):
                self._deliver_next(rid, self.store.get(rid), ctrl)
                self._settle(rid, ctrl)
                item = next((i for i in self._queue_items(qkey) if i['id'] == item['id']), item)
            return {'ok': True, 'queue': self.queue_view(qkey, rid), 'item': item}

    def _persist(self, run, launch, st):
        if run['host_id'] not in ('mac', 'local'):
            # Remote native transcript will be picked up by remote_sync; store a
            # cleaned local projection now with the real execution identity.
            path = ''
        elif run['provider'] == 'claude':
            path = stream._claude_transcript(launch.get('env') or {}, run['cwd'], st.sid)
        else:
            path = stream._codex_rollout(launch.get('env') or {}, st.sid)
        stream._persist(run['key'], run['provider'], st.sid, run['cwd'], path,
                        run['message'], run['text'], not bool(run.get('original_key')), run['created_at'], now())
        native_chat = None
        if run['provider'] == 'claude' and (run.get('quota_failover') or run.get('quota_failover_from')) and path and Path(path).is_file():
            import indexer
            native_chat = indexer.parse_claude(path)
        # Bind identity and provenance on both index and cleaned projection. The
        # raw run journal remains the authoritative rich timeline.
        with stream.LOCK:
            for path in (Path(stream.INDEX), Path(stream.CHATS) / (run['key'] + '.json')):
                data = stream._jload(str(path), {})
                record = data.get(run['key']) if path == Path(stream.INDEX) else data
                if isinstance(record, dict):
                    if path != Path(stream.INDEX) and native_chat:
                        record.update(native_chat)
                    for key in ('account_id', 'host_id', 'conversation_key', 'source_key', 'source_session_id', 'source_provider', 'source_turn', 'context_omitted_chars', 'quota_failover_from'):
                        if key in run:
                            record[key] = run[key]
                    record['execution_host'] = run['host_id']
                    record['node'] = run['host_id']
                    record['last_run_id'] = run['id']
                    if path != Path(stream.INDEX) and record.get('quota_failover_from'):
                        import indexer
                        indexer.mark_quota_handoff_seed(record, run)
                    stream._jsave(str(path), data, indent=1)


_BROKER = None
_INIT_LOCK = threading.Lock()


def instance():
    global _BROKER
    with _INIT_LOCK:
        if _BROKER is None:
            from accounts import collect_run_notice
            _BROKER = Broker(RunStore(DATA / 'execution', collect_run_notice))
        return _BROKER


def guarded(fn):
    def handle(req, query, body):
        try:
            return fn(req, query or {}, body or {})
        except SessionBusy as exc:
            return {'ok': False, 'code': 'session_busy', 'active_run_id': exc.active_run_id, 'error': str(exc)}, 409
        except RequestResolved as exc:
            return {'ok':False, 'code':'request_resolved', 'request_id':exc.request_id, 'error':str(exc)}, 409
        except QueueError as exc:
            return {'ok': False, 'code': exc.code, 'error': str(exc)}, 409
        except (ValueError, KeyError, TypeError) as exc:
            return {'ok': False, 'error': str(exc)}, 400
    return handle


@guarded
def create_run(req, query, body):
    obj = instance()
    old = obj.store.find_request(body.get('request_id'))
    if old:
        return {'ok': True, 'run': old, 'events': obj.store.events(old['id'])}
    index = stream._jload(stream.INDEX, {})
    runs = obj.store.list()
    identity_fields = ('account_id', 'execution_host', 'host_id', 'conversation_key',
                       'source_key', 'source_session_id', 'source_provider', 'source_turn', 'quota_failover_from')
    latest = {}
    for run in runs:
        if run.get('key') and run.get('session_id'):
            latest.setdefault(run['key'], run)
    for key, run in latest.items():
        if key in index:
            index[key] = dict(index[key], **{k: run[k] for k in identity_fields if k in run})
    def load_chat(key):
        chat = stream._jload(str(Path(stream.CHATS) / (safe_id(key) + '.json')), {})
        binding = latest.get(key) or {}
        return dict(chat, **{k: binding[k] for k in identity_fields if k in binding})
    def load_evidence(key):
        lines = []
        for run in reversed([r for r in runs if r.get('key') == key][:40]):
            for attachment in run.get('attachment_records') or []:
                lines.append('Attachment: ' + json.dumps(attachment, ensure_ascii=False))
            for event in obj.store.events(run['id']):
                if event['event'] in ('tool', 'tool_result', 'output', 'file_change', 'steered'):
                    lines.append(json.dumps({'event': event['event'], 'data': event['data']}, ensure_ascii=False))
        return '\n'.join(lines)
    spec = prepare_run(body, index, load_chat, load_evidence)
    spec['original_key'] = body.get('key') if not spec['handoff'] else ''
    run = obj.start(spec)
    return {'ok': True, 'run': run, 'events': obj.store.events(run['id'])}


@guarded
def recover_run(req, query, body):
    import auth_recovery
    return auth_recovery.recover(instance(), body)


@guarded
def get_run(req, query, body):
    store = instance().store
    rid = query.get('id') or body.get('id')
    after = int(query.get('after') or 0)
    with store.lock:
        events = store.events(rid, after, limit=2000 if after == 0 else None, before=query.get('before'))
        first = events[0]['seq'] if events else None
        truncated = first if after == 0 and first and any(seq < first for seq in store.offsets.get(rid, {})) else None
        return {'ok': True, 'run': store.get(rid), 'events': events, 'events_truncated_before': truncated}


@guarded
def list_runs(req, query, body):
    from notices import summary
    runs = instance().store.list(summaries=True, key=query.get('key'))[:100]
    return {'ok': True, 'runs': runs, 'notice_center': summary(DATA / 'notices.json')}


@guarded
def conversation_run(req, query, body):
    key = str(query.get('key') or '')
    # These are ownership identities. Ancestry (source/origin) must never cause
    # an unrelated fork to take over its parent's viewer.
    run = next((r for r in instance().store.list()
                if key and key in (r.get('key'), r.get('conversation_key'))), None)
    return {'ok': True, 'run': run, 'events': []}


@guarded
def control_run(req, query, body):
    return instance().control(body.get('id'), body)


@guarded
def upload(req, query, body):
    item = instance().store.attach(body.get('name'), body.get('mime'), body.get('data_base64') or '')
    return {'ok': True, 'attachment': item}


@guarded
def attachment_content(req, query, body):
    item = instance().store.attachment(query.get('id'))
    raw = Path(item['path']).read_bytes()
    req.send_response(200)
    safe_inline = item['mime'] in ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif')
    req.send_header('Content-Type', item['mime'] if safe_inline else 'application/octet-stream')
    req.send_header('Content-Length', str(len(raw)))
    req.send_header('Content-Disposition', 'inline' if safe_inline else 'attachment')
    req.send_header('X-Content-Type-Options', 'nosniff')
    req.end_headers()
    req.wfile.write(raw)


@guarded
def event_stream(req, query, body):
    store = instance().store
    rid = query.get('id')
    after = int(query.get('after') or req.headers.get('Last-Event-ID') or 0)
    store.get(rid)
    req.send_response(200)
    req.send_header('Content-Type', 'text/event-stream')
    req.send_header('Cache-Control', 'no-cache')
    req.send_header('Connection', 'close')
    req.end_headers()
    req.close_connection = True
    try:
        while True:
            events = store.events(rid, after)
            for entry in events:
                req.wfile.write(f"id: {entry['seq']}\nevent: {entry['event']}\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n".encode())
                req.wfile.flush()
                after = entry['seq']
            if store.get(rid)['status'] in TERMINAL and not store.events(rid, after):
                return None
            store.wait_for_change(rid, after, timeout=10)
            req.wfile.write(b': keepalive\n\n')
            req.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        return None  # only the subscriber ends, never the provider process


@guarded
def queue_get(req, query, body):
    obj = instance()
    qkey, rid, _ctrl = obj._queue_target(str(query.get('key') or ''))
    return {'ok': True, 'queue': obj.queue_view(qkey, rid, all_items=query.get('all') == '1')}


@guarded
def queue_post(req, query, body):
    obj = instance()
    if body.get('action') != 'deliver':
        return obj.queue_action(body)
    # No run is active: start one whose prompt is the first pending message.
    key = str(body.get('key') or '')
    qkey, rid, _ctrl = obj._queue_target(key)
    if rid:
        raise QueueError('run_active', 'An agent is already running; queued messages follow at its next turn end.')
    with obj.queue_lock:
        items = obj._queue_items(qkey)
        item = next((i for i in items if i['state'] == 'queued'), None)
        if not item:
            raise QueueError('queue_item_resolved', 'Nothing is queued in this conversation.')
        item.update(state='delivering', updated_at=now())
        obj._queue_save(qkey, items)
    run_body = {k: body[k] for k in ('model', 'effort', 'account') if body.get(k)}
    result = create_run(req, {}, dict(run_body, key=item.get('key') or key, message=item['text'],
                                      attachments=item.get('attachments') or [], request_id='queue-' + item['id']))
    if isinstance(result, tuple):
        obj._queue_set(qkey, item['id'], state='queued', error=result[0].get('error'))
        return result
    item, _items = obj._queue_set(qkey, item['id'], state='delivered', run_id=result['run']['id'])
    return {'ok': True, 'queue': obj.queue_view(qkey, result['run']['id']), 'item': item, 'run': result['run']}


@guarded
def run_decisions(req, query, body):
    obj = instance()
    rid = query.get('id')
    if not rid and query.get('key'):
        rid = next((r['id'] for r in obj.store.list() if query['key'] in (r.get('key'), r.get('conversation_key'))), None)
    if not rid:
        raise ValueError('id or key is required')
    return obj.decisions(rid)


ROUTES = {
    ('GET', '/api/attention'): lambda req, query, body: {**instance().attention(), 'watcher_sound': attention_watch.active(DATA)},
    ('GET', '/api/run/queue'): queue_get,
    ('POST', '/api/run/queue'): queue_post,
    ('GET', '/api/run/decisions'): run_decisions,
    ('POST', '/api/runs'): create_run,
    ('POST', '/api/run/recover'): recover_run,
    ('GET', '/api/runs'): list_runs,
    ('GET', '/api/run'): get_run,
    ('GET', '/api/conversation/run'): conversation_run,
    ('GET', '/api/run/events'): event_stream,
    ('POST', '/api/run/control'): control_run,
    ('POST', '/api/attachments'): upload,
    ('GET', '/api/attachment'): attachment_content,
    ('GET', '/api/capabilities'): lambda req, query, body: {'ok': True, 'providers': CAPABILITIES, 'settings': ['auto_approve_tools', 'attention_sound', 'agent_questions', 'auto_compact']},
}
BACKGROUND = [(lambda: instance().idle_compactions(), 60)]
