#!/usr/bin/env python3
"""
stream.py -- streaming chat plugin for the dashboard (docs/V2-CONTRACTS.md,
"Chat streaming (stream.py)").

  POST /api/stream   SSE: start, delta, tool, done, error
  GET  /api/cwds     the 30 most used cwds from the index (new-chat picker)

Runs a real user chat through the local CLI, so MCP servers and hooks load as
in a normal session (no --strict-mcp-config). The message goes in on stdin, so
a message that starts with "-" is never parsed as a flag. Stdlib only.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clis  # noqa: E402

DATA = os.path.join(HERE, "data")
CHATS = os.path.join(DATA, "chats")
INDEX = os.path.join(DATA, "index.json")
LOCK = threading.Lock()
TIMEOUT_S = 1800
CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CODEX_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


# ---------------------------------------------------------------- helpers
def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _jload(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _jsave(path, obj, indent=None):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def _account(tool, wanted):
    """Resolve a configured profile without switching or silently substituting."""
    from host_connector import local_profile
    acc, _home, env = local_profile(tool, wanted or None)
    return acc, env


def _claude_transcript(env, cwd, sid):
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(os.path.expanduser(base), "projects", slug, sid + ".jsonl")


def _codex_rollout(env, sid):
    base = os.path.expanduser(env.get("CODEX_HOME") or "~/.codex")
    hits = glob.glob(os.path.join(base, "sessions", "*", "*", "*", f"rollout-*-{sid}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else ""


def _sse(req, event, data):
    req.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
    req.wfile.flush()


def _preview(obj, n=160):
    if isinstance(obj, dict):
        for k in ("command", "file_path", "path", "pattern", "query", "url", "description"):
            if obj.get(k):
                return str(obj[k])[:n]
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s[:n]


# ---------------------------------------------------------------- commands
def _claude_cmd(sid, model, effort):
    cmd = [clis.cli("claude"), "-p", "--output-format", "stream-json", "--verbose",
           "--include-partial-messages"]
    if sid:
        cmd += ["--resume", sid]
    if model:
        cmd += ["--model", model]
    if effort in CLAUDE_EFFORTS:
        cmd += ["--effort", effort]
    return cmd


def _codex_cmd(sid, model, effort):
    cmd = [clis.cli("codex"), "exec"]
    if sid:
        cmd += ["resume", sid]
    cmd += ["--json", "--skip-git-repo-check"]
    if model:
        cmd += ["-m", model]
    if effort in CODEX_EFFORTS:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    return cmd + ["-"]  # "-" = read the prompt from stdin


# ---------------------------------------------------------------- parsers
class _State:
    def __init__(self, sid):
        self.sid = sid
        self.text = ""          # final answer text
        self.cost = None
        self.usage = None
        self.error = ""
        self.started = False


def _parse_claude(ev, st, emit):
    t = ev.get("type")
    if ev.get("session_id"):
        st.sid = ev["session_id"]
    if t == "stream_event":
        e = ev.get("event") or {}
        d = e.get("delta") or {}
        if e.get("type") == "content_block_delta" and d.get("type") == "text_delta" and d.get("text"):
            emit("delta", {"text": d["text"]})
    elif t == "assistant":
        for blk in (ev.get("message") or {}).get("content") or []:
            if blk.get("type") == "tool_use":
                emit("tool", {"name": blk.get("name", ""), "input_preview": _preview(blk.get("input"))})
    elif t == "result":
        st.text = ev.get("result") or ""
        st.cost = ev.get("total_cost_usd")
        st.usage = ev.get("usage")
        if ev.get("is_error"):
            st.error = st.text or ev.get("subtype") or "claude error"


def _parse_codex(ev, st, emit):
    t = ev.get("type")
    if t == "thread.started" and ev.get("thread_id"):
        st.sid = ev["thread_id"]
    elif t == "item.completed":
        it = ev.get("item") or {}
        kind = it.get("type") or it.get("item_type")
        if kind == "agent_message" and it.get("text"):
            chunk = ("\n\n" if st.text else "") + it["text"]
            st.text += chunk
            emit("delta", {"text": chunk})
        elif kind in ("command_execution", "mcp_tool_call", "web_search", "file_change"):
            inp = it.get("command") or it.get("tool") or it.get("query") or it.get("changes") or ""
            emit("tool", {"name": kind, "input_preview": _preview(inp)})
    elif t == "turn.completed":
        st.usage = ev.get("usage")
    elif t in ("turn.failed", "error"):
        err = ev.get("error") or {}
        msg = (err.get("message") if isinstance(err, dict) else str(err)) or ev.get("message") or t
        try:  # codex wraps the API error body as a JSON string
            inner = json.loads(msg)
            msg = (inner.get("error") or {}).get("message") or msg
        except (ValueError, AttributeError, TypeError):
            pass
        st.error = st.error or msg


# ---------------------------------------------------------------- persistence
def _already_has(turns, message, reply):
    """True when the last exchange is already this message and reply. `claude -p`
    fires SessionEnd, whose pipeline run can parse the transcript before we get
    here; appending again would show the exchange twice."""
    tail = [t for t in turns[-6:] if t.get("role") in ("user", "assistant")]
    for i, t in enumerate(tail):
        if t["role"] == "user" and (t.get("text") or "").strip() == message.strip():
            after = [a for a in tail[i + 1:] if a["role"] == "assistant"]
            if after and (after[-1].get("text") or "").strip() == reply.strip():
                return True
    return False


def _persist(key, tool, sid, cwd, file_path, message, reply, is_new, t_user, t_asst):
    turns = [{"role": "user", "text": message, "ts": t_user},
             {"role": "assistant", "text": reply, "ts": t_asst}]
    with LOCK:
        os.makedirs(CHATS, exist_ok=True)
        cpath = os.path.join(CHATS, key + ".json")
        chat = _jload(cpath, None)
        if chat is None:
            chat = {"key": key, "source": tool, "id": sid, "file": file_path, "cwd": cwd,
                    "title_seed": message.strip().splitlines()[0][:80] if message.strip() else "",
                    "ts_first": t_user, "ts_last": t_asst, "n_user": 0, "n_asst": 0,
                    "n_answer": 0, "last_role": "assistant", "status": "complete",
                    "files_all": [file_path] if file_path else [], "turns": []}
        if _already_has(chat["turns"], message, reply):
            return  # the session hook's pipeline run indexed this exchange first
        chat["turns"].extend(turns)
        for f in ("n_user", "n_asst", "n_answer"):
            if isinstance(chat.get(f), int):
                chat[f] += 1
        chat["ts_last"] = t_asst
        chat["last_role"] = "assistant"
        _jsave(cpath, chat)

        index = _jload(INDEX, None)
        if not isinstance(index, dict):
            return  # never overwrite an unreadable index with a near-empty one
        rec = index.get(key)
        if rec is None:
            if not is_new:
                return
            rec = {"key": key, "source": tool, "id": sid, "file": file_path,
                   "files_all": [file_path] if file_path else [], "cwd": cwd,
                   "title_seed": chat["title_seed"], "ts_first": t_user, "ts_last": t_asst,
                   "n_user": 1, "n_asst": 1, "n_answer": 1, "last_role": "assistant",
                   "status": "complete", "attention_required": False, "n_files": 0,
                   "first_prompt": message[:400], "node": "mac"}
        else:
            rec["ts_last"] = t_asst
            rec["last_role"] = "assistant"
            for f in ("n_user", "n_asst", "n_answer"):
                rec[f] = int(rec.get(f) or 0) + 1
        index[key] = rec
        _jsave(INDEX, index, indent=1)


# ---------------------------------------------------------------- routes
def handle_stream(req, query, body):
    body = body or {}
    message = str(body.get("message") or "")
    if not message.strip():
        return {"ok": False, "error": "empty message"}, 400
    model = str(body.get("model") or "")
    effort = str(body.get("effort") or "")

    if body.get("key"):
        key = str(body["key"])
        meta = _jload(INDEX, {}).get(key)
        if not meta:
            return {"ok": False, "error": "unknown chat"}, 404
        node = meta.get('execution_host') or meta.get('host_id') or meta.get('node') or 'mac'
        if node not in ('mac', 'local'):
            return {"ok": False, "error": "Remote chats require the host-aware run endpoint; no local fallback is permitted"}, 400
        tool, sid, cwd, is_new = meta["source"], meta["id"], meta.get("cwd") or "", False
    else:
        new = body.get("new") or {}
        tool, sid, cwd, is_new, key = new.get("tool") or "claude", "", new.get("cwd") or "", True, ""
    if tool not in ("claude", "codex"):
        return {"ok": False, "error": f"unknown tool {tool}"}, 400
    if model and not re.fullmatch(r"[A-Za-z0-9._\-\[\]:]+", model):
        return {"ok": False, "error": "bad model id"}, 400
    if not cwd:
        return {"ok": False, "error": "working directory is required"}, 400
    cwd = os.path.expanduser(cwd)
    if not os.path.isdir(cwd):
        return {"ok": False, "error": f"cwd does not exist: {cwd}"}, 400

    try:
        wanted = str(body.get("account") or (meta.get('account_id') if not is_new else '') or '')
        if not is_new and meta.get('account_id') and wanted != meta['account_id']:
            raise ValueError('native resume requires the original account')
        account, env = _account(tool, wanted)
        if sid:
            from host_connector import _session_exists
            home = env.get('CLAUDE_CONFIG_DIR' if tool == 'claude' else 'CODEX_HOME') or os.path.expanduser('~/.claude' if tool == 'claude' else '~/.codex')
            if not _session_exists(tool, home, sid, os.path.realpath(cwd)):
                raise ValueError('session is absent from the selected account and workspace')
    except (ValueError, ImportError) as exc:
        return {"ok": False, "error": str(exc)}, 400
    if tool == "codex" and not model and not sid:
        # ~/.codex/config.toml may name a model the ChatGPT account rejects
        # (gpt-6.1-sol did on 2026-10-21); a new chat needs a model that works.
        model = str(_jload(os.path.join(DATA, "config.json"), {}).get("codex_default_model")
                    or "gpt-6-sol")
    cmd = (_claude_cmd if tool == "claude" else _codex_cmd)(sid, model, effort)
    parse = _parse_claude if tool == "claude" else _parse_codex

    req.send_response(200)
    req.send_header("Content-Type", "text/event-stream; charset=utf-8")
    req.send_header("Cache-Control", "no-cache")
    req.send_header("X-Accel-Buffering", "no")
    req.send_header("Connection", "close")
    req.end_headers()
    req.close_connection = True

    st = _State(sid)
    t_user = _now_iso()
    deadline = time.time() + TIMEOUT_S

    def emit(event, data):
        if not st.started and event != "error":
            _start()
        _sse(req, event, data)

    def _start():
        st.started = True
        k = key or (f"{tool}-{st.sid}" if st.sid else "")
        _sse(req, "start", {"session_id": st.sid, "key": k, "account": account})

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except Exception as e:
        _sse(req, "error", {"message": f"could not start {tool}: {e}"})
        return None
    stderr_buf = []
    threading.Thread(target=lambda: stderr_buf.append(proc.stderr.read()), daemon=True).start()
    timer = threading.Timer(TIMEOUT_S, proc.kill)
    timer.daemon = True
    timer.start()
    try:
        try:
            proc.stdin.write(message)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            parse(ev, st, emit)
            if not st.started and st.sid:
                _start()
        proc.wait(timeout=30)
    except (BrokenPipeError, ConnectionResetError):
        proc.kill()  # client went away; stop the CLI instead of running on
        timer.cancel()
        return None
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()

    try:
        if time.time() > deadline:
            _sse(req, "error", {"message": f"{tool} timed out ({TIMEOUT_S}s)"})
            return None
        if st.error or not st.sid or (proc.returncode != 0 and not st.text):
            msg = st.error or "".join(stderr_buf).strip()[-800:] or f"{tool} exited {proc.returncode}"
            _sse(req, "error", {"message": msg})
            return None
        key = key or f"{tool}-{st.sid}"
        if not st.started:
            _start()
        if tool == "claude":
            fpath = _claude_transcript(env, cwd, st.sid)
        else:
            fpath = _codex_rollout(env, st.sid)
        _persist(key, tool, st.sid, cwd, fpath, message, st.text, is_new, t_user, _now_iso())
        done = {"text": st.text, "session_id": st.sid, "key": key}
        if st.cost is not None:
            done["cost_usd"] = st.cost
        if st.usage:
            done["usage"] = st.usage
        _sse(req, "done", done)
    except (BrokenPipeError, ConnectionResetError):
        pass
    return None


def handle_cwds(req, query, body):
    counts, last = {}, {}
    for m in _jload(INDEX, {}).values():
        c = m.get("cwd")
        if not c or "mac" not in str(m.get("node") or "mac"):
            continue
        counts[c] = counts.get(c, 0) + 1
        last[c] = max(last.get(c, ""), m.get("ts_last") or "")
    top = sorted(counts, key=lambda c: (-counts[c], c))[:30]
    return {"cwds": [{"cwd": c, "n": counts[c], "last": last[c],
                      "exists": os.path.isdir(c)} for c in top]}


ROUTES = {("POST", "/api/stream"): handle_stream, ("GET", "/api/cwds"): handle_cwds}
BACKGROUND = []
