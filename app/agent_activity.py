"""Whether a delegated child agent is running, from transcript evidence.

Claude Code subagents and Codex spawned agents run inside their root
session's process, so they never appear as broker runs or live process rows.
Their activity is read from the files the providers write:

Claude child (``~/.claude/projects/<dir>/<root>/subagents/agent-<id>.jsonl``)
  1. Child tail: the last ``assistant`` record has ``message.stop_reason``
     ``end_turn``/``stop_sequence`` and no later ``user`` text record: finished.
  2. Parent transcript: ``<task-notification>`` with ``<task-id>`` equal to the
     child's agent id (``queue-operation`` content or a ``user`` message) gives
     ``<status>`` completed / failed / killed / stopped; a synchronous Agent or
     Task ``toolUseResult`` with this ``agentId`` and a status other than
     ``async_launched`` means it returned. Ignored when the child wrote later.
  3. No completion and the child file was written in the last 120 s: running.
  4. No completion: the root session's process (``live`` inventory, Mac) is
     alive: running; verified gone: stopped.
Codex child (``~/.codex/sessions/.../rollout-*.jsonl``)
  1. Last lifecycle ``event_msg``: ``task_complete`` (finished, or failed when it
     carries ``error``), ``turn_aborted`` (stopped), ``error`` (failed),
     ``task_started`` (a turn is open).
  2. Open turn and the rollout was written in the last 10 minutes: running.
  3. Open turn and the fresh live inventory shows no Codex process on this
     Mac: stopped. Otherwise unknown.
Results are cached by file mtime and size; the parent scan is incremental.
"""
import calendar
import json
import os
import re
import sys
import threading
import time

TAIL_BYTES = 256 * 1024
CLAUDE_FRESH_S = 120
CODEX_FRESH_S = 600
LIVE_FRESH_S = 180
_NOTE = re.compile(r'<task-id>([^<]+)</task-id>.*?<status>([^<]+)</status>', re.S)
_TERMINAL = {'completed': 'finished', 'failed': 'failed', 'killed': 'stopped', 'stopped': 'stopped'}

_TAILS = {}    # path -> (mtime_ns, size, state)
_PARENTS = {}  # path -> {'ino', 'offset', 'reports': {agent_id: (status, ts)}}
_LOCK = threading.Lock()


def _stat(path):
    try:
        return os.stat(path)
    except (OSError, TypeError, ValueError):
        return None


def transcript_path(meta):
    sig = meta.get('src_sig') or []
    if sig and isinstance(sig[0], (list, tuple)) and sig[0]:
        return sig[0][0]
    return meta.get('file') or ''


def _records(path, size, whole=False):
    with open(path, 'rb') as fh:
        if size > TAIL_BYTES and not whole:
            fh.seek(size - TAIL_BYTES)
            fh.readline()  # drop the partial first line
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _claude_tail(path, size, whole=False):
    """('ended', ts) after a final end_turn, else ('open', ts) or (None, '')."""
    state, ts = None, ''
    for d in _records(path, size, whole):
        t = d.get('type')
        if t == 'assistant':
            reason = (d.get('message') or {}).get('stop_reason')
            state, ts = ('ended' if reason in ('end_turn', 'stop_sequence') else 'open'), d.get('timestamp') or ts
        elif t == 'user':
            content = (d.get('message') or {}).get('content')
            if isinstance(content, str) or any(isinstance(b, dict) and b.get('type') == 'text' for b in content or []):
                state, ts = 'open', d.get('timestamp') or ts
            elif state != 'ended':
                state, ts = 'open', d.get('timestamp') or ts
    return state, ts


def _codex_tail(path, size, whole=False):
    state, ts = None, ''
    for d in _records(path, size, whole):
        if d.get('type') != 'event_msg':
            continue
        p = d.get('payload') if isinstance(d.get('payload'), dict) else {}
        pt = p.get('type')
        if pt == 'task_started':
            state = 'open'
        elif pt == 'task_complete':
            state = 'failed' if p.get('error') else 'ended'
        elif pt == 'turn_aborted':
            state = 'aborted'
        elif pt == 'error':
            state = 'failed'
        else:
            continue
        ts = d.get('timestamp') or ts
    return state, ts


def tail_state(path, source):
    st = _stat(path)
    if not st:
        return None, '', None
    hit = _TAILS.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2] + (st.st_mtime,)
    try:
        read = _codex_tail if source == 'codex' else _claude_tail
        state = read(path, st.st_size)
        if state[0] is None and st.st_size > TAIL_BYTES:
            state = read(path, st.st_size, True)  # a long final output can hide the last event
    except OSError:
        return None, '', st.st_mtime
    _TAILS[path] = (st.st_mtime_ns, st.st_size, state)
    return state + (st.st_mtime,)


def parent_reports(path):
    """Agent id -> (status, timestamp) recorded in a Claude parent transcript."""
    st = _stat(path)
    if not st:
        return {}
    with _LOCK:
        entry = _PARENTS.get(path)
        if not entry or entry['ino'] != st.st_ino or st.st_size < entry['offset']:
            entry = _PARENTS[path] = {'ino': st.st_ino, 'offset': 0, 'reports': {}}
        if st.st_size == entry['offset']:
            return entry['reports']
        try:
            with open(path, 'rb') as fh:
                fh.seek(entry['offset'])
                for raw in fh:
                    if not raw.endswith(b'\n'):
                        break  # a line still being written; read it next time
                    entry['offset'] += len(raw)
                    if b'agentId' not in raw and b'<task-notification>' not in raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    _scan_parent_record(d, entry['reports'])
        except OSError:
            pass
        return entry['reports']


def _scan_parent_record(d, reports):
    ts = d.get('timestamp') or ''
    result = d.get('toolUseResult')
    if isinstance(result, dict) and result.get('agentId'):
        status = result.get('status') or ''
        if status == 'async_launched':
            reports.setdefault(result['agentId'], ('launched', ts))
        elif status:
            reports[result['agentId']] = (status, ts)
    t = d.get('type')
    if t == 'queue-operation' and d.get('operation') == 'enqueue':
        text = d.get('content')
    elif t == 'user':
        text = (d.get('message') or {}).get('content')
    else:
        return
    if isinstance(text, list):
        text = '\n'.join(b.get('text') or '' for b in text if isinstance(b, dict) and b.get('type') == 'text')
    if not isinstance(text, str) or '<task-notification>' not in text:
        return
    for agent_id, status in _NOTE.findall(text):
        reports[agent_id.strip()] = (status.strip(), ts)


def _live():
    """(fresh payload or None). Read only from the server's in-memory cache."""
    module = sys.modules.get('live')
    payload = getattr(module, '_CACHE', None) if module else None
    if not isinstance(payload, dict) or time.time() - (payload.get('fetched_at') or 0) > LIVE_FRESH_S:
        return None
    if not any(n.get('name') == 'mac' and n.get('ok') for n in payload.get('nodes') or []):
        return None
    return payload


def _on_mac(meta):
    host = meta.get('execution_host') or meta.get('host_id') or meta.get('node') or 'mac'
    return host in ('mac', 'local')


def _ago(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds} s ago' if seconds < 120 else f'{seconds // 60} min ago'


def _iso(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S'))
    except (TypeError, ValueError):
        return None


def child_activity(meta, index, now=None, live=None):
    """{'activity': running|finished|failed|stopped|unknown, 'activity_evidence': str}."""
    now = time.time() if now is None else now
    live = _live() if live is None else live
    source = meta.get('source')
    path = transcript_path(meta)
    state, ts, mtime = tail_state(path, source)
    age = now - mtime if mtime else None
    if source == 'codex':
        if state == 'ended':
            return _out('finished', 'Child rollout ended with task_complete')
        if state == 'aborted':
            return _out('stopped', 'Child rollout ended with turn_aborted')
        if state == 'failed':
            return _out('failed', 'Child rollout recorded an error')
        if state == 'open' and age is not None and age < CODEX_FRESH_S:
            return _out('running', 'Turn open (task_started), rollout written ' + _ago(age))
        if state == 'open' and live and _on_mac(meta) and not any(
                s.get('tool') == 'codex' and s.get('alive') and (s.get('node') or 'mac') == 'mac' for s in live.get('sessions') or []):
            return _out('stopped', 'Turn open, but no Codex process is running on this Mac')
        if state == 'open':
            return _out('unknown', 'Turn open with no completion; rollout last written ' + _ago(age or 0))
        if age is not None and age < CODEX_FRESH_S:
            return _out('running', 'Rollout created ' + _ago(age) + '; no turn event yet')
        if age is not None:
            return _out('stopped', 'No turn was recorded in the child rollout')
        return _out('unknown', 'Child rollout unavailable')
    if state == 'ended':
        return _out('finished', 'Child transcript ended with end_turn')
    parent = index.get(meta.get('parent_key') or '') or {}
    report = parent_reports(transcript_path(parent)).get(meta.get('agent_id') or '') if parent else None
    if report and report[0] != 'launched':
        reported = _iso(report[1])
        later = mtime and reported and mtime > reported + 5 and state == 'open' and age is not None and age < CLAUDE_FRESH_S
        if not later:
            activity = _TERMINAL.get(report[0], 'finished')
            return _out(activity, f'Parent recorded status {report[0]}')
    if state == 'open' and age is not None and age < CLAUDE_FRESH_S:
        return _out('running', 'No completion recorded; transcript written ' + _ago(age))
    root_sid = meta.get('root_session_id') or ''
    if live and root_sid and _on_mac(meta):
        alive = any(s.get('tool') == 'claude' and s.get('alive') and s.get('session_id') == root_sid
                    for s in live.get('sessions') or [])
        if alive:
            return _out('running', 'No completion recorded; root session process is alive')
        return _out('stopped', 'No completion recorded and the root session process has exited')
    return _out('unknown', 'No completion recorded' + ('; transcript last written ' + _ago(age) if age is not None else '; transcript unavailable'))


def _out(activity, evidence):
    return {'activity': activity, 'activity_evidence': evidence}
