#!/usr/bin/env python3
"""
indexer.py — Parse Claude Code + Codex transcripts into cleaned, display-ready chats.

For every session found under the configured roots this produces:
  data/chats/<key>.json   cleaned chat: user/assistant turns only + files changed
  data/index.json         {key: meta} master index (no turn bodies)

Incremental: a chat is re-parsed only when its source file's (mtime, size)
changed — so a chat that was continued after it was first indexed is picked up
again and its cleaned copy rewritten.

Only chats where the agent actually answered are kept: a transcript whose
assistant turns are all API errors (or that has no assistant text at all,
because the turn was interrupted) is left out of the index.  Set
``min_answer_msgs: 0`` in data/config.json to keep them.

Run:  python3 indexer.py [--full] [--prune] [--keep-missing]

--keep-missing retains prior index entries and cleaned files when a source is
unavailable. Use it for metadata migrations without pruning user history.

--prune additionally deletes cleaned copies in data/chats that no longer have an
index entry (left behind when a chat is filtered out or its transcript is gone).
They are derived data -- a later run rebuilds any copy whose transcript exists --
but deletion is opt-in so a bad filter setting cannot quietly throw work away.
"""
import json, os, re, sys, glob, time, fcntl, stat
import datetime
import hashlib
from pathlib import Path

# Bumped whenever meta_of() gains a field the rest of the pipeline reads, so
# already-indexed chats are re-parsed once instead of keeping stale metadata.
# 3: node, model_main, models, usage, usage_days (dashboard v2).
# 10: resume (context size, cache TTL, last model and effort for the resume line).
import auth_failure

META_V = 10
LINEAGE_FIELDS = ('agent_id', 'parent_agent_id', 'parent_session_id', 'parent_key',
                  'root_key', 'root_session_id', 'is_child', 'agent_label',
                  'agent_path', 'spawned_children', 'lineage_source',
                  'provider_account_id', 'usage_host')
DESKTOP_LINEAGE_SOURCE = "claude_desktop_task"
DESKTOP_LINEAGE_FIELDS = ("parent_key", "parent_session_id", "parent_agent_id",
                          "root_key", "is_child", "lineage_source")
EXECUTION_FIELDS = ('account_id', 'execution_host', 'host_id', 'conversation_key',
                    'source_key', 'source_session_id', 'source_provider', 'source_turn',
                    'context_omitted_chars', 'last_run_id', 'quota_failover_from')


def preserve_execution_metadata(chat, previous):
    for key in EXECUTION_FIELDS:
        if key in previous:
            chat[key] = previous[key]

def mark_quota_handoff_seed(chat, trusted_run=None):
    """Mark only the broker-identified seed for this exact native session."""
    provenance = chat.get('quota_failover_from') or {}
    if provenance.get('mode') == 'native_resume':
        for turn in chat.get('turns') or []:
            turn.pop('internal_handoff_seed', None)
        return
    turns = chat.get('turns') or []
    rid = chat.get('last_run_id') or ''
    if trusted_run is None and re.fullmatch(r'[A-Za-z0-9_-]+', rid):
        try:
            with open(os.path.join(DATA, 'execution', 'runs', rid, 'run.json')) as handle:
                trusted_run = json.load(handle)
        except (OSError, ValueError):
            return
    if (not isinstance(trusted_run, dict) or trusted_run.get('id') != rid or
            trusted_run.get('session_id') != chat.get('id') or
            trusted_run.get('provider') != 'claude' or
            trusted_run.get('quota_failover_from') != provenance):
        return
    if (chat.get('source') != 'claude' or not provenance.get('run_id') or
            provenance.get('native_session_id') != chat.get('id') or not turns):
        return
    turn = turns[0]
    if (turn.get('role') == 'user' and isinstance(turn.get('text'), str) and
            hashlib.sha256(turn['text'].encode('utf-8')).hexdigest() == provenance.get('prompt_sha256')):
        turn['internal_handoff_seed'] = True


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CHATS = os.path.join(DATA, "chats")
INDEX = os.path.join(DATA, "index.json")
CONFIG = os.path.join(DATA, "config.json")
HOME = os.path.expanduser("~")
CLAUDE_DESKTOP_SESSIONS = Path(HOME) / "Library/Application Support/Claude/claude-code-sessions"
NATIVE_SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)

DEFAULT_CONFIG = {
    "claude_projects_root": os.path.join(HOME, ".claude/projects"),
    "codex_sessions_root": os.path.join(HOME, ".codex/sessions"),
    "codex_index": os.path.join(HOME, ".codex/session_index.jsonl"),
    # cwds excluded from the dashboard (managed from the frontend Settings panel)
    "excluded_cwds": ["/private/tmp", "/tmp"],
    "min_user_msgs": 1,
    # a chat needs this many real answers (assistant turns that are not an API
    # error) to be indexed; 0 keeps interrupted and errored-out chats
    "min_answer_msgs": 1,
    "freshness_window": "1d",
    "port": 8744,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG):
        try:
            cfg.update(json.load(open(CONFIG)))
        except Exception:
            pass
    else:
        os.makedirs(DATA, exist_ok=True)
        json.dump(cfg, open(CONFIG, "w"), indent=2)
    # Let the portable config example use ~/.claude and ~/.codex rather than
    # embedding any one person's absolute home directory.
    for key in ("claude_projects_root", "codex_sessions_root", "codex_index"):
        if cfg.get(key):
            cfg[key] = os.path.expanduser(os.path.expandvars(str(cfg[key])))
    cfg["excluded_cwds"] = [os.path.expanduser(os.path.expandvars(str(path)))
                            for path in cfg.get("excluded_cwds", [])]
    return cfg


# ---------------------------------------------------------------- text cleaning
SYS_BLOCK = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
IDE_BLOCK = re.compile(r"<ide_(?:selection|opened_file|diagnostics)>.*?</ide_(?:selection|opened_file|diagnostics)>", re.S)
CAVEAT = re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.S)
CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
CMD_MSG = re.compile(r"<command-message>.*?</command-message>", re.S)
CMD_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
TASK_NOTIF = re.compile(r"<task-notification>.*?</task-notification>", re.S)
INTERRUPT = re.compile(r"\[Request interrupted by user[^\]]*\]")
ATTENTION_PATTERNS = (
    re.compile(r"\b(?:i|we) need (?:you|your)\b", re.I),
    re.compile(r"\b(?:please|you(?:'|’)ll need to|you need to)\s+"
               r"(?:approve|confirm|choose|select|provide|send|share|open|log in|authenticate|decide)\b", re.I),
    re.compile(r"\b(?:blocked|blocker|waiting)\b.{0,100}\b"
               r"(?:you|your|approval|decision|input|key|credential)\b", re.I | re.S),
)


# An assistant turn that is only a harness error is not an answer. Claude
# flags these with isApiErrorMessage; codex has no flag, so match the text.
ERROR_ANSWER = re.compile(
    r"^\s*(?:Failed to authenticate\.\s*)?"
    r"(?:API Error|API error|Request timed out|Request was aborted|"
    r"Execution error|Claude Code is unable|No response|"
    r"Prompt is too long|Context low)\b",
    re.I)


def is_error_answer(text):
    return bool(ERROR_ANSWER.match(text or ""))


def atomic_json_dump(obj, path, indent=None):
    """Write JSON through a temp file so a killed run cannot truncate the real
    one — index.json was corrupted this way when a SessionStart hook was cut."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def needs_attention(status, turns, explicit=False):
    """Conservatively identify a turn that cannot continue without the user."""
    turns = [t for t in turns if t.get("channel") != "analysis"]
    if status != "working" or not turns or turns[-1].get("role") != "assistant":
        return False
    if explicit:
        return True
    text = turns[-1].get("text", "")[-1600:]
    return any(pattern.search(text) for pattern in ATTENTION_PATTERNS)


def clean_user_text(t):
    """Strip harness wrappers; render slash-commands as '/name args'."""
    if not t:
        return ""
    if "Caveat: The messages below were generated by the user" in t and "<command-" not in t:
        return ""
    cmd = CMD_NAME.search(t)
    if cmd:
        name = cmd.group(1).strip().lstrip("/")
        args = CMD_ARGS.search(t)
        args = (args.group(1).strip() if args else "")
        rest = CMD_STDOUT.sub("", t)
        for rx in (CMD_NAME, CMD_ARGS, CMD_MSG, CAVEAT, SYS_BLOCK, IDE_BLOCK, TASK_NOTIF):
            rest = rx.sub("", rest)
        rest = rest.strip()
        line = ("/" + name + (" " + args if args else "")).strip()
        return (line + "\n" + rest).strip() if rest else line
    for rx in (SYS_BLOCK, IDE_BLOCK, CAVEAT, CMD_STDOUT, TASK_NOTIF, INTERRUPT):
        t = rx.sub("", t)
    return t.strip()


def text_blocks(content):
    """Extract visible text from a message content (str or block list)."""
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
    return "\n".join(out)


FILE_TOOLS = {"Edit": "edit", "Write": "write", "MultiEdit": "edit", "NotebookEdit": "edit"}
# Tool calls made through the programmatic executor can contain a JavaScript
# string whose newlines are represented literally as ``\n``. Stop at either
# representation so the patch body is never mistaken for part of a filename.
PATCH_FILE = re.compile(
    r"\*\*\* (Add|Update|Delete) File: (.+?)(?=\r?\n|\\n|$)"
)


# ---------------------------------------------------------------- token usage
USAGE_FIELDS = ("in", "out", "cache_read", "cache_create", "turns")
_DAY_CACHE = {}


def empty_usage():
    return {f: 0 for f in USAGE_FIELDS}


def local_day(ts):
    """ISO timestamp (UTC 'Z' or offset) -> plain local YYYY-MM-DD."""
    if not ts:
        return ""
    # for a UTC "Z" stamp the local date depends only on the minute
    hour = ts[:16] if ts.endswith("Z") else None
    day = _DAY_CACHE.get(hour) if hour else None
    if day is None:
        try:
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            day = dt.astimezone().strftime("%Y-%m-%d")
        except ValueError:
            day = ts[:10]
        if hour:
            _DAY_CACHE[hour] = day
    return day


def summarize_usage(events):
    """events: [(model, ts, in, out, cache_read, cache_create)], one per model
    call. Returns the index fields model_main, models, usage, usage_days."""
    models, usage, days, buckets = {}, empty_usage(), {}, {}
    for event in events:
        model, ts, tin, tout, cr, cc = event[:6]
        account = event[6] if len(event) > 6 else ''
        if model:
            models[model] = models.get(model, 0) + 1
        day = days.setdefault(local_day(ts) or "unknown", empty_usage())
        try:
            hour = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().hour
        except (ValueError, AttributeError):
            hour = None
        exact = buckets.setdefault((local_day(ts) or 'unknown', hour, model or 'unknown', account), empty_usage())
        for bucket in (usage, day, exact):
            bucket["in"] += tin
            bucket["out"] += tout
            bucket["cache_read"] += cr
            bucket["cache_create"] += cc
            bucket["turns"] += 1
    # deterministic tie-break: most turns, then model id
    model_main = min(models, key=lambda m: (-models[m], m)) if models else ""
    return {"model_main": model_main, "models": models, "usage": usage,
            "usage_days": dict(sorted(days.items())),
            "usage_buckets": [dict(day=d, hour=h, model=m, account_id=a or 'unknown',
                                   account_source='native_transcript' if a else 'unknown', **u)
                              for (d, h, m, a), u in buckets.items()]}


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- Claude parser
def parse_claude(path):
    turns, files, title = [], {}, ""
    cwd = branch = ""
    ts_first = ts_last = ""
    status = "complete"
    attention_required = False
    slug = os.path.basename(os.path.dirname(path))
    # Claude writes one line per content block, each repeating the message's
    # usage, so usage is keyed by message id (last line wins) to count once.
    usage_by_msg = {}
    agent_id = ''
    delegated = '/subagents/' in path.replace(os.sep, '/')
    calls, results = {}, []
    # What the next turn sends: the last call's input plus its reply, or the
    # compacted size when a compact boundary came after that call.
    resume = {}

    def add_file(p, op):
        if not p:
            return
        rec = files.setdefault(p, {"edits": 0, "writes": 0})
        rec["writes" if op == "write" else "edits"] += 1

    try:
        fh = open(path, errors="ignore")
    except OSError:
        return None
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        if t in ("session-owner", "tools-used", "thread_settings_applied"):
            continue
        agent_id = d.get('agentId') or agent_id
        content = (d.get('message') or {}).get('content')
        if t == 'assistant' and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') in ('Agent', 'Task'):
                    calls[block.get('id')] = block.get('input') or {}
        result = d.get('toolUseResult')
        if isinstance(result, dict) and result.get('agentId'):
            ids = [b.get('tool_use_id') for b in content or [] if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(content, list) else []
            results.append((result, ids))
        if t == "ai-title" and d.get("aiTitle"):
            title = d["aiTitle"]
        elif t == "summary" and d.get("summary") and not title:
            title = d["summary"]
        if d.get("cwd") and not cwd:
            cwd = d["cwd"]
        if d.get("gitBranch") and not branch:
            branch = d["gitBranch"]
        tsv = d.get("timestamp")
        if tsv:
            ts_first = ts_first or tsv
            ts_last = tsv
        main_chain = delegated or not d.get('isSidechain')
        if t == 'api-request' and main_chain and (delegated or not str(d.get('querySource', '')).startswith('agent:')):
            effort = ((d.get('params') or {}).get('output_config') or {}).get('effort')
            if effort:
                resume['effort'] = effort
        elif t == 'system' and d.get('subtype') == 'compact_boundary' and main_chain:
            post = (d.get('compactMetadata') or {}).get('postTokens')
            resume.update(context_tokens=_int(post) or None, compacted=True, at=tsv or '')
            resume.pop('cache_ttl_s', None)
        if t == "assistant" and main_chain:
            umsg = d.get("message") or {}
            u = umsg.get("usage")
            model = umsg.get("model") or ""
            if isinstance(u, dict) and model != "<synthetic>":
                mid = umsg.get("id") or d.get("uuid") or str(len(usage_by_msg))
                usage_by_msg[mid] = (
                    model, tsv or "",
                    _int(u.get("input_tokens")), _int(u.get("output_tokens")),
                    _int(u.get("cache_read_input_tokens")),
                    _int(u.get("cache_creation_input_tokens")))
                cache = u.get('cache_creation') or {}
                ttl = (3600 if _int(cache.get('ephemeral_1h_input_tokens')) else
                       300 if _int(cache.get('ephemeral_5m_input_tokens')) or _int(u.get('cache_creation_input_tokens')) else
                       resume.get('cache_ttl_s') or (300 if _int(u.get('cache_read_input_tokens')) else 0))
                resume.update(model=model, at=tsv or '', compacted=False, cache_ttl_s=ttl,
                              context_tokens=sum(usage_by_msg[mid][2:]))
        if d.get("isSidechain") and not delegated:
            continue
        if t == "user":
            msg = d.get("message") or {}
            content = msg.get("content")
            # skip pure tool_result lines
            if isinstance(content, list) and all(
                isinstance(b, dict) and b.get("type") != "text" for b in content
            ):
                continue
            if d.get("isMeta") or d.get("isCompactSummary"):
                continue
            txt = clean_user_text(text_blocks(content))
            if not txt:
                continue
            turns.append({"role": "user", "text": txt, "ts": tsv or ""})
            status = "working"
            attention_required = False
        elif t == "assistant":
            msg = d.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        if str(b.get("name", "")).lower() in ("askuserquestion", "request_user_input"):
                            attention_required = True
                        op = FILE_TOOLS.get(b.get("name", ""))
                        if op:
                            add_file((b.get("input") or {}).get("file_path")
                                     or (b.get("input") or {}).get("notebook_path"), op)
                    elif isinstance(b, dict) and b.get("type") == "thinking" and (b.get("thinking") or "").strip():
                        turns.append({"role": "assistant", "channel": "analysis",
                                      "text": b["thinking"].strip(), "ts": tsv or ""})
            txt = text_blocks(content).strip()
            if txt:
                turn = {"role": "assistant", "text": txt, "ts": tsv or ""}
                if d.get("isApiErrorMessage") or is_error_answer(txt):
                    turn["error"] = True
                turns.append(turn)
            if msg.get("stop_reason") in ("end_turn", "stop_sequence"):
                status = "complete"
            elif content:
                status = "working"
    fh.close()

    sid = os.path.splitext(os.path.basename(path))[0]
    extra = {"claude_dir": slug}
    if delegated:
        root_sid = os.path.basename(os.path.dirname(os.path.dirname(path)))
        agent_id = agent_id or sid.removeprefix('agent-')
        sid = root_sid + '-agent-' + agent_id
        extra.update(agent_id=agent_id, parent_session_id=root_sid,
                     parent_agent_id=root_sid, root_session_id=root_sid,
                     parent_key='claude-' + root_sid, root_key='claude-' + root_sid,
                     is_child=True, lineage_source='claude_subagent_artifact')
    extra['spawned_children'] = []
    for result, ids in results:
        # Only Agent/Task results joined to their tool-use ID prove an edge.
        call_id = next((i for i in ids if i in calls), None)
        if not call_id:
            continue
        inp = calls[call_id]
        extra['spawned_children'].append(dict(agent_id=result['agentId'], tool_use_id=call_id,
            label=inp.get('description') or result.get('description') or '',
            agent_type=inp.get('subagent_type') or '', model=result.get('resolvedModel') or inp.get('model') or ''))
    extra.update(summarize_usage(list(usage_by_msg.values())))
    if resume:
        extra['resume'] = resume
    chat = finish("claude", sid, path, cwd, branch, title, turns, files,
                  ts_first, ts_last, extra=extra)
    chat["status"] = status
    chat["attention_required"] = needs_attention(status, chat["turns"], attention_required)
    return chat


# ---------------------------------------------------------------- Codex parser
CODEX_SKIP_PREFIX = ("<environment_context>", "<permissions", "<user_instructions>",
                     "<skills_instructions>", "<turn_aborted", "<ide_", "<AGENTS",
                     "# AGENTS", "<repo_instructions")


def codex_session_id(path):
    """Read just the session_meta line to learn which session a rollout belongs to."""
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "session_meta":
                    p = d.get("payload") or {}
                    return p.get("id") or p.get("session_id") or ""
                break
    except OSError:
        pass
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                  os.path.basename(path))
    return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]


def parse_codex_file(path):
    """Parse one rollout file -> (cwd, turns, files, ts_first, ts_last)."""
    turns, files = [], {}
    cwd = ""
    ts_first = ts_last = ""
    saw_event_user = saw_event_agent = False
    status = "unknown"
    attention_required = False
    # token_count events carry a cumulative total_token_usage plus the
    # per-call last_token_usage. Sum last_token_usage over the events whose
    # cumulative total moved (Codex repeats an unchanged event), so a resumed
    # rollout that restarts or carries over the total is never double-counted.
    model = ""
    usage_events, prev_total = [], None
    metadata, usage_ids = {}, []
    native_usage, native_ids = [], []

    def add_patch_files(args_str):
        for m in PATCH_FILE.finditer(args_str or ""):
            op = "write" if m.group(1) == "Add" else "edit"
            p = m.group(2).strip().strip('"')
            if p and not os.path.isabs(p) and cwd:
                p = os.path.normpath(os.path.join(cwd, p))
            rec = files.setdefault(p, {"edits": 0, "writes": 0})
            rec["writes" if op == "write" else "edits"] += 1

    try:
        fh = open(path, errors="ignore")
    except OSError:
        return None
    raw_items = []  # (kind, role, text, ts) fallback from response_item
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        t = d.get("type")
        pt = p.get("type", "")
        if t in ("session-owner", "tools-used", "thread_settings_applied") or pt == "thread_settings_applied":
            continue
        tsv = d.get("timestamp")
        if tsv:
            ts_first = ts_first or tsv
            ts_last = tsv
        if t == "session_meta":
            cwd = p.get("cwd", "")
            src = p.get('source') if isinstance(p.get('source'), dict) else {}
            sub = src.get('subagent') if isinstance(src.get('subagent'), dict) else {}
            spawn = sub.get('thread_spawn') if isinstance(sub.get('thread_spawn'), dict) else {}
            parent = p.get('parent_thread_id') or spawn.get('parent_thread_id')
            sid = p.get('id') or p.get('session_id')
            root = p.get('root_thread_id') or (p.get('session_id') if p.get('session_id') != sid else None)
            metadata.update(agent_id=sid, provider_account_id=p.get('creator_account_id') or '',
                            agent_path=p.get('agent_path') or spawn.get('agent_path') or '',
                            agent_label=p.get('agent_nickname') or spawn.get('agent_nickname') or '')
            if parent:
                metadata.update(parent_session_id=parent, parent_agent_id=parent,
                                parent_key='codex-' + parent, is_child=True,
                                lineage_source='codex_session_meta')
            if root:
                metadata.update(root_session_id=root, root_key='codex-' + root)
        elif t == "turn_context":
            model = p.get("model") or model
            effort = p.get('effort') or (((p.get('collaboration_mode') or {}).get('settings') or {}).get('reasoning_effort'))
            if effort:
                metadata.setdefault('resume', {})['effort'] = effort
        elif t == 'compacted':
            # The next token_count reports the shrunk size; until then it is unknown.
            metadata.setdefault('resume', {}).update(context_tokens=None, compacted=True, at=tsv or '')
        elif t == 'token_usage_record' and isinstance(p.get('usage'), dict):
            u = p['usage']
            tin, cr = _int(u.get('input_tokens')), _int(u.get('cached_input_tokens'))
            native_usage.append((p.get('model') or model, tsv or '', max(tin - cr, 0),
                                 _int(u.get('output_tokens')), cr, _int(u.get('cache_write_input_tokens'))))
            native_ids.append('response:' + str(p['response_id']) if p.get('response_id') else
                              json.dumps([tsv, p.get('thread_token_usage'), u], sort_keys=True))
        elif t == "event_msg" and pt == "token_count":
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            total, last = info.get("total_token_usage"), info.get("last_token_usage")
            if isinstance(last, dict) and total != prev_total:
                prev_total = total
                tin = _int(last.get("input_tokens"))
                cr = _int(last.get("cached_input_tokens"))
                usage_events.append((
                    model, tsv or "", max(tin - cr, 0),
                    _int(last.get("output_tokens")), cr,
                    _int(last.get("cache_write_input_tokens"))))
                usage_ids.append(json.dumps([tsv, total, last], sort_keys=True))
                metadata.setdefault('resume', {}).update(
                    model=model, at=tsv or '', compacted=False,
                    context_tokens=tin + _int(last.get('output_tokens')),
                    context_window=_int(info.get('model_context_window')) or None)
        elif t == 'event_msg' and (pt == 'error' or pt == 'task_complete' and p.get('error')):
            failure = auth_failure.from_event('codex', d, at=tsv)
            turns.append({'role': 'assistant', 'text': failure['raw'], 'ts': tsv or '', 'error': True, 'failure': failure})
            status = 'failed'
        elif t == "event_msg" and pt == "task_started":
            status = "working"
        elif t == "event_msg" and pt in ("task_complete", "turn_aborted"):
            status = "complete"
        elif t == "event_msg" and pt == "user_message":
            msg = (p.get("message") or "").strip()
            if msg and not msg.startswith(CODEX_SKIP_PREFIX):
                saw_event_user = True
                turns.append({"role": "user", "text": msg, "ts": tsv or ""})
                status = "working"
                attention_required = False
        elif t == "event_msg" and pt == "agent_message":
            msg = (p.get("message") or "").strip()
            if msg:
                saw_event_agent = True
                turn = {"role": "assistant", "text": msg, "ts": tsv or ""}
                if is_error_answer(msg):
                    turn["error"] = True
                turns.append(turn)
        elif t == "response_item" and pt == "message":
            role = p.get("role")
            txt = ""
            for b in p.get("content") or []:
                if isinstance(b, dict) and b.get("type") in ("input_text", "output_text"):
                    txt += b.get("text", "")
            txt = txt.strip()
            if role in ("user", "assistant") and txt and not txt.startswith(CODEX_SKIP_PREFIX):
                raw_items.append((role, txt, tsv or "", p.get("channel") or p.get("phase")))
        elif t == "response_item" and pt in ("function_call", "custom_tool_call"):
            name = str(p.get("name") or "").lower()
            if name in ("request_user_input", "askuserquestion"):
                attention_required = True
            add_patch_files(str(p.get("arguments") or p.get("input") or ""))
    fh.close()

    # Preserve recorded output channels even when agent_message events are used.
    channel_occurrences = {}
    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        candidates = [item for item in raw_items if item[0] == "assistant" and item[1] == turn["text"] and item[3]]
        if candidates:
            position = channel_occurrences.get(turn["text"], 0)
            if position < len(candidates):
                turn["channel"] = candidates[position][3]
            channel_occurrences[turn["text"]] = position + 1

    # Older rollouts may lack event_msg user/agent lines — fall back to response items
    if not saw_event_user or not saw_event_agent:
        # A live session can have a user event before its first agent event.
        # Preserve available event roles and fall back only for the missing role.
        for r, tx, ts, channel in raw_items:
            if (r == 'user' and saw_event_user) or (r == 'assistant' and saw_event_agent):
                continue
            turn = {"role": r, "text": tx, "ts": ts}
            if channel:
                turn["channel"] = channel
            if r == "assistant" and is_error_answer(tx):
                turn["error"] = True
            turns.append(turn)
        turns.sort(key=lambda turn: turn.get('ts', ''))
    if status == "unknown":
        status = "complete" if turns and turns[-1]["role"] == "assistant" else "working"

    # New native records have stable response IDs; token_count mirrors the
    # same calls and must not be added again. Older rollouts use the fallback.
    return (cwd, turns, files, ts_first, ts_last, status, attention_required,
            [e + (metadata.get('provider_account_id') or '',) for e in (native_usage or usage_events)],
            metadata, native_ids or usage_ids)


def parse_codex_group(sid, paths, thread_names):
    """Merge all rollout files of one codex session (resume writes a new file
    per continuation carrying only the new turns)."""
    parts = []
    seen = set()
    for path in paths:
        parsed = parse_codex_file(path)
        if parsed:
            # Profile mirrors can contain the same native rollout. Keep source
            # locations but count identical parsed content only once.
            fingerprint = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            parts.append((parsed[3], path) + parsed)  # sort key = ts_first
    if not parts:
        return None
    parts.sort(key=lambda x: x[0])
    cwd, turns, files = "", [], {}
    ts_first = ts_last = ""
    status = "complete"
    attention_required = False
    usage_events = []
    metadata, seen_usage = {}, set()
    for (_, path, p_cwd, p_turns, p_files, p_tsf, p_tsl, p_status, p_attention,
         p_usage, p_meta, p_usage_ids) in parts:
        resume = {**metadata.get('resume', {}), **p_meta.get('resume', {})}
        metadata.update({k: v for k, v in p_meta.items() if v not in ('', None)})
        if resume:
            metadata['resume'] = resume
        for event, event_id in zip(p_usage, p_usage_ids):
            if event_id not in seen_usage:
                seen_usage.add(event_id)
                usage_events.append(event)
        cwd = cwd or p_cwd
        for t in p_turns:
            if turns and turns[-1]["role"] == t["role"] and turns[-1]["text"] == t["text"]:
                continue  # continuation files sometimes replay the last exchange
            turns.append(t)
        for fp, c in p_files.items():
            rec = files.setdefault(fp, {"edits": 0, "writes": 0})
            rec["edits"] += c["edits"]
            rec["writes"] += c["writes"]
        ts_first = ts_first or p_tsf
        ts_last = p_tsl or ts_last
        status = p_status
        attention_required = p_attention
    title = thread_names.get(sid, "")
    newest = parts[-1][1]
    chat = finish("codex", sid, newest, cwd, "", title, turns, files,
                  ts_first, ts_last, extra={**summarize_usage(usage_events), **metadata})
    chat["files_all"] = sorted(set(paths))
    chat["status"] = status
    chat["attention_required"] = needs_attention(status, chat["turns"], attention_required)
    return chat


# ---------------------------------------------------------------- shared
def merge_consecutive(turns):
    out = []
    for t in turns:
        if out and out[-1]["role"] == t["role"] and out[-1].get("channel") == t.get("channel"):
            out[-1]["text"] = (out[-1]["text"] + "\n\n" + t["text"]).strip()
            # a merged turn is only an error turn if every part was one, so a
            # real answer followed by a 529 retry still counts as an answer
            if t.get("ts") and not t.get("error"):
                out[-1]["ts"] = t["ts"]
            if not t.get("error"):
                out[-1].pop("error", None)
        else:
            out.append(dict(t))
    return out


def finish(source, sid, path, cwd, branch, title, turns, files,
           ts_first, ts_last, extra=None):
    turns = merge_consecutive(turns)
    for turn in turns:
        if turn.get('error'):
            turn['failure'] = turn.get('failure') or auth_failure.classify(source, turn.get('text', ''), (extra or {}).get('account_id'), turn.get('ts'))
    n_user = sum(1 for t in turns if t["role"] == "user")
    n_asst = sum(1 for t in turns if t["role"] == "assistant")
    n_answer = sum(1 for t in turns
                   if t["role"] == "assistant" and not t.get("error")
                   and t.get("channel") != "analysis")
    key = f"{source}-{sid}"
    chat = {
        "key": key, "source": source, "id": sid, "file": path,
        "cwd": cwd, "branch": branch, "title_seed": title,
        "ts_first": ts_first, "ts_last": ts_last,
        "n_user": n_user, "n_asst": n_asst, "n_answer": n_answer,
        "last_role": turns[-1]["role"] if turns else "",
        "last_assistant_ts": next((t.get("ts", "") for t in reversed(turns) if t["role"] == "assistant" and not t.get("error") and t.get("channel") != "analysis" and t.get("ts")), ""),
        "files_changed": [
            {"path": p, **c} for p, c in sorted(files.items())
        ],
        "turns": turns,
    }
    last_answer = next((turn for turn in reversed(turns) if turn.get('role') == 'assistant' and turn.get('channel') != 'analysis'), {})
    if last_answer.get('failure'):
        chat['failure'] = last_answer['failure']
        chat['status'] = 'failed'
    chat["files_all"] = [path]
    if extra:
        chat.update(extra)
    if chat.get('failure'):
        chat['status'] = 'failed'
    return chat


def src_sig(paths):
    sig = []
    for p in sorted(paths):
        try:
            st = os.stat(p)
            sig.append([p, st.st_mtime, st.st_size])
        except OSError:
            continue
    return sig


def content_sig(chat):
    """Identifies the conversation as it stands, so enrich.py can tell that an
    already-labelled chat has since been continued and needs a fresh title."""
    return "%d:%d:%s" % (chat["n_user"], chat["n_answer"], chat["ts_last"])


def meta_of(chat):
    first_user = next((t["text"] for t in chat["turns"] if t["role"] == "user"), "")
    return {
        "key": chat["key"], "source": chat["source"], "id": chat["id"],
        "file": chat["file"], "files_all": chat["files_all"],
        "cwd": chat["cwd"], "title_seed": chat["title_seed"],
        "ts_first": chat["ts_first"], "ts_last": chat["ts_last"],
        **({'failure': chat['failure']} if chat.get('failure') else {}),
        "n_user": chat["n_user"], "n_asst": chat["n_asst"],
        "n_answer": chat["n_answer"],
        "content_sig": content_sig(chat),
        "meta_v": META_V,
        "last_role": chat.get("last_role", ""),
        "last_assistant_ts": chat.get("last_assistant_ts", ""),
        "status": chat.get("status", "complete"),
        "attention_required": bool(chat.get("attention_required", False)),
        "n_files": len(chat["files_changed"]),
        "first_prompt": first_user[:300],
        "src_sig": src_sig(chat["files_all"]),
        "node": chat.get("node", "mac"),
        "model_main": chat.get("model_main", ""),
        "models": chat.get("models", {}),
        "usage": chat.get("usage", empty_usage()),
        "usage_days": chat.get("usage_days", {}),
        "usage_buckets": chat.get("usage_buckets", []),
        **({'resume': chat['resume']} if chat.get('resume') else {}),
        **{k: chat[k] for k in LINEAGE_FIELDS if k in chat},
        **{k: chat[k] for k in ('source_locations', 'account_attribution') if k in chat},
    }


def source_roots(cfg):
    """[(node, claude_projects_root, codex_sessions_root, codex_index)]: the
    Mac's own roots, then every remote source synced under data/remote/<name>."""
    roots = [("mac", cfg["claude_projects_root"], cfg["codex_sessions_root"],
              cfg["codex_index"])]
    for profile in configured_profiles():
        directory = os.path.expanduser(profile.get('config_dir') or '')
        if not os.path.isabs(directory):
            continue
        if profile.get('tool') == 'claude':
            candidate = ('mac', os.path.join(directory, 'projects'), '', '')
            if not any(r[0] == 'mac' and os.path.realpath(r[1]) == os.path.realpath(candidate[1]) for r in roots if r[1]):
                roots.append(candidate)
        elif profile.get('tool') == 'codex':
            candidate = ('mac', '', os.path.join(directory, 'sessions'), os.path.join(directory, 'session_index.jsonl'))
            if not any(r[0] == 'mac' and os.path.realpath(r[2]) == os.path.realpath(candidate[2]) for r in roots if r[2]):
                roots.append(candidate)
    for src in cfg.get("remote_sources") or []:
        name = str((src or {}).get("name") or "").strip()
        if not name or name == "mac" or "/" in name:
            continue
        base = os.path.join(DATA, "remote", name)
        roots.append((name, os.path.join(base, "claude-projects"),
                       os.path.join(base, "codex-sessions"),
                       os.path.join(base, "codex-session_index.jsonl")))
    return roots


def claude_desktop_metadata():
    """Join display titles and explicit Desktop delegations to native CLI keys."""
    records = []
    for path in CLAUDE_DESKTOP_SESSIONS.glob("*/*/local_*.json"):
        if path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                record = json.load(handle)
                modified = os.fstat(handle.fileno()).st_mtime_ns
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        sid = record.get("cliSessionId")
        if not isinstance(sid, str) or not NATIVE_SESSION_ID.fullmatch(sid):
            continue
        records.append((modified, path, "claude-" + sid.lower(), record))

    sessions, current_by_key = {}, {}
    for _, path, key, record in sorted(records):
        account = path.parent.parent
        desktop_id = record.get("sessionId")
        if desktop_id == path.stem:
            sessions[(account, desktop_id)] = key
        current_by_key[key] = (account, record)

    titles, parents = {}, {}
    for key, (account, record) in current_by_key.items():
        title = record.get("title")
        if isinstance(title, str) and title.strip():
            titles[key] = title.strip()
        spawn = record.get("spawnedFrom")
        if (not isinstance(spawn, dict) or spawn.get("sideSession") is not True
                or not spawn.get("taskId") or spawn.get("userFork") or spawn.get("btw")):
            continue
        parent_id = spawn.get("sessionId")
        parent = sessions.get((account, parent_id)) if isinstance(parent_id, str) else None
        if parent and parent != key:
            parents[key] = parent
    return titles, parents


def is_local_claude(meta):
    return meta.get("source") == "claude" and "mac" in meta.get("node", "mac").split("+")


def reconcile_desktop_lineage(index, desktop_parents):
    """Rebuild only Desktop's edges, including recoverable missing parents."""
    for meta in index.values():
        if meta.get("lineage_source") == DESKTOP_LINEAGE_SOURCE:
            for field in DESKTOP_LINEAGE_FIELDS:
                meta.pop(field, None)
    native_parents = {}
    for key, meta in index.items():
        parent = meta.get("parent_key")
        root = meta.get("root_key")
        if parent or (root and root != key):
            native_parents[key] = parent or root
    candidate_parents = {}
    for key, parent in desktop_parents.items():
        meta = index.get(key)
        if (meta and is_local_claude(meta) and key not in native_parents
                and not any(meta.get(field) for field in ("is_child", "parent_session_id", "parent_agent_id"))):
            candidate_parents[key] = parent
    parents = {**native_parents, **candidate_parents}
    for key, parent in candidate_parents.items():
        seen = {key}
        cursor = parent
        while cursor and cursor in index and cursor not in seen:
            seen.add(cursor)
            cursor = parents.get(cursor)
        if cursor in seen:  # Reject a corrupt cycle; missing parents have tree placeholders.
            continue
        parent_sid = parent.removeprefix("claude-")
        index[key].update(parent_key=parent, parent_session_id=parent_sid, parent_agent_id=parent_sid,
                          is_child=True, lineage_source=DESKTOP_LINEAGE_SOURCE)


def configured_profiles():
    """Read profile paths only; credentials and quota are never inspected here."""
    try:
        with open(os.path.join(DATA, 'accounts.json')) as handle:
            value = json.load(handle)
        return [p for p in value.get('accounts', []) if isinstance(p, dict)]
    except (OSError, ValueError, AttributeError):
        return []


def source_locations(by_node, cfg):
    profiles = configured_profiles()
    result = []
    for node, paths in by_node.items():
        for path in sorted(set(paths)):
            canonical = os.path.realpath(path)
            matches = []
            for profile in profiles if node == 'mac' else []:
                directory = profile.get('config_dir') or ''
                if not directory or not profile.get('id'):
                    continue
                suffix = 'sessions' if profile.get('tool') == 'codex' else 'projects' if profile.get('tool') == 'claude' else None
                if suffix is None:
                    continue
                # A profile may alias only its transcript directory, while its
                # credentials stay separate. Record every hint, never ownership.
                root = os.path.realpath(os.path.join(os.path.expanduser(directory), suffix))
                if canonical.startswith(root.rstrip('/') + '/'):
                    matches.append(profile['id'])
            result.append({'host_id':node, 'path':path, 'profile_ids':sorted(set(matches)),
                           'provenance':'configured_profile' if matches else 'configured_root' if node == 'mac' else 'remote_mirror'})
    return result


def parse_unit(key, source, by_node, thread_names):
    """Parse one chat. When the same uuid exists on several nodes, each node's
    copy is parsed and the one with the later ts_last wins; node lists every
    node the chat was found on, joined by '+' in root order (mac first)."""
    best = None
    for node, paths in by_node.items():
        if source == "claude":
            candidates = [chat for path in sorted(set(paths)) if (chat := parse_claude(path))]
            chat = max(candidates, key=lambda c:c['ts_last']) if candidates else None
        else:
            chat = parse_codex_group(key[len("codex-"):], paths, thread_names)
        if chat and (best is None or chat["ts_last"] > best["ts_last"]):
            best = chat
            best['usage_host'] = node
    if best is not None:
        best["node"] = "+".join(by_node)
        best["files_all"] = sorted(set(best["files_all"])
                                   | {p for ps in by_node.values() for p in ps})
    return best


def resolve_lineage(index):
    """Resolve only recorded edges. Never group unrelated sessions by cwd/time."""
    for key, meta in index.items():
        meta.setdefault('agent_id', meta.get('id') or key)
        meta.setdefault('root_key', key)
    # Claude subagent artifacts share a root directory; Task result joins tell
    # us when an intermediate child, rather than the root, spawned an agent.
    for parent_key, parent in index.items():
        for child in parent.get('spawned_children') or []:
            root = parent.get('root_key') or parent_key
            child_key = root + '-agent-' + str(child.get('agent_id') or '')
            target = index.get(child_key)
            if target and child_key != parent_key:
                target.update(parent_key=parent_key, parent_agent_id=parent.get('agent_id'),
                              agent_label=child.get('label') or target.get('agent_label') or '',
                              lineage_source='claude_tool_result', is_child=True)
    for key, meta in index.items():
        parent_key = meta.get('parent_key')
        if not parent_key:
            continue
        meta['is_child'] = True
        seen, current = {key}, parent_key
        while current not in seen:
            seen.add(current)
            parent = index.get(current)
            if parent is None:
                # Native metadata may know a root beyond a missing parent.
                if meta.get('root_key') == key:
                    meta['root_key'] = current
                break
            next_key = parent.get('parent_key')
            if not next_key:
                meta['root_key'] = current
                break
            current = next_key
    return index


# ---------------------------------------------------------------- main
def official_transcript_aliases():
    """Ignore only aliases whose private ownership record still matches the file.

    Current Claude SDKs reject final-component symlinks. Official's lookup aliases
    therefore share the source inode. A stale alias after atomic source replacement
    is still excluded, but an unrelated replacement or the last surviving copy is
    retained. Reading this manifest never changes a transcript or its ownership.
    """
    shared = os.environ.get('LIBRARY_OFFICIAL_SHARED') or os.path.join(HOME, '.library-code-official-shared')
    try:
        with open(os.path.join(shared, 'session-aliases.json')) as handle:
            manifest = json.load(handle)
        if not isinstance(manifest, dict) or manifest.get('version') != 1:
            return set()
        aliases = manifest.get('aliases')
        if not isinstance(aliases, dict):
            return set()
    except (OSError, ValueError):
        return set()
    owned = set()
    for alias, record in aliases.items():
        if not isinstance(record, dict) or record.get('kind') != 'hardlink':
            continue
        source = record.get('source')
        if not isinstance(source, str) or not os.path.isabs(alias) or not os.path.isabs(source):
            continue
        # Inspect the final alias component itself before resolving parents; a
        # replacement symlink must never hide its unrelated target.
        alias, source = os.path.abspath(alias), os.path.realpath(source)
        if os.path.realpath(alias) == source:
            continue
        try:
            info = os.lstat(alias)
            original = os.lstat(source)
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode) and stat.S_ISREG(original.st_mode)
                and (info.st_dev, info.st_ino) == (record.get('dev'), record.get('ino'))):
            owned.add(os.path.realpath(alias))
    return owned


def main():
    full = "--full" in sys.argv
    cfg = load_config()
    os.makedirs(CHATS, exist_ok=True)
    index = {}
    if os.path.exists(INDEX) and not full:
        try:
            index = json.load(open(INDEX))
        except Exception:
            index = {}

    roots = source_roots(cfg)
    thread_names = {}
    for _node, _croot, _xroot, xindex in roots:
        if not os.path.exists(xindex):
            continue
        for line in open(xindex, errors="ignore"):
            try:
                d = json.loads(line)
                if d.get("id") and d.get("thread_name"):
                    thread_names.setdefault(d["id"], d["thread_name"])
            except Exception:
                continue

    # work units: claude = one file per chat; codex = all rollouts of a session
    # id. Each unit maps node -> [paths] so a uuid seen on two nodes is merged.
    aliases = official_transcript_aliases()
    units = {}  # key -> (source, {node: [paths]})
    for node, croot, xroot, _xindex in roots:
        for pd in sorted(glob.glob(os.path.join(croot, "*")) if croot else []):
            if os.path.isdir(pd):
                claude_files = glob.glob(os.path.join(pd, '*.jsonl'))
                claude_files += glob.glob(os.path.join(pd, '*', 'subagents', '*.jsonl'))
                for f in sorted(claude_files):
                    if os.path.islink(f) or (node in ('mac', 'local') and os.path.realpath(f) in aliases):
                        continue
                    sid = os.path.splitext(os.path.basename(f))[0]
                    if os.path.basename(os.path.dirname(f)) == 'subagents':
                        sid = os.path.basename(os.path.dirname(os.path.dirname(f))) + '-agent-' + sid.removeprefix('agent-')
                    key = "claude-" + sid
                    units.setdefault(key, ("claude", {}))[1] \
                        .setdefault(node, []).append(f)
        for f in sorted(glob.glob(os.path.join(xroot, "**", "*.jsonl"),
                                  recursive=True) if xroot else []):
            if os.path.islink(f):
                continue
            key = "codex-" + codex_session_id(f)
            units.setdefault(key, ("codex", {}))[1].setdefault(node, []).append(f)

    min_answers = int(cfg.get("min_answer_msgs", 1))
    seen_keys, n_new, n_skip, n_drop, n_noanswer = set(), 0, 0, 0, 0
    for key, (source, by_node) in units.items():
        paths = [p for ps in by_node.values() for p in ps]
        prev = index.get(key)
        if (prev and prev.get("src_sig") == src_sig(paths)
                and prev.get("meta_v") == META_V):
            # unchanged since the last run, but still honour the current filter
            if prev.get("n_answer", 1) < min_answers and not prev.get('is_child') and not prev.get('failure'):
                n_noanswer += 1
                continue
            seen_keys.add(key)
            n_skip += 1
            continue
        chat = parse_unit(key, source, by_node, thread_names)
        if not chat or (not chat.get('is_child') and chat["n_user"] < cfg["min_user_msgs"]):
            n_drop += 1
            continue
        if chat["n_answer"] < min_answers and not chat.get('is_child') and not chat.get('failure'):
            # asked but never answered: interrupted mid-turn, or every reply was
            # an API error. Nothing to read, so keep it out of the dashboard.
            n_noanswer += 1
            continue
        cpath = os.path.join(CHATS, chat["key"] + ".json")
        # Runtime provenance belongs to Library, not the native transcript parser.
        # Read the cleaned metadata too so --full preserves execution ownership.
        previous = dict(prev or {})
        try:
            previous.update(json.load(open(cpath)))
        except (OSError, ValueError):
            pass
        preserve_execution_metadata(chat, previous)
        mark_quota_handoff_seed(chat)
        chat['source_locations'] = source_locations(by_node, cfg)
        chat['account_attribution'] = 'verified' if chat.get('account_id') else 'unknown'
        atomic_json_dump(chat, cpath)
        record = meta_of(chat)
        preserve_execution_metadata(record, chat)
        index[chat["key"]] = record
        seen_keys.add(chat["key"])
        n_new += 1

    # drop index entries whose source file disappeared
    gone = [] if '--keep-missing' in sys.argv else [k for k in index if k not in seen_keys]
    for k in gone:
        index.pop(k)
        cpath = os.path.join(CHATS, k + ".json")
        if os.path.exists(cpath):
            os.remove(cpath)

    desktop_titles, desktop_parents = claude_desktop_metadata()
    reconcile_desktop_lineage(index, desktop_parents)
    resolve_lineage(index)
    # Keep drill-in transcripts consistent with the index after resolving
    # explicit intermediate parents (without rewriting turn bodies).
    for key, meta in index.items():
        desktop_title = desktop_titles.get(key.lower()) if is_local_claude(meta) else None
        if desktop_title:
            meta["desktop_title"] = desktop_title
        else:
            meta.pop("desktop_title", None)
        cpath = os.path.join(CHATS, key + '.json')
        try:
            with open(cpath) as fh:
                chat = json.load(fh)
            fields = {k: meta[k] for k in LINEAGE_FIELDS if k in meta}
            obsolete = ()
            if chat.get("lineage_source") == DESKTOP_LINEAGE_SOURCE:
                obsolete = tuple(k for k in DESKTOP_LINEAGE_FIELDS if k in chat and k not in fields)
            if obsolete or chat.get("desktop_title") != desktop_title or any(chat.get(k) != v for k, v in fields.items()):
                for field in obsolete:
                    chat.pop(field)
                if desktop_title:
                    chat["desktop_title"] = desktop_title
                else:
                    chat.pop("desktop_title", None)
                chat.update(fields)
                atomic_json_dump(chat, cpath)
        except (OSError, ValueError):
            pass
    atomic_json_dump(index, INDEX, indent=1)

    pruned = 0
    if "--prune" in sys.argv:
        for name in os.listdir(CHATS):
            if name.endswith(".json") and name[:-5] not in index:
                os.remove(os.path.join(CHATS, name))
                pruned += 1

    print(f"indexed={n_new} unchanged={n_skip} dropped_trivial={n_drop} "
          f"dropped_no_answer={n_noanswer} removed_stale={len(gone)} "
          f"pruned_copies={pruned} total={len(index)}")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, 'archive-write.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
