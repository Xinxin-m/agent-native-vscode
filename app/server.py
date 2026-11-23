#!/usr/bin/env python3
"""
server.py — Local API + static frontend for the chat-history dashboard.

Stdlib only. Run:  python3 server.py  (port from data/config.json, default 8744)

Endpoints
  GET  /                      frontend
  GET  /api/state             config + projects + chat metas + views + dirs
  GET  /api/chat?key=K        full cleaned chat (turns + files)
  POST /api/view              {key} increment view counter
  POST /api/projects          {action: create|rename|delete|reorder, ...}
  POST /api/assign            {key, project}  (project=null -> ungrouped)
  POST /api/delete_chat       {key}  permanent: source transcript(s) + cleaned copy
  POST /api/config            {excluded_cwds: [...]}
  POST /api/rescan            refresh local and remote history, enrich, and group
  POST /api/sync              parse-only refresh; include_remote requests a manual scan
  GET  /api/history_refresh  progress for a manual history refresh
  POST /api/send              {key, message, model?, effort?} resume via claude/codex CLI
  POST /api/open              {path} open file in editor
"""
import gzip, hashlib, json, mimetypes, os, re, shutil, stat, subprocess, sys, threading, time, urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clis  # noqa: E402  (the CLIs are invisible to a service's minimal PATH)
from automation import automation_kind, effective_projects
import json_cache
import notices

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WEB = os.path.join(HERE, "web")
CHATS = os.path.join(DATA, "chats")

FILES = {
    "index": os.path.join(DATA, "index.json"),
    "enrich": os.path.join(DATA, "enrich.json"),
    "projects": os.path.join(DATA, "projects.json"),
    "flags": os.path.join(DATA, "chat_flags.json"),
    "views": os.path.join(DATA, "views.json"),
    "config": os.path.join(DATA, "config.json"),
}
LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
_HISTORY_REFRESH_JOBS = OrderedDict()
STATE_LOCK = threading.Lock()  # guards _STATE, the cached /api/state bytes
_STATE = {}
_STATE_HISTORY = OrderedDict()  # Current and four previous chat-row fingerprints.
_CHAT_CACHE = OrderedDict()
_CHAT_LOCK = threading.Lock()
_RESPONSE_LOCK = threading.Lock()
_STATE_ERROR = {}  # last failed build: {"message", "at", "logged"}; cleared by a good build
STATE_RETRY_S = 5


def jload(name, default):
    try:
        return json.load(open(FILES[name]))
    except Exception:
        return default


def jsave(name, obj):
    tmp = FILES[name] + ".tmp"
    json.dump(obj, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, FILES[name])


def default_projects():
    return {"projects": [], "assignments": {}}


# ------------------------------------------------- models the composer offers
# Claude aliases resolve to the current model of that tier, so they keep working
# across releases; effort levels are the CLI's own --effort values.
# Exact versions come from the extra models each Claude CLI profile caches from
# the server (`additionalModelOptionsCache` in .claude.json) and from an optional
# model tiers file (config `models_file`); newest first within each family.
CLAUDE_MODEL_CACHES = ("~/.claude.json", "~/.claude-profiles/*/.claude.json")
CLAUDE_ID = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d+))?$")
_MODEL_SOURCES = {}  # path -> ((mtime_ns, size), extracted value)


def _model_source(path, extract):
    """extract(json) for path, re-read only when the file changes."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _MODEL_SOURCES.get(path)
    if hit and hit[0] == (st.st_mtime_ns, st.st_size):
        return hit[1]
    try:
        with open(path) as fh:
            value = extract(json.load(fh))
    except Exception:
        value = None
    _MODEL_SOURCES[path] = ((st.st_mtime_ns, st.st_size), value)
    return value


def claude_cached_models():
    """Model values the Claude CLI was offered, from every profile (only that key is read)."""
    import glob
    out = []
    for pattern in CLAUDE_MODEL_CACHES:
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            for entry in _model_source(path, lambda d: d.get("additionalModelOptionsCache")) or []:
                value = entry.get("value") if isinstance(entry, dict) else None
                if isinstance(value, str) and value not in out:
                    out.append(value)
    return out


def claude_tier_models():
    path = _model_source(FILES["config"], lambda d: d.get("models_file"))
    tiers = _model_source(os.path.expanduser(path), lambda d: d.get("tiers")) if isinstance(path, str) and path else None
    return [str(t.get("claude")) for t in (tiers or {}).values() if isinstance(t, dict) and t.get("claude")]


def claude_models():
    families = {}  # family -> {version tuple: [id, has 1M variant]}
    for value in claude_cached_models() + claude_tier_models():
        base = value[:-4] if value.lower().endswith("[1m]") else value
        m = CLAUDE_ID.match(base)
        if not m:
            continue
        entry = families.setdefault(m[1], {}).setdefault((int(m[2]), int(m[3] or 0)), [base, False])
        entry[1] = entry[1] or base != value
    out = []
    for family, versions in families.items():
        for (major, minor), (base, long) in sorted(versions.items(), reverse=True):
            name = family.title() + " " + str(major) + ("." + str(minor) if CLAUDE_ID.match(base)[3] else "")
            out.append({"id": base, "name": name})
            if long:
                out.append({"id": base + "[1m]", "name": name + " (1M context)"})
    return out + CLAUDE_MODELS


CLAUDE_MODELS = [
    {"id": "", "name": "Session default"},
    {"id": "opus", "name": "Opus (CLI alias)"},
    {"id": "opus[1m]", "name": "Opus (1M context)"},
    {"id": "sonnet", "name": "Sonnet (CLI alias)"},
    {"id": "sonnet[1m]", "name": "Sonnet (1M context)"},
    {"id": "haiku", "name": "Haiku (CLI alias)"},
    {"id": "fable", "name": "Fable (CLI alias)"},
]
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_MODELS_CACHE = os.path.expanduser("~/.codex/models_cache.json")
# fallback if the cache is missing; codex rejects an unknown slug outright
CODEX_FALLBACK = [{"id": "", "name": "Session default"}]
CODEX_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def codex_models():
    """Read the models codex itself advertises, with each one's effort levels."""
    out = [{"id": "", "name": "Session default"}]
    try:
        cache = json.load(open(CODEX_MODELS_CACHE))
    except Exception:
        return CODEX_FALLBACK
    for m in cache.get("models") or []:
        if m.get("visibility") == "hide" or not m.get("slug"):
            continue
        out.append({
            "id": m["slug"],
            "name": m.get("display_name") or m["slug"],
            "efforts": [e.get("effort") for e in
                        (m.get("supported_reasoning_levels") or []) if e.get("effort")],
            "default_effort": m.get("default_reasoning_level") or "",
        })
    return out


def compose_options():
    return {
        "claude": {"models": claude_models(), "efforts": CLAUDE_EFFORTS},
        "codex": {"models": codex_models(), "efforts": CODEX_EFFORTS},
    }


def default_flags():
    return {"archived": [], "starred": []}


def config_exclusions(cfg):
    values = cfg.get("excluded_cwds", []) if isinstance(cfg, dict) else []
    return [value for value in values if isinstance(value, str) and value.strip()] if isinstance(values, list) else []


def display_title(key, meta, generated=None, manual=None):
    return (manual or meta.get("desktop_title") or generated or meta.get("title_seed")
            or (meta.get("first_prompt") or "")[:60] or key)


def build_state():
    """The /api/state payload without its revision."""
    with LOCK:
        index = jload("index", {})
        enrich = jload("enrich", {})
        flags = jload("flags", default_flags())
        archived = set(flags.get("archived", []))
        starred = set(flags.get("starred", []))
        cfg = jload("config", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        excluded = config_exclusions(cfg)
        cfg = dict(cfg, excluded_cwds=excluded)
        chats = []
        dirs = {}
        for k, m in index.items():
            cwd = m.get("cwd") or "?"
            dirs[cwd] = dirs.get(cwd, 0) + 1
            if any(cwd == e or cwd.startswith(e.rstrip("/") + "/")
                   for e in excluded):
                continue
            e = enrich.get(k) or {}
            chats.append({**{x: m[x] for x in
                             ("key", "source", "id", "file", "cwd", "title_seed",
                              "ts_first", "ts_last", "n_user", "n_asst", "n_files",
                              "first_prompt")},
                          "files_all": m.get("files_all", [m["file"]]),
                          # v2 fields written by the indexer (see docs/V2-CONTRACTS.md)
                          "node": m.get("node", "mac"),
                          "model_main": m.get("model_main", ""),
                          "models": m.get("models", {}),
                          **{field: m[field] for field in ('account_id', 'execution_host', 'host_id', 'conversation_key', 'source_key', 'last_run_id', 'parent_key', 'root_key', 'parent_agent_id', 'parent_session_id', 'agent_id', 'agent_label', 'agent_path', 'is_child', 'lineage_source', 'spawned_children', 'source_locations', 'account_attribution') if field in m},
                          "usage": m.get("usage", {}),
                          "title": display_title(k, m, e.get("title"), (flags.get("titles") or {}).get(k)),
                          "description": e.get("description", ""),
                          "keywords": e.get("keywords", []),
                          "topic_hint": e.get("topic_hint", ""),
                          "is_ops": e.get("is_ops", False),
                          "automation": bool(automation_kind(m)),
                          "automation_kind": automation_kind(m),
                          "enriched": "title" in e,
                          "status": m.get("status", "complete"),
                          "last_role": m.get("last_role", ""),
                          "last_assistant_ts": m.get("last_assistant_ts", ""),
                          "transcript_revision": json.dumps(m.get("src_sig", [])),
                          "attention_required": bool(m.get("attention_required", False)),
                          "archived": k in archived or bool(automation_kind(m)),
                          "starred": k in starred})
        project_state = effective_projects(index, jload("projects", default_projects()))
        from session_lineage import effective_assignments
        project_state["assignments"] = effective_assignments(index, project_state["assignments"])
        from projects import display_state
        project_state = display_state(index, project_state, flags, cfg)
        return {
            "chats": chats,
            "projects": project_state,
            "chat_flags": flags,
            "views": jload("views", {}),
            "config": {**cfg, "data_dir": DATA},
            "dirs": [{"cwd": c, "n": n,
                      "excluded": any(c == e or c.startswith(e.rstrip("/") + "/")
                                      for e in excluded)}
                     for c, n in sorted(dirs.items(), key=lambda x: -x[1])],
            # Pending means not attempted yet. Failed label attempts are
            # tracked separately so the UI never implies a worker is
            # still running after a timeout.
            "enrich_pending": sum(1 for k in index if k not in enrich),
            "enrich_failed": sum(1 for k in index
                                 if "error" in (enrich.get(k) or {})),
            "compose_options": compose_options(),
            # what THIS process can execute, which is what matters for
            # resuming a chat and for a Rescan it launches
            "cli_paths": {n: clis.cli(n) for n in ("claude", "codex")},
        }


def _state_signature():
    sig = []
    for path in [FILES[n] for n in ("index", "enrich", "projects", "flags", "views", "config")] + [CODEX_MODELS_CACHE]:
        try:
            st = os.stat(path)
            sig.append((path, st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            sig.append((path, None))
    # The Claude list, not its files: .claude.json changes on every session.
    sig.append(tuple(m["id"] for m in claude_models()))
    return tuple(sig)


def _serialized_json(payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    return {"etag": hashlib.sha256(body).hexdigest()[:24], "body": body}


def _chat_signature(path):
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        raise FileNotFoundError(path)
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def chat_body(key):
    """Reuse serialized chat bytes; title reads never take the mutation LOCK."""
    path = os.path.join(CHATS, os.path.basename(key) + ".json")
    signature = _chat_signature(path)
    flags = json_cache.latest(FILES["flags"], default_flags())
    title = (flags.get("titles") or {}).get(key) if isinstance(flags, dict) else None
    version = (signature, title)
    with _CHAT_LOCK:
        cached = _CHAT_CACHE.get(path)
        if cached and cached["version"] == version:
            _CHAT_CACHE.move_to_end(path)
            return cached
        with open(path) as handle:
            chat = json.load(handle)
        if title or chat.get("desktop_title"):
            chat["title"] = title or chat["desktop_title"]
        response = {**_serialized_json(chat), "version": version}
        _CHAT_CACHE[path] = response
        _CHAT_CACHE.move_to_end(path)
        # Bound both transcript count and raw byte storage. Lazy gzip adds at
        # most roughly another copy without retaining the parsed transcripts.
        while len(_CHAT_CACHE) > 64 or sum(len(row["body"]) for row in _CHAT_CACHE.values()) > 32 * 1024 * 1024:
            _CHAT_CACHE.popitem(last=False)
        return response


def _remember_state_rows(state):
    rows = {row["key"]: hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).digest()
            for row in state["payload"].get("chats", [])}
    state["rows"] = rows
    _STATE_HISTORY[state["etag"]] = rows
    _STATE_HISTORY.move_to_end(state["etag"])
    while len(_STATE_HISTORY) > 5:
        _STATE_HISTORY.popitem(last=False)


def _state_response(state, since):
    previous = _STATE_HISTORY.get(since) if since else None
    if previous is None:
        return state  # Legacy/full shape, also the recovery path for old tokens.
    responses = state.setdefault("deltas", {})
    if since not in responses:
        payload = state["payload"]
        delta = {"delta": True, "since": since, "revision": state["etag"],
                 "changed": [row for row in payload.get("chats", []) if previous.get(row["key"]) != state["rows"][row["key"]]],
                 "removed": sorted(set(previous) - set(state["rows"])),
                 "state": {key: value for key, value in payload.items() if key != "chats"}}
        responses[since] = _serialized_json(delta)
    return responses[since]


def state_body(since=None):
    """Serialized /api/state, rebuilt only when one of its input files changed.

    The revision is a hash of the content, so a rewrite that changes nothing a
    client sees (same projects, same index) does not make clients refetch."""
    global _STATE
    with STATE_LOCK:
        sig = _state_signature()  # taken after the wait, so a queued caller reuses a newer build
        if _STATE.get("sig") != sig:
            if _STATE_ERROR and time.time() - _STATE_ERROR["at"] < STATE_RETRY_S:
                raise RuntimeError(_STATE_ERROR["message"])  # still failing; do not rebuild yet
            try:
                payload = build_state()
                body = json.dumps(payload, ensure_ascii=False).encode()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:300]
                if _STATE_ERROR.get("message") != message:  # log once per distinct error
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} /api/state build failed: {message}", flush=True)
                _STATE_ERROR.update(message=message, at=time.time())
                raise
            _STATE_ERROR.clear()
            etag = hashlib.sha256(body).hexdigest()[:24]
            # "revision" stays the last key, as before
            body = body[:-1] + b', "revision": "' + etag.encode() + b'"}'
            if not _STATE:
                _STATE_HISTORY.clear()
            snapshot = {"sig": sig, "etag": etag, "body": body, "payload": payload}
            _remember_state_rows(snapshot)
            _STATE = snapshot  # Publish only after every derived field is ready.
        return _state_response(_STATE, since)


def gzipped_state(state):
    # Each response object owns its compressed representation, including chat
    # and delta responses. A concurrent new state cannot change these bytes.
    with _RESPONSE_LOCK:
        if "gzip" not in state:
            state["gzip"] = gzip.compress(state["body"], 1, mtime=0)
        return state["gzip"]


def data_revision():
    """A cheap token for the browser to learn that library data changed.

    Never waits for a rebuild: a stale cache starts one in the background and
    the next poll sees the new revision once it is ready."""
    state = _STATE
    retry = time.time() - _STATE_ERROR.get("at", 0) >= STATE_RETRY_S
    if not state:
        if retry:
            try:
                return state_body()["etag"]
            except Exception:
                pass
        return ""
    if retry and state["sig"] != _state_signature() and STATE_LOCK.acquire(blocking=False):
        STATE_LOCK.release()
        threading.Thread(target=_rebuild_quietly, daemon=True).start()
    return state["etag"]


def _rebuild_quietly():
    try:
        state_body()
    except Exception:
        pass  # recorded in _STATE_ERROR and logged by state_body


def revision_payload():
    revision = data_revision()
    error = _STATE_ERROR.get("message")
    # Looking up the already imported module must never initialize a broker.
    broker = getattr(sys.modules.get("broker"), "_BROKER", None)
    runs = getattr(getattr(broker, "store", None), "revision", "")
    payload = {"revision": revision, "runs": runs,
               "notice_center": notices.summary(os.path.join(DATA, "notices.json"))}
    if error:
        payload["error"] = error
    return payload


# ---------------------------------------------------------------- actions
def history_refresh_status(job_id):
    with SCAN_LOCK:
        job = _HISTORY_REFRESH_JOBS.get(job_id)
        return dict(job) if job else None


def start_history_refresh(rescan=False):
    """Coalesce concurrent manual requests, promoting a Rescan when requested."""
    with SCAN_LOCK:
        current = next(reversed(_HISTORY_REFRESH_JOBS.values()), None)
        if current and current["running"]:
            current["rescan"] |= rescan
            return {"ok": True, "started": False, "refresh": dict(current)}
        job_id = str(time.monotonic_ns())
        job = {"id": job_id, "running": True, "phase": "local", "rescan": rescan,
               "local_ready": False, "remote_indexed": False, "errors": []}
        _HISTORY_REFRESH_JOBS[job_id] = job
        if len(_HISTORY_REFRESH_JOBS) > 8:
            _HISTORY_REFRESH_JOBS.popitem(last=False)
        result = {"ok": True, "started": True, "refresh": dict(job)}
    threading.Thread(target=do_rescan, args=(job_id,), daemon=True).start()
    return result


def _history_pipeline(reason, mode):
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "pipeline.py"), "--reason", reason,
                        mode, "--wait", "--quiet", "--keep-missing"],
                       cwd=HERE, capture_output=True, timeout=7200, check=True)
        return ""
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"History refresh: {reason}: {exc}", file=sys.stderr)
        return f"{reason} failed. See the refresh log for details."


def do_rescan(job_id):
    job = _HISTORY_REFRESH_JOBS[job_id]
    try:
        local_error = _history_pipeline("manual-local-history", "--parse-only")
        with SCAN_LOCK:
            job.update(phase="remote", local_ready=not local_error,
                       errors=[local_error] if local_error else [])
        remote, remote_errors = {}, []
        try:
            import remote_sync
            remote = remote_sync.sync(run_pipeline=False, wait=True)
            for source in remote.get("sources", []):
                if source.get("last_error"):
                    name = source.get("name") or "Cluster"
                    print(f"History refresh: {name}: {source['last_error']}", file=sys.stderr)
                    remote_errors.append(f"{name}: {source['last_error']}")
        except Exception as exc:
            print(f"History refresh: remote sync failed: {exc}", file=sys.stderr)
            remote_errors.append("Cluster history could not be fetched. See the refresh log for details.")

        remote_changed = remote.get("changed", 0) > 0
        needs_index = remote_changed and remote.get("pipeline") is not True
        full_completed = False
        with SCAN_LOCK:
            job.update(errors=job["errors"] + remote_errors,
                       remote_indexed=remote_changed and not needs_index)
        while True:
            with SCAN_LOCK:
                full_needed = job["rescan"] and not full_completed
                if not full_needed and not needs_index:
                    job.update(phase="done", running=False)
                    return
                job["phase"] = "importing"
            error = (_history_pipeline("rescan", "--uncapped") if full_needed
                     else _history_pipeline("manual-cluster-history", "--parse-only"))
            with SCAN_LOCK:
                if error:
                    job["errors"] = job["errors"] + [error]
                elif remote_changed:
                    job["remote_indexed"] = True
            needs_index = False
            full_completed |= full_needed
    except Exception as exc:
        print(f"History refresh failed: {exc}", file=sys.stderr)
        with SCAN_LOCK:
            job.update(phase="done", running=False,
                       errors=job["errors"] + ["History refresh failed. See the refresh log for details."])


SYNC_EVERY_S = 300
SYNC_LOCK = threading.Lock()
_last_sync = [0.0]


def light_sync(reason):
    """Index changed transcripts without model calls when a client or the server starts.

    Non-blocking and rate-limited. pipeline.py holds the refresh lock; if a refresh is
    already running it leaves a pending marker and that run makes one more pass. Clients
    see the result through /api/revision."""
    with SYNC_LOCK:
        if time.time() - _last_sync[0] < SYNC_EVERY_S:
            return False
        _last_sync[0] = time.time()
    threading.Thread(target=subprocess.run, daemon=True, args=(
        [sys.executable, os.path.join(HERE, "pipeline.py"), "--reason", reason, "--parse-only", "--quiet"],),
        kwargs={"cwd": HERE, "capture_output": True, "timeout": 1800}).start()
    return True


def resume_chat(meta, message, model="", effort=""):
    """Send one message into an existing session via the CLI; returns reply text.
    claude -p --resume appends to the same session file (verified, no fork);
    new_session_id is reported anyway in case a future CLI version forks.

    model/effort are the composer's per-message overrides. Both are checked
    against the lists the composer was built from, so an unknown value is
    dropped rather than handed to the CLI."""
    if str(meta.get("node") or "mac") != "mac":
        return {"ok": False, "error": "This session belongs to another host. Use the host-aware agent workspace."}
    cwd = meta.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        return {"ok": False, "error": "The original workspace is unavailable; select a valid workspace explicitly."}
    opts = compose_options()[meta["source"]]
    if model and model not in {m["id"] for m in opts["models"] if m["id"]}:
        model = ""
    if effort and effort not in opts["efforts"]:
        effort = ""
    if meta["source"] == "claude":
        cmd = [clis.cli("claude"), "-p", "--resume", meta["id"],
               "--output-format", "json"]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        cmd.append(message)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           cwd=cwd, env=clis.env())
        try:
            d = json.loads(r.stdout)
            return {"ok": True, "reply": d.get("result", ""),
                    "new_session_id": d.get("session_id", "")}
        except Exception:
            if r.returncode == 0 and r.stdout.strip():
                return {"ok": True, "reply": r.stdout.strip(), "new_session_id": ""}
            return {"ok": False, "error": (r.stderr or r.stdout or "no output")[-800:]}
    else:
        import tempfile
        with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as tf:
            out_file = tf.name
        try:
            cmd = [clis.cli("codex"), "exec", "resume", meta["id"], message,
                   "--skip-git-repo-check", "-o", out_file]
            if model:
                cmd += ["-m", model]
            if effort:
                # codex has no effort flag; it is a config override
                cmd += ["-c", f'model_reasoning_effort="{effort}"']
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                               cwd=cwd, env=clis.env())
            reply = ""
            if os.path.exists(out_file):
                reply = open(out_file, errors="ignore").read().strip()
            if r.returncode == 0 and reply:
                return {"ok": True, "reply": reply, "new_session_id": ""}
            return {"ok": False, "error": (r.stderr or r.stdout or "codex failed")[-800:]}
        finally:
            if os.path.exists(out_file):
                os.unlink(out_file)


def delete_chat(key):
    """Permanently remove a chat: every source transcript copy + cleaned copy + records."""
    with LOCK:
        index = jload("index", {})
        meta = index.get(key)
        if not meta:
            return {"ok": False, "error": "unknown chat"}
        removed = []
        for p in meta.get("files_all") or [meta["file"]]:
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        cpath = os.path.join(CHATS, key + ".json")
        if os.path.exists(cpath):
            os.remove(cpath)
        index.pop(key, None)
        jsave("index", index)
        for name in ("enrich", "views"):
            d = jload(name, {})
            if key in d:
                d.pop(key)
                jsave(name, d)
        proj = jload("projects", default_projects())
        if key in proj["assignments"]:
            proj["assignments"].pop(key)
            jsave("projects", proj)
        flags = jload("flags", default_flags())
        changed = False
        for name in ("archived", "starred"):
            if key in flags.get(name, []):
                flags[name] = [item for item in flags[name] if item != key]
                changed = True
        if (flags.get("titles") or {}).pop(key, None) is not None:
            changed = True
        if changed:
            jsave("flags", flags)
        return {"ok": True, "removed": removed}


def open_in_editor(path):
    if not os.path.exists(path):
        return {"ok": False, "error": "file no longer exists"}
    commands = []
    if shutil.which("code"):
        commands.append(["code", "-g", path])
    if sys.platform == "darwin" and shutil.which("open"):
        commands.append(["open", path])
    if sys.platform != "darwin" and shutil.which("xdg-open"):
        commands.append(["xdg-open", path])
    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True, timeout=15, check=True)
            return {"ok": True}
        except Exception:
            continue
    return {"ok": False, "error": "no opener worked"}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _trusted_request(self):
        """Only this loopback origin may drive the user's local agent client."""
        host = self.headers.get("Host", "")
        try:
            target = urllib.parse.urlsplit("http://" + host)
            valid = (target.hostname in ("localhost", "127.0.0.1") and
                     (target.port or 80) == self.server.server_port and
                     not target.username and not target.password and
                     not target.path and not target.query and not target.fragment)
            if hasattr(self.headers, "get_all") and len(self.headers.get_all("Host", [])) != 1:
                valid = False
            origin = self.headers.get("Origin")
            if origin is not None:
                source = urllib.parse.urlsplit(origin)
                valid = valid and (source.scheme == "http" and source.hostname == target.hostname and
                                   (source.port or 80) == self.server.server_port and
                                   not source.username and not source.password and
                                   not source.path and not source.query and not source.fragment)
            valid = valid and self.headers.get("Sec-Fetch-Site", "").lower() != "cross-site"
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            self._json({"ok": False, "error": "request origin is not allowed"}, 403)
        return valid

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_state(self, state):
        etag = '"%s"' % state["etag"]
        sent = [t.strip().removeprefix("W/") for t in self.headers.get("If-None-Match", "").split(",")]
        if etag in sent or "*" in sent:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        accepted = {}
        for item in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = [part.strip() for part in item.split(";")]
            quality = 1.0
            for option in parts[1:]:
                if option.startswith("q="):
                    try:
                        quality = float(option[2:])
                    except ValueError:
                        quality = 0.0
            accepted[parts[0]] = quality
        body = gzipped_state(state) if accepted.get("gzip", accepted.get("*", 0)) > 0 else state["body"]
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Vary", "Accept-Encoding")
        if body is not state["body"]:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = os.path.realpath(os.path.join(WEB, rel.lstrip("/")))
        if os.path.commonpath([os.path.realpath(WEB), path]) != os.path.realpath(WEB) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        # Local files change on every edit; a heuristic browser cache served
        # stale app.js/usage.js for hours after a restart.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._trusted_request():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/" or u.path == "/index.html":
            return self._static("index.html")
        if u.path in ("/workbench", "/workbench/"):
            return self._static("workbench/index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        if u.path == "/api/state":
            try:
                state = state_body((q.get("since") or [None])[0])
            except Exception:
                return self._json({"ok": False, "error": "Library state could not be built: " + _STATE_ERROR.get("message", "unknown error")}, 500)
            return self._send_state(state)
        if u.path == "/api/revision":
            return self._json(revision_payload())
        if u.path == "/api/history_refresh":
            job = history_refresh_status((q.get("id") or [""])[0])
            return self._json({"ok": True, "refresh": job}) if job else self._json(
                {"ok": False, "error": "This history refresh is no longer available."}, 404)
        if u.path == "/api/chat":
            key = (q.get("key") or [""])[0]
            try:
                return self._send_state(chat_body(key))
            except FileNotFoundError:
                return self._json({"error": "not found"}, 404)
            except (OSError, ValueError):
                return self._json({"error": "Chat could not be read"}, 500)
        if self._plugin("GET", u.path, {k: v[0] for k, v in q.items()}, None):
            return
        self.send_error(404)

    def do_HEAD(self):
        if not self._trusted_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if self._plugin("HEAD", parsed.path, query, None):
            return
        self.send_error(404)

    def _plugin(self, method, path, query, body):
        """Dispatch to a route registered by a plugin module (live, accounts,
        stream, usage_history). A handler returns a JSON-able object, an
        (object, status) pair, or None when it wrote the response itself
        (SSE streams do that)."""
        fn = PLUGIN_ROUTES.get((method, path))
        if not fn:
            return False
        try:
            res = fn(self, query, body)
        except Exception as e:  # a plugin bug must not take the server down
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return True
        if res is None:
            return True
        if isinstance(res, tuple):
            self._json(res[0], res[1])
        else:
            self._json(res)
        return True

    def do_POST(self):
        if not self._trusted_request():
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return self._json({"ok": False, "error": "Content-Type must be application/json"}, 415)
        length = self.headers.get("Content-Length", "")
        if self.headers.get("Transfer-Encoding") or not re.fullmatch(r"[0-9]+", length):
            return self._json({"ok": False, "error": "invalid Content-Length"}, 400)
        if hasattr(self.headers, "get_all") and len(self.headers.get_all("Content-Length", [])) != 1:
            return self._json({"ok": False, "error": "invalid Content-Length"}, 400)
        if len(length) > 20:
            return self._json({"ok": False, "error": "request exceeds 30 MB"}, 413)
        ln = int(length)
        if ln > 30 * 1024 * 1024:
            return self._json({"ok": False, "error": "request exceeds 30 MB"}, 413)
        try:
            body = json.loads(self.rfile.read(ln) or b"{}")
        except Exception:
            return self._json({"ok": False, "error": "bad json"}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "JSON body must be an object"}, 400)
        try:
            return self._post(body)
        except (KeyError, TypeError, ValueError) as exc:
            return self._json({"ok": False, "error": "invalid request: " + str(exc)}, 400)

    def _post(self, body):
        p = self.path

        if p in ("/api/send", "/api/stream"):
            return self._json({"ok": False, "error": "This execution route has retired. Use /api/runs for managed agent execution."}, 410)

        if p == "/api/view":
            if not isinstance(body.get("key"), str) or not body["key"]:
                raise ValueError("key is required")
            with LOCK:
                views = jload("views", {})
                v = views.setdefault(body["key"], {"views": 0, "last": ""})
                v["views"] += 1
                v["last"] = body.get("ts", "")
                jsave("views", views)
            return self._json({"ok": True})

        if p == "/api/projects":
            act = body.get("action")
            if act not in ("create", "rename", "delete", "reorder"):
                raise ValueError("unknown project action")
            if act in ("create", "rename") and (not isinstance(body.get("name"), str) or not body["name"].strip()):
                raise ValueError("project name is required")
            if act in ("rename", "delete") and (not isinstance(body.get("id"), str) or not body["id"]):
                raise ValueError("project id is required")
            if act == "reorder" and (not isinstance(body.get("order"), list) or not all(isinstance(v, str) for v in body["order"])):
                raise ValueError("project order must be a list of ids")
            with LOCK:
                proj = jload("projects", default_projects())
                act = body.get("action")
                if act == "create":
                    pid = re.sub(r"[^a-z0-9]+", "-", body["name"].lower()).strip("-") or "p"
                    base, i = pid, 2
                    while any(x["id"] == pid for x in proj["projects"]):
                        pid = f"{base}-{i}"; i += 1
                    proj["projects"].append({"id": pid, "name": body["name"],
                                             "pinned_bottom": bool(body.get("pinned_bottom"))})
                elif act == "rename":
                    from workspace_groups import preserve_project
                    preserve_project(proj, body["id"])
                    for x in proj["projects"]:
                        if x["id"] == body["id"]:
                            x["name"] = body["name"]
                elif act == "delete":
                    from workspace_groups import preserve_project
                    preserve_project(proj, body["id"])
                    proj["projects"] = [x for x in proj["projects"] if x["id"] != body["id"]]
                    # chats fall back to the ungrouped pool
                    proj["assignments"] = {k: v for k, v in proj["assignments"].items()
                                           if v != body["id"]}
                elif act == "reorder":
                    order = {pid: i for i, pid in enumerate(body["order"])}
                    proj["projects"].sort(key=lambda x: order.get(x["id"], 999))
                jsave("projects", proj)
            return self._json({"ok": True, "projects": effective_projects(jload("index", {}), jload("projects", default_projects()))})

        if p == "/api/assign":
            with LOCK:
                proj = jload("projects", default_projects())
                keys = body.get("keys") or [body.get("key")]
                keys = [key for key in keys if isinstance(key, str)]
                from workspace_groups import mark_manual
                mark_manual(proj, keys)
                for key in keys:
                    if body.get("project"):
                        proj["assignments"][key] = body["project"]
                    else:
                        proj["assignments"].pop(key, None)
                jsave("projects", proj)
            return self._json({"ok": True})

        if p == "/api/chat_state":
            action = body.get("action")
            if action == "rename":
                key, title = body.get("key"), body.get("title")
                if not isinstance(key, str) or not isinstance(title, str) or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
                    return self._json({"ok": False, "error": "Enter a title between 1 and 200 characters"}, 400)
                with LOCK:
                    if key not in jload("index", {}):
                        return self._json({"ok": False, "error": "Conversation not found"}, 404)
                    flags = jload("flags", default_flags())
                    flags["titles"] = dict(flags.get("titles") or {}, **{key: title.strip()})
                    jsave("flags", flags)
                return self._json({"ok": True, "title": title.strip()})
            if action == "reset_title":  # drop the manual rename; the generated title shows again
                key = body.get("key")
                with LOCK:
                    index = jload("index", {})
                    if not isinstance(key, str) or key not in index:
                        return self._json({"ok": False, "error": "Conversation not found"}, 404)
                    flags = jload("flags", default_flags())
                    titles = dict(flags.get("titles") or {})
                    if titles.pop(key, None) is not None:
                        jsave("flags", dict(flags, titles=titles))
                    m = index[key]
                    title = display_title(key, m, (jload("enrich", {}).get(key) or {}).get("title"))
                return self._json({"ok": True, "title": title})
            if action not in ("archive", "unarchive", "star", "unstar"):
                return self._json({"ok": False, "error": "unknown chat-state action"}, 400)
            keys = body.get("keys") or [body.get("key")]
            keys = {key for key in keys if isinstance(key, str)}
            with LOCK:
                flags = jload("flags", default_flags())
                archived = set(flags.get("archived", []))
                starred = set(flags.get("starred", []))
                target = archived if "archive" in action else starred
                if action.startswith("un"):
                    target.difference_update(keys)
                else:
                    target.update(keys)
                flags = {**flags, "archived": sorted(archived), "starred": sorted(starred)}
                jsave("flags", flags)
            return self._json({"ok": True, "chat_flags": flags})

        if p == "/api/delete_chat":
            if not isinstance(body.get("key"), str) or not body["key"]:
                raise ValueError("key is required")
            return self._json(delete_chat(body["key"]))

        if p == "/api/delete_chats":
            keys = body.get("keys") or []
            results = [delete_chat(key) for key in keys if isinstance(key, str)]
            return self._json({"ok": all(item.get("ok") for item in results),
                               "deleted": sum(1 for item in results if item.get("ok")),
                               "results": results})

        if p == "/api/config":
            if "excluded_cwds" in body and (not isinstance(body["excluded_cwds"], list) or
                    any(not isinstance(value, str) or not value.strip() for value in body["excluded_cwds"])):
                raise ValueError("excluded_cwds must be a list of nonempty paths")
            with LOCK:
                cfg = jload("config", {})
                cfg = cfg if isinstance(cfg, dict) else {}
                if "excluded_cwds" in body:
                    cfg["excluded_cwds"] = body["excluded_cwds"]
                if body.get("freshness_window") in ("live", "1d", "3d", "7d"):
                    cfg["freshness_window"] = body["freshness_window"]
                for key in ("auto_approve_tools", "attention_sound"):  # Settings > Agents
                    if isinstance(body.get(key), bool):
                        cfg[key] = body[key]
                if body.get("agent_questions") in ("defer", "ask"):
                    cfg["agent_questions"] = body["agent_questions"]
                compact = body.get("auto_compact")
                if isinstance(compact, dict):
                    if not (isinstance(compact.get("enabled", True), bool) and
                            compact.get("threshold", 0.5) in (0.4, 0.5, 0.6, 0.7) and
                            compact.get("idle_minutes", 0) in (0, 15, 30, 60)):
                        raise ValueError("auto_compact needs enabled, threshold 0.4-0.7 and idle_minutes 0, 15, 30 or 60")
                    old = cfg.get("auto_compact") if isinstance(cfg.get("auto_compact"), dict) else {}
                    cfg["auto_compact"] = {**old, **{k: compact[k] for k in ("enabled", "threshold", "idle_minutes") if k in compact}}
                jsave("config", cfg)
            return self._json({"ok": True})

        if p == "/api/rescan":
            return self._json(start_history_refresh(rescan=True))

        if p == "/api/sync":
            if body.get("include_remote") is True:
                return self._json(start_history_refresh())
            return self._json({"ok": True, "started": light_sync("client-open")})

        if p == "/api/open":
            return self._json(open_in_editor(body.get("path", "")))

        if p == "/api/send":
            index = jload("index", {})
            meta = index.get(body.get("key", ""))
            if not meta:
                return self._json({"ok": False, "error": "unknown chat"}, 404)
            try:
                res = resume_chat(meta, body.get("message", ""),
                                  str(body.get("model") or ""),
                                  str(body.get("effort") or ""))
            except subprocess.TimeoutExpired:
                res = {"ok": False, "error": "CLI timed out (600s)"}
            if res.get("ok"):
                # persist the continued exchange into the cleaned copy so the UI
                # (and future sessions) see it even before a rescan
                with LOCK:
                    cpath = os.path.join(CHATS, body["key"] + ".json")
                    if os.path.isfile(cpath):
                        chat = json.load(open(cpath))
                        chat["turns"].append({"role": "user",
                                              "text": body.get("message", ""), "ts": ""})
                        chat["turns"].append({"role": "assistant",
                                              "text": res.get("reply", ""), "ts": ""})
                        if res.get("new_session_id"):
                            chat["resumed_as"] = res["new_session_id"]
                        json.dump(chat, open(cpath, "w"), ensure_ascii=False)
            return self._json(res)

        if self._plugin("POST", p, {}, body):
            return
        self.send_error(404)


# ---------------------------------------------------------------- plugins
# Each optional module exposes ROUTES {(method, path): handler} and
# BACKGROUND [(fn, interval_seconds)]. Missing or broken modules are skipped
# so the core library keeps serving. Contracts: docs/V2-CONTRACTS.md.
PLUGIN_MODULES = ("live", "accounts", "stream", "usage_history", "remote_sync", "projects", "hosts", "broker", "session_lineage", "commands", "file_service", "preview_server", "search_api", "open_destinations", "conversation_settings", "settings_insights", "session_reference", "host_models", "project_suggestions", "model_updates")
PLUGIN_ROUTES = {}
PLUGIN_BACKGROUND = []


def load_plugins():
    import importlib
    for name in PLUGIN_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            print(f"plugin {name}: not loaded ({type(e).__name__}: {e})")
            continue
        PLUGIN_ROUTES.update(getattr(mod, "ROUTES", {}))
        PLUGIN_BACKGROUND.extend(getattr(mod, "BACKGROUND", []))


def run_background():
    def loop(fn, every):
        while True:
            try:
                fn()
            except Exception as e:
                print(f"background {fn.__module__}.{fn.__name__}: {type(e).__name__}: {e}")
            time.sleep(every)
    for fn, every in PLUGIN_BACKGROUND:
        threading.Thread(target=loop, args=(fn, every), daemon=True).start()


def main():
    sys.setswitchinterval(0.0005)
    cfg = jload("config", {})
    cfg = cfg if isinstance(cfg, dict) else {}
    port = int(cfg.get("port", 8744))
    load_plugins()
    run_background()
    light_sync("server-start")
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"chat dashboard: http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
