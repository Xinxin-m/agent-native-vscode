#!/usr/bin/env python3
"""sessions: read-only view of every chat session the chat dashboard knows about.

Lets any agent (Claude Code or Codex, in any workspace) see which sessions are
live or recent and which ones failed or are stuck, so one agent can diagnose
another tab's problem.

    sessions live | recent | problems | show KEY | tail KEY | accounts | ref KEY

Read-only: it never resumes, sends to, stops or restarts anything and never
writes under data/. It asks the dashboard server for live state when the server
is up and reads data/index.json, data/execution/runs/*/run.json and the native
transcripts when it is down. Python 3 standard library only.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(os.environ.get("CHAT_DASHBOARD_DIR") or Path(__file__).resolve().parent)
DATA = Path(os.environ.get("SESSIONS_DATA_DIR") or ROOT / "data")
HOME = Path.home()

STALE_RUNNING_S = 30 * 60      # a "running" run with no write for this long is stale
LIVE_SNAPSHOT_MAX_S = 180      # older data/live.json is refreshed from the server
PROBLEM_HOURS = 48

NOTES = []                     # one-line caveats printed with the output
_state = {"server": None}


# ---------------------------------------------------------------- config

def load_config():
    for name in ("config.json", "config.example.json"):
        try:
            return json.loads((DATA / name).read_text())
        except (OSError, ValueError):
            continue
    return {}


CFG = load_config()
PORT = int(os.environ.get("SESSIONS_PORT") or CFG.get("port") or 8744)
CLAUDE_ROOT = Path(CFG.get("claude_projects_root") or HOME / ".claude" / "projects")
CODEX_ROOT = Path(CFG.get("codex_sessions_root") or HOME / ".codex" / "sessions")


def mirror_roots(kind):
    """Transcript roots of other hosts synced under data/remote/<host>/."""
    sub = "claude-projects" if kind == "claude" else "codex-sessions"
    return {p.parent.name: p for p in sorted(DATA.glob("remote/*/" + sub)) if p.is_dir()}


def cache_dir():
    override = os.environ.get("SESSIONS_CACHE_DIR")
    for base in ([Path(override)] if override else
                 [HOME / "Library" / "Caches" / "sessions-cli", Path(tempfile.gettempdir()) / "sessions-cli"]):
        try:
            base.mkdir(parents=True, exist_ok=True)
            return base
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- small helpers

def now():
    return time.time()


def iso_ts(s):
    if not s:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def age(ts, ref=None):
    if not ts:
        return "-"
    d = max(0, (ref or now()) - ts)
    if d < 90:
        return "%ds" % d
    if d < 5400:
        return "%dm" % (d / 60)
    if d < 172800:
        return "%dh" % (d / 3600)
    return "%dd" % (d / 86400)


def local_str(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).astimezone().strftime("%b %d %H:%M %Z")


def short(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def tilde(path):
    path = path or ""
    h = str(HOME)
    return "~" + path[len(h):] if path.startswith(h) else path


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def probe_server():
    """One quick connect so every command can say whether the dashboard server is up."""
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.3).close()
        _state["server"] = True
    except OSError:
        _state["server"] = False
        note("dashboard server not reachable on port %d: using data/ files and native transcripts" % PORT)


def http_json(path, timeout=2.0):
    if _state["server"] is False:
        return None
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=timeout) as r:
            data = json.loads(r.read())
        _state["server"] = True
        return data
    except (OSError, ValueError):
        if _state["server"] is None:
            _state["server"] = False
        return None


def note(msg):
    if msg not in NOTES:
        NOTES.append(msg)


# ---------------------------------------------------------------- processes

def process_table():
    """pid -> (stat, approximate start epoch). One ps call."""
    out = {}
    try:
        txt = subprocess.run(["ps", "-axo", "pid=,stat=,etime="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    t = now()
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        out[int(parts[0])] = (parts[1], t - etime_seconds(parts[2]))
    return out


def etime_seconds(s):
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    nums = [int(x) for x in s.split(":")]
    while len(nums) < 3:
        nums.insert(0, 0)
    return days * 86400 + nums[0] * 3600 + nums[1] * 60 + nums[2]


_ps = {}


def process_state(pid, identity=None):
    """'alive', 'zombie', 'dead' or None when it cannot be known."""
    if not pid:
        return None
    if not _ps:
        _ps.update(process_table() or {-1: ("", 0)})
    entry = _ps.get(int(pid))
    if entry is None:
        return "dead"
    if entry[0].startswith("Z"):
        return "zombie"
    start = (identity or {}).get("start_sec")
    if start and abs(entry[1] - start) > 180:
        return "dead"          # the pid was reused by another process
    return "alive"


# ---------------------------------------------------------------- failure wording
# Wording comes from real transcripts and the provider binaries, not guesses:
#  Claude assistant error turns: "You've hit your weekly limit · resets Nov 17 at 12am (America/Los_Angeles)",
#    "Failed to authenticate: this login reached its time limit ... Run `claude auth login`", "Not logged in · Please run /login".
#  Codex task_complete.error {message, codex_error_info}: "You've hit your usage limit. ... or try again at Aug 26th, 2026 8:11 AM."
#    (codex_error_info usage_limit_exceeded | server_overloaded | other); auth wording from the binary:
#    "Your access token could not be refreshed. Please log out and sign in again."

CLAUDE_RESET = re.compile(
    r"resets\s+(?:(?P<mon>[A-Z][a-z]{2})[a-z]*\s+(?P<day>\d{1,2})(?:,|\s+at)?\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
    r"(?:\s*\((?P<tz>[^)]+)\))?", re.I)
CODEX_RESET = re.compile(
    r"try again at (?P<mon>[A-Z][a-z]{2})[a-z]* (?P<day>\d{1,2})(?:st|nd|rd|th)?,? (?P<year>\d{4}) (?P<h>\d{1,2}):(?P<m>\d{2}) ?(?P<ap>AM|PM)", re.I)
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _hour(h, ap):
    h = int(h) % 12
    return h + 12 if ap.lower() == "pm" else h


def parse_reset(text, ref_ts):
    """Absolute epoch of the limit reset named in an error message, or None."""
    m = CODEX_RESET.search(text or "")
    if m:
        try:
            dt = datetime(int(m["year"]), MONTHS[m["mon"][:3].lower()], int(m["day"]), _hour(m["h"], m["ap"]), int(m["m"]))
            return dt.astimezone().timestamp()
        except (ValueError, KeyError):
            return None
    m = CLAUDE_RESET.search(text or "")
    if not m:
        return None
    tz = None
    if m["tz"] and ZoneInfo:
        try:
            tz = ZoneInfo(m["tz"].strip())
        except Exception:
            tz = None
    ref = datetime.fromtimestamp(ref_ts or now(), tz)
    hour, minute = _hour(m["h"], m["ap"]), int(m["m"] or 0)
    try:
        if m["mon"]:
            dt = ref.replace(month=MONTHS[m["mon"][:3].lower()], day=int(m["day"]), hour=hour, minute=minute, second=0, microsecond=0)
            if dt.timestamp() < ref.timestamp() - 30 * 86400:
                dt = dt.replace(year=dt.year + 1)
        else:
            dt = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= ref:
                dt = dt + timedelta(days=1)
    except (ValueError, KeyError):
        return None
    return dt.timestamp()


def classify(text, code="", ref_ts=None, failure=None):
    """-> {kind, resets_at, raw}. kind: auth_expired, not_logged_in, limit_reached, rate_limited, other_error."""
    raw = (text or "").strip()
    if isinstance(failure, dict) and failure.get("kind"):
        kind = failure["kind"]
        kind = "other_error" if kind == "other" else kind
        resets = iso_ts(failure.get("resets_at")) or parse_reset(raw, ref_ts) if kind == "limit_reached" else None
        return {"kind": kind, "resets_at": resets or None, "raw": failure.get("raw") or raw, "from": "server"}
    low = raw.lower()
    code = (code or "").lower()
    if "not logged in" in low or "please run /login" in low:
        kind = "not_logged_in"
    elif ("failed to authenticate" in low or "login reached its time limit" in low or code == "authentication_failed"
          or "could not be refreshed" in low or "log out and sign in again" in low or "failed to refresh token" in low
          or "refresh token was rejected" in low or "invalid authentication credentials" in low):
        kind = "auth_expired"
    elif (re.search(r"hit your .{0,30}limit", low) or "usage limit" in low
          or code in ("usage_limit_reached", "usage_limit_exceeded")):
        kind = "limit_reached"
    elif ("rate limit" in low or "rate_limit" in code or "server_overloaded" in code or "at capacity" in low
          or "overloaded" in low or re.search(r"\b429\b", low)):
        kind = "rate_limited"
    else:
        kind = "other_error"
    resets = parse_reset(raw, ref_ts) if kind == "limit_reached" else None
    return {"kind": kind, "resets_at": resets, "raw": raw, "from": "local"}


# ---------------------------------------------------------------- transcripts

def tail_raw(path, nbytes=262144):
    """Last complete lines of a file as bytes, oldest first."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > nbytes:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def tail_lines(path, nbytes=262144):
    out = []
    for ln in tail_raw(path, nbytes):
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def reversed_events(raw, markers):
    """Parsed lines, newest first, skipping lines that cannot be the ones wanted without parsing them."""
    for ln in reversed(raw):
        if any(m in ln for m in markers):
            try:
                yield json.loads(ln)
            except ValueError:
                continue


def claude_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text") or "")
    return "\n".join(parts)


NOISE = re.compile(r"<(system-reminder|browser_instruction|command-name|command-message|command-args|local-command-stdout|"
                   r"local-command-caveat|ide_opened_file|ide_selection|user-prompt-submit-hook|task-notification)>.*?</\1>", re.S)
CODEX_NOISE = ("<environment_context>", "# AGENTS.md instructions", "<user_instructions>", "<turn_aborted>", "<permissions instructions>")


def claude_turns(objs):
    turns = []
    for o in objs:
        t = o.get("type")
        if t not in ("user", "assistant") or o.get("isMeta") or o.get("isSidechain"):
            continue
        msg = o.get("message") or {}
        text = NOISE.sub("", claude_text(msg.get("content"))).strip()
        if not text:
            continue
        err = bool(o.get("isApiErrorMessage"))
        turns.append({"role": t, "ts": o.get("timestamp"), "text": text, "error": err,
                      "code": (o.get("error") or o.get("apiError") or "") if err else ""})
    return turns


def codex_turns(objs):
    """Clean turns from response_item messages (older rollouts: event_msg messages), plus failed-turn errors."""
    items, events, errors = [], [], []
    for o in objs:
        p = o.get("payload") or {}
        if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            text = "\n".join(b.get("text", "") for b in p.get("content") or [] if isinstance(b, dict)).strip()
            if text and not text.startswith(CODEX_NOISE):
                items.append({"role": p["role"], "ts": o.get("timestamp"), "text": text, "error": False, "code": ""})
        elif o.get("type") == "event_msg":
            pt = p.get("type")
            text = (p.get("message") or "").strip()
            if pt in ("user_message", "agent_message") and text and not text.startswith(CODEX_NOISE):
                events.append({"role": "user" if pt == "user_message" else "assistant", "ts": o.get("timestamp"),
                               "text": text, "error": False, "code": ""})
            elif pt == "task_complete" and p.get("error"):
                e = p["error"]
                errors.append({"role": "assistant", "ts": o.get("timestamp"), "text": e.get("message") or str(e),
                               "error": True, "code": e.get("codex_error_info") or ""})
    return sorted((items or events) + errors, key=lambda t: iso_ts(t["ts"]))


def read_turns(provider, path, n):
    """Last n cleaned turns of a native transcript."""
    parse = claude_turns if provider == "claude" else codex_turns
    nbytes = 262144
    while True:
        turns = parse(tail_lines(path, nbytes))
        if len(turns) >= n or nbytes >= os.path.getsize(path):
            return turns[-n:]
        nbytes *= 4


def transcript_state(provider, path):
    """Latest outcome recorded in a transcript: {state, ts, text, code}.

    state: error | ok | working | complete | aborted | None
    """
    size = os.path.getsize(path) if os.path.exists(path) else 0
    nbytes = 16384
    while True:
        st = _state_from(provider, tail_raw(path, nbytes))
        if st["state"] or nbytes >= size:
            return st
        nbytes *= 8


def _state_from(provider, raw):
    if provider == "claude":
        for o in reversed_events(raw, (b'"assistant"', b'"user"')):
            if o.get("type") in ("user", "assistant") and not o.get("isMeta"):
                if o.get("isApiErrorMessage"):
                    return {"state": "error", "ts": iso_ts(o.get("timestamp")), "code": o.get("error") or o.get("apiError") or "",
                            "text": claude_text((o.get("message") or {}).get("content"))}
                return {"state": "ok", "ts": iso_ts(o.get("timestamp")), "code": "", "text": ""}
        return {"state": None, "ts": 0, "code": "", "text": ""}
    for o in reversed_events(raw, (b"task_complete", b"turn_aborted", b"task_started")):
        p = o.get("payload") or {}
        if o.get("type") != "event_msg":
            continue
        pt = p.get("type")
        if pt == "task_complete":
            e = p.get("error")
            if e:
                return {"state": "error", "ts": iso_ts(o.get("timestamp")), "code": e.get("codex_error_info") or "",
                        "text": e.get("message") or ""}
            return {"state": "complete", "ts": iso_ts(o.get("timestamp")), "code": "", "text": ""}
        if pt == "turn_aborted":
            return {"state": "aborted", "ts": iso_ts(o.get("timestamp")), "code": "", "text": ""}
        if pt == "task_started":
            return {"state": "working", "ts": iso_ts(o.get("timestamp")), "code": "", "text": ""}
    return {"state": None, "ts": 0, "code": "", "text": ""}


def find_transcript(provider, uuid):
    """Main transcript path for a session id (mac first, then mirrored hosts)."""
    if not uuid:
        return None, "mac"
    roots = [("mac", CLAUDE_ROOT if provider == "claude" else CODEX_ROOT)] + list(mirror_roots(provider).items())
    for host, root in roots:
        pat = str(root / "*" / (uuid + ".jsonl")) if provider == "claude" else str(root / "*" / "*" / "*" / ("rollout-*-" + uuid + ".jsonl"))
        hits = glob.glob(pat)
        if hits:
            return max(hits, key=mtime), host
    return None, "mac"


def subagent_files(provider, path):
    if provider != "claude" or not path:
        return []
    return sorted(glob.glob(str(Path(path).with_suffix("")) + "/subagents/*.jsonl"))


# ---------------------------------------------------------------- dashboard data

def load_runs():
    """Broker runs from data/execution/runs/*/run.json (faster than the server under load), else the server."""
    runs = []
    for p in glob.glob(str(DATA / "execution" / "runs" / "*" / "run.json")):
        r = read_json(p)
        if isinstance(r, dict):
            runs.append(r)
    if runs:
        return runs
    d = http_json("/api/runs", 5.0)
    if d and isinstance(d.get("runs"), list):
        return d["runs"]
    note("no broker runs found under data/execution/runs and the dashboard server is not reachable on port %d" % PORT)
    return []


def load_live(max_age=LIVE_SNAPSHOT_MAX_S):
    """data/live.json when fresh, else the server, else the stale snapshot, else None."""
    path = DATA / "live.json"
    snap = read_json(path)
    fresh = snap and now() - mtime(path) < max_age
    if not fresh:
        d = http_json("/api/live", 4.0)
        if d:
            return d
    if snap and not fresh:
        note("live snapshot is %s old and the server did not refresh it" % age(mtime(path)))
    return snap


def load_accounts_cfg():
    """Account definitions plus cached usage from data/accounts.json."""
    a = read_json(DATA / "accounts.json") or {}
    cache = a.get("cache") or {}
    out = []
    for acc in a.get("accounts") or []:
        c = dict(acc)
        c.update({k: v for k, v in (cache.get(acc["id"]) or {}).items() if k not in ("email", "org")})
        out.append(c)
    return out


def load_index():
    """Slim copy of data/index.json plus titles, cached by file signature."""
    ipath, epath = DATA / "index.json", DATA / "enrich.json"
    sig = [mtime(ipath), mtime(epath)]
    cdir = cache_dir()
    cpath = cdir / "index-slim.json" if cdir else None
    if cpath:
        c = read_json(cpath)
        if c and c.get("sig") == sig:
            return c["chats"]
    raw = read_json(ipath) or {}
    titles = read_json(epath) or {}
    chats = {}
    for k, v in raw.items():
        t = titles.get(k)
        chats[k] = {
            "key": k, "provider": v.get("source"), "id": v.get("id"), "file": v.get("file"),
            "files_all": (v.get("files_all") or [])[:6], "cwd": v.get("cwd"),
            "title": (t.get("title") if isinstance(t, dict) else None) or v.get("title_seed") or "",
            "ts_first": iso_ts(v.get("ts_first")), "ts_last": iso_ts(v.get("ts_last")),
            "node": v.get("node") or v.get("host_id") or "mac", "model": v.get("model_main"),
            "account": v.get("account_id"), "last_run_id": v.get("last_run_id"),
            "n_children": len(v.get("spawned_children") or []),
        }
    if cpath and chats:
        try:
            tmp = cpath.with_suffix(".%d.tmp" % os.getpid())
            tmp.write_text(json.dumps({"sig": sig, "chats": chats}))
            os.replace(tmp, cpath)
        except OSError:
            pass
    return chats


# ---------------------------------------------------------------- the model of one chat

def split_key(key):
    prov, _, uuid = key.partition("-")
    return prov, uuid


class Context:
    """Everything loaded once per command."""

    def __init__(self, need_index=True, live_age=LIVE_SNAPSHOT_MAX_S):
        if _state["server"] is None:
            probe_server()
        self.runs = load_runs()
        self.live = load_live(live_age) or {}
        self.index = load_index() if need_index else {}
        if need_index and not self.index:
            note("data/index.json missing or empty: titles and ids come from runs and transcripts only")
        self.accounts = load_accounts_cfg()
        self.runs_by_key = {}
        for r in sorted(self.runs, key=lambda r: iso_ts(r.get("created_at"))):
            for k in {r.get("key"), r.get("conversation_key")} - {None, ""}:
                self.runs_by_key.setdefault(k, []).append(r)
        self._tpath = {}

    def transcript(self, key):
        if key not in self._tpath:
            ix = self.index.get(key) or {}
            prov, uuid = split_key(key)
            path, host = None, ix.get("node") or "mac"
            for p in [ix.get("file")] + (ix.get("files_all") or []):
                if p and os.path.exists(p):
                    path = p
                    break
            if not path:
                path, host = find_transcript(prov, uuid)
            self._tpath[key] = (path, host)
        return self._tpath[key]

    def title(self, key):
        ix = self.index.get(key) or {}
        if ix.get("title"):
            return ix["title"]
        for s in self.live.get("sessions") or []:
            if s.get("chat_key") == key and s.get("name"):
                return s["name"]
        runs = self.runs_by_key.get(key) or []
        return short((runs[0].get("message") or "") if runs else "", 70)

    def live_for(self, key):
        _, uuid = split_key(key)
        for s in self.live.get("sessions") or []:
            if s.get("chat_key") == key or (uuid and s.get("session_id") == uuid):
                return s
        return None

    def account_of(self, key):
        runs = self.runs_by_key.get(key) or []
        for r in reversed(runs):
            if r.get("account_id"):
                return r["account_id"]
        return (self.index.get(key) or {}).get("account")

    def model_of(self, key):
        runs = self.runs_by_key.get(key) or []
        for r in reversed(runs):
            m = r.get("effective_model") or r.get("model")
            if m:
                return m
        return (self.index.get(key) or {}).get("model")

    def host_of(self, key):
        runs = self.runs_by_key.get(key) or []
        if runs and runs[-1].get("host_id"):
            return runs[-1]["host_id"]
        return self.transcript(key)[1]

    def cwd_of(self, key):
        runs = self.runs_by_key.get(key) or []
        if runs and runs[-1].get("cwd"):
            return runs[-1]["cwd"]
        live = self.live_for(key)
        return (live or {}).get("cwd") or (self.index.get(key) or {}).get("cwd") or ""

    def last_activity(self, key):
        path, _ = self.transcript(key)
        ts = [mtime(path)] if path else []
        ts += [mtime(f) for f in subagent_files(split_key(key)[0], path)]
        for r in self.runs_by_key.get(key) or []:
            ts.append(iso_ts(r.get("updated_at")))
        live = self.live_for(key)
        if live:
            ts += [live.get("last_act_ts") or 0, live.get("last_msg_ts") or 0]
        ts.append((self.index.get(key) or {}).get("ts_last") or 0)
        return max(ts) if ts else 0

    def alternative_account(self, key, exclude_limited=True):
        """Another logged-in account to continue on, same provider first."""
        prov = split_key(key)[0]
        cur = self.account_of(key)

        def usable(a):
            if a["id"] == cur or not a.get("logged_in", True):
                return False
            return not any((w.get("percent") or 0) >= 90 for w in a.get("windows") or [])
        same = [a for a in self.accounts if a["tool"] == prov and usable(a)]
        other = [a for a in self.accounts if a["tool"] != prov and usable(a)]
        pick = (same or other or [None])[0]
        return pick["id"] if pick else None

    def config_dir(self, account_id):
        for a in self.accounts:
            if a["id"] == account_id:
                return a.get("config_dir")
        return None


# ---------------------------------------------------------------- diagnosis

SEVERITY = ["auth_expired", "not_logged_in", "limit_reached", "rate_limited", "dead_process",
            "completed_but_running", "stale_running", "other_error"]


def fix_for(ctx, key, kind, resets_at, account):
    prov = split_key(key)[0]
    if kind in ("auth_expired", "not_logged_in"):
        cdir = ctx.config_dir(account)
        default = str(HOME / (".claude" if prov == "claude" else ".codex"))
        if prov == "claude":
            cmd = "claude auth login"
            env = "CLAUDE_CONFIG_DIR=%s " % cdir if cdir and cdir != default else ""
        else:
            cmd = "codex login"
            env = "CODEX_HOME=%s " % cdir if cdir and cdir != default else ""
        return "sign in: %s%s, then resend the failed message" % (env, cmd)
    if kind == "limit_reached":
        alt = ctx.alternative_account(key)
        when = "limit resets %s" % local_str(resets_at) if resets_at else "limit reset time unknown"
        return "%s; continue on %s" % (when, alt) if alt else when + "; no other account available"
    if kind == "rate_limited":
        return "transient overload; retry in a few minutes"
    if kind == "dead_process":
        return "provider process is gone; send a new message to resume or interrupt the run"
    if kind == "completed_but_running":
        return "the turn already finished; the run status is stale, interrupt it in the dashboard"
    if kind == "stale_running":
        return "no output for a long time; read `sessions tail %s`, then interrupt the run if nothing is moving" % short_key(key)
    return "read `sessions tail %s` for the error" % short_key(key)


def describe_failure(c):
    kind, resets = c["kind"], c["resets_at"]
    raw = short(c["raw"], 110)
    if kind == "limit_reached":
        m = re.search(r"hit your ([a-z0-9\- ]+?) limit", c["raw"], re.I)
        which = (m.group(1).lower() + " limit") if m else "usage limit"
        return "%s hit%s" % (which, ", resets %s" % local_str(resets) if resets else "")
    if kind == "auth_expired":
        return "sign-in expired: " + raw
    if kind == "not_logged_in":
        return "not logged in"
    return raw


def make_problem(ctx, key, run, kind, flags, reason, resets_at, since, cause=None, extra=None):
    account = (run or {}).get("account_id") or ctx.account_of(key)
    p = {
        "key": key, "provider": split_key(key)[0], "title": ctx.title(key), "cwd": ctx.cwd_of(key),
        "host": ctx.host_of(key), "account": account, "model": ctx.model_of(key),
        "run_id": (run or {}).get("id"), "kind": kind, "flags": flags, "reason": reason,
        "fix": fix_for(ctx, key, kind, resets_at, account), "resets_at": resets_at, "since": since,
    }
    if cause:
        p["error"] = short(cause["raw"], 300)
    if extra:
        p.update(extra)
    return p


def run_is_local(r):
    return (r.get("process_transport") in (None, "", "local")) and (r.get("host_id") in (None, "", "mac", "local"))


def diagnose_run(ctx, r, cutoff):
    """Problem record for one broker run, or None."""
    key = r.get("key") or r.get("conversation_key")
    if not key:
        return None
    prov = r.get("provider") or split_key(key)[0]
    status = r.get("status")
    created, updated = iso_ts(r.get("created_at")), iso_ts(r.get("updated_at"))
    if status != "running" and (status not in ("failed", "interrupted") or updated < cutoff):
        return None
    path, _ = ctx.transcript(key)
    tstate = transcript_state(prov, path) if path else {"state": None, "ts": 0, "text": "", "code": ""}
    last_write = max(updated, mtime(path) if path else 0)
    flags, cause = [], None

    err_text = r.get("error") or ""
    if r.get("failure") or err_text:
        cause = classify(err_text or (r.get("failure") or {}).get("raw", ""), ref_ts=updated or created, failure=r.get("failure"))
    elif tstate["state"] == "error" and tstate["ts"] >= created - 5:
        cause = classify(tstate["text"], tstate["code"], tstate["ts"])

    if status == "running":
        if run_is_local(r) and r.get("process_pid"):
            ps = process_state(r["process_pid"], r.get("process_identity"))
            if ps in ("dead", "zombie"):
                flags.append("dead_process" if ps == "dead" else "zombie_process")
        if tstate["state"] in ("complete", "aborted") and tstate["ts"] >= created - 5:
            flags.append("completed_but_running")
        if now() - last_write > STALE_RUNNING_S:
            flags.append("stale_running")
        if not flags and not cause:
            return None
        since = last_write
    else:
        if status not in ("failed", "interrupted") or updated < cutoff:
            return None
        if cause is None:
            return None            # interrupted by the user with no error is not a problem
        # resolved when a later run or later transcript turn went through
        for later in ctx.runs_by_key.get(key) or []:
            if iso_ts(later.get("created_at")) > created and later.get("status") in ("completed", "running"):
                return None
        if tstate["state"] in ("ok", "complete", "working") and tstate["ts"] > updated + 5:
            return None
        since = updated or created

    if cause and cause["kind"] != "other_error":
        kind = cause["kind"]
    elif "dead_process" in flags or "zombie_process" in flags:
        kind = "dead_process"
    elif "completed_but_running" in flags:
        kind = "completed_but_running"
    elif "stale_running" in flags:
        kind = "stale_running"
    else:
        kind = cause["kind"] if cause else "other_error"

    parts = []
    if cause:
        parts.append(describe_failure(cause))
    if status == "running":
        parts.append("run says running")
        if "dead_process" in flags:
            parts.append("process %s is gone" % r.get("process_pid"))
        if "zombie_process" in flags:
            parts.append("process %s is a zombie" % r.get("process_pid"))
        if "completed_but_running" in flags:
            parts.append("transcript shows the turn already ended")
        if "stale_running" in flags:
            parts.append("no write for %s" % age(last_write))
    else:
        parts.append("run %s" % status)
        gap = updated - mtime(path) if path else 0
        if gap > STALE_RUNNING_S:
            flags.append("was_stale_running")
            parts.append("the run stayed running %s after the transcript's last write" % age(now() - gap))
    return make_problem(ctx, key, r, kind, flags, "; ".join(parts), cause["resets_at"] if cause else None, since, cause,
                        {"run_status": status})


def transcript_files(cutoff):
    """(provider, host, path, subagent_of_key or None) for transcripts written since cutoff."""
    out = []
    roots = [("mac", CLAUDE_ROOT, "claude"), ("mac", CODEX_ROOT, "codex")]
    roots += [(h, p, "claude") for h, p in mirror_roots("claude").items()]
    roots += [(h, p, "codex") for h, p in mirror_roots("codex").items()]
    cut = datetime.fromtimestamp(cutoff)
    for host, root, prov in roots:
        if prov == "claude":
            try:
                projects = [d for d in os.scandir(root) if d.is_dir()]
            except OSError:
                continue
            for d in projects:
                try:
                    entries = list(os.scandir(d.path))
                except OSError:
                    continue
                for e in entries:
                    if e.name.endswith(".jsonl") and e.stat().st_mtime >= cutoff:
                        out.append((prov, host, e.path, None))
                    elif e.is_dir():
                        sub = os.path.join(e.path, "subagents")
                        if os.path.isdir(sub):
                            for f in os.scandir(sub):
                                if f.name.endswith(".jsonl") and f.stat().st_mtime >= cutoff:
                                    out.append((prov, host, f.path, "claude-" + e.name))
        else:
            day = datetime(cut.year, cut.month, cut.day)
            while day <= datetime.now():
                for f in glob.glob(str(root / ("%04d/%02d/%02d" % (day.year, day.month, day.day)) / "rollout-*.jsonl")):
                    if mtime(f) >= cutoff:
                        out.append((prov, host, f, None))
                day = datetime.fromtimestamp(day.timestamp() + 86400)
    return out


def codex_key_of(path):
    m = re.search(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
    return "codex-" + m.group(1) if m else None


def transcript_problem(ctx, key, prov, host, path, parent, cutoff):
    """Problem record when a transcript's latest outcome is an error, else None."""
    st = transcript_state(prov, path)
    if st["state"] != "error" or st["ts"] < cutoff:
        return None
    # a newer run that went through clears an older error on the same chat
    if any(iso_ts(r.get("created_at")) > st["ts"] and r.get("status") in ("completed", "running")
           for r in ctx.runs_by_key.get(key) or []):
        return None
    c = classify(st["text"], st["code"], st["ts"])
    sub = " (subagent %s)" % Path(path).stem.replace("agent-", "") if parent else ""
    known = key in ctx.index or key in ctx.runs_by_key or ctx.live_for(key) is not None
    return make_problem(ctx, key, None, c["kind"], [], describe_failure(c) + sub, c["resets_at"], st["ts"], c,
                        {"transcript": path, "host": host, "headless": not known,
                         "project_dir": Path(path).parent.name if prov == "claude" else ""})


def find_problems(ctx, hours=PROBLEM_HOURS, scan=True, only_keys=None):
    """Stuck or failed sessions. scan=False checks only the runs and only_keys (cheap path for `live`)."""
    cutoff = now() - hours * 3600
    found = {}
    for r in ctx.runs:
        if only_keys is not None and (r.get("key") not in only_keys):
            continue
        p = diagnose_run(ctx, r, cutoff)
        if p and (p["key"] not in found or p["since"] > found[p["key"]]["since"]):
            found[p["key"]] = p
    if scan:
        files = transcript_files(cutoff)
    else:
        files = []
        for key in only_keys or ():
            prov = split_key(key)[0]
            path, host = ctx.transcript(key)
            if path:
                files.append((prov, host, path, None))
    for prov, host, path, parent in files:
        key = parent or ("claude-" + Path(path).stem if prov == "claude" else codex_key_of(path))
        if not key or key in found:
            continue
        p = transcript_problem(ctx, key, prov, host, path, parent, cutoff)
        if p:
            found[key] = p
    return sorted(found.values(), key=lambda p: -p["since"])


# ---------------------------------------------------------------- command results

def status_of(ctx, key, problems_by_key):
    p = problems_by_key.get(key)
    if p:
        if p["kind"] in ("dead_process", "stale_running", "completed_but_running"):
            return "stuck", p
        return "failed", p
    return None, None


def live_rows(ctx, include_all=False):
    sessions = ctx.live.get("sessions") or []
    run_by_pid = {r.get("process_pid"): r for r in ctx.runs if r.get("status") == "running" and r.get("process_pid")}
    keys = {s.get("chat_key") for s in sessions if s.get("chat_key")}
    keys |= {r.get("key") for r in run_by_pid.values() if r.get("key")}
    keys |= {r.get("key") for r in ctx.runs if r.get("status") == "running" and r.get("key")}
    problems = {p["key"]: p for p in find_problems(ctx, 6, scan=False, only_keys=keys)}
    rows, seen, claimed_pids, skipped = [], set(), set(), []

    def add(key, prov, status, node, cwd, last, pid=None, name="", run=None, problem=None, process_only=False):
        row = {
            "status": status, "provider": prov, "model": ctx.model_of(key) if key else None,
            "account": ctx.account_of(key) if key else None, "title": (ctx.title(key) if key else "") or name,
            "key": key, "cwd": cwd, "host": node, "last_activity": last, "pid": pid,
            "run_id": (run or {}).get("id"), "reason": (problem or {}).get("reason", ""),
            "fix": (problem or {}).get("fix", ""), "process_only": process_only,
        }
        rows.append(row)

    for s in sessions:
        key = s.get("chat_key") or ""
        pid = s.get("pid")
        run = run_by_pid.get(pid) if not key else None
        if run:
            key = run.get("key") or ""
            claimed_pids.add(pid)
        if not key and s.get("process_only") and not include_all:
            skipped.append(pid)
            continue
        if key:
            seen.add(key)
        st = {"busy": "running", "idle": "idle"}.get(s.get("status"), "unknown")
        if run:
            st = "running"
        stuck, prob = status_of(ctx, key, problems) if key else (None, None)
        last = max(s.get("last_act_ts") or 0, s.get("last_msg_ts") or 0, iso_ts((run or {}).get("updated_at")))
        if key:
            last = max(last, ctx.last_activity(key))
        add(key or None, s.get("tool"), stuck or st, s.get("node") or s.get("host"), s.get("cwd"), last, pid,
            s.get("name") or "", run, prob, bool(s.get("process_only") and not key))
    for r in ctx.runs:
        key = r.get("key")
        if r.get("status") != "running" or not key or key in seen:
            continue
        seen.add(key)
        stuck, prob = status_of(ctx, key, problems)
        add(key, r.get("provider"), stuck or "running", r.get("host_id") or "mac", r.get("cwd"),
            max(iso_ts(r.get("updated_at")), ctx.last_activity(key)), r.get("process_pid"), "", r, prob)
    order = {"stuck": 0, "failed": 1, "running": 2, "idle": 3, "unknown": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 5), -(r["last_activity"] or 0)))
    return rows, len(skipped)


def recent_rows(ctx, hours):
    cutoff = now() - hours * 3600
    keys = {}

    def see(key, ts):
        key = key.split("-agent-")[0] if key else key
        if key and ts and ts >= cutoff:
            keys[key] = max(keys.get(key, 0), ts)
    for r in ctx.runs:
        see(r.get("key"), iso_ts(r.get("updated_at")))
    for s in ctx.live.get("sessions") or []:
        see(s.get("chat_key"), max(s.get("last_act_ts") or 0, s.get("last_msg_ts") or 0))
    for k, ix in ctx.index.items():
        see(k, ix.get("ts_last"))
    for prov, host, path, parent in transcript_files(cutoff):
        key = parent or ("claude-" + Path(path).stem if prov == "claude" else codex_key_of(path))
        see(key, mtime(path))
    problems = {p["key"]: p for p in find_problems(ctx, hours)}
    out = []
    for key, ts in keys.items():
        live = ctx.live_for(key)
        st, prob = status_of(ctx, key, problems)
        if not st:
            running = any(r.get("status") == "running" for r in ctx.runs_by_key.get(key) or [])
            st = "running" if running else {"busy": "running", "idle": "idle"}.get((live or {}).get("status"), "done")
        out.append({"status": st, "provider": split_key(key)[0], "model": ctx.model_of(key), "account": ctx.account_of(key),
                    "title": ctx.title(key), "key": key, "cwd": ctx.cwd_of(key), "host": ctx.host_of(key),
                    "last_activity": max(ts, ctx.last_activity(key)), "reason": (prob or {}).get("reason", "")})
    out.sort(key=lambda r: -r["last_activity"])
    return out


def resolve(ctx, query):
    """Full key for a full key, a bare uuid or any unique prefix of either."""
    q = query.strip().lower()
    pool = set(ctx.index) | set(ctx.runs_by_key) | {s.get("chat_key") for s in ctx.live.get("sessions") or [] if s.get("chat_key")}
    hits = sorted(k for k in pool if k.lower().startswith(q) or k.partition("-")[2].lower().startswith(q))
    if len(hits) > 1 and q in hits:
        hits = [q]
    if len(hits) > 1 and "agent" not in q:
        hits = [k for k in hits if "-agent-" not in k] or hits
    if not hits:
        for prov in ("claude", "codex"):
            u = q.split("-", 1)[1] if q.startswith(prov + "-") else q
            pat = (str(CLAUDE_ROOT / "*" / (u + "*.jsonl")) if prov == "claude"
                   else str(CODEX_ROOT / "*" / "*" / "*" / ("rollout-*-" + u + "*.jsonl")))
            for p in glob.glob(pat):
                k = ("claude-" + Path(p).stem) if prov == "claude" else codex_key_of(p)
                if k:
                    hits.append(k)
        hits = sorted(set(hits))
    if not hits:
        sys.exit("no session matches %r" % query)
    if len(hits) > 1:
        sys.exit("ambiguous %r, matches:\n  %s" % (query, "\n  ".join(hits[:12])))
    return hits[0]


def chat_record(ctx, key, turns=3):
    prov, uuid = split_key(key)
    path, host = ctx.transcript(key)
    subs = subagent_files(prov, path)
    runs = ctx.runs_by_key.get(key) or []
    problem = {p["key"]: p for p in find_problems(ctx, PROBLEM_HOURS)}.get(key)
    live = ctx.live_for(key)
    status = "idle"
    if problem:
        status = "stuck" if problem["kind"] in ("dead_process", "stale_running", "completed_but_running") else "failed"
    elif any(r.get("status") == "running" for r in runs) or (live or {}).get("status") == "busy":
        status = "running"
    elif not live:
        status = "done"
    rec = {
        "key": key, "provider": prov, "session_id": uuid, "title": ctx.title(key), "cwd": ctx.cwd_of(key),
        "host": host if host != "mac" else ctx.host_of(key), "account": ctx.account_of(key), "model": ctx.model_of(key),
        "status": status, "run_ids": [{"id": r.get("id"), "status": r.get("status")} for r in runs],
        "transcripts": [path] if path else [], "subagent_transcripts": len(subs),
        "last_activity": ctx.last_activity(key), "problem": problem,
        "turns": [], "live_pid": (live or {}).get("pid"),
    }
    if path and turns:
        rec["turns"] = [dict(t, text=short(t["text"], 400)) for t in read_turns(prov, path, turns)]
    return rec


def ref_line(ctx, key):
    prov, uuid = split_key(key)
    path, _ = ctx.transcript(key)
    clean = lambda s: re.sub(r"\s*\|\s*", " / ", re.sub(r"\s+", " ", s or "")).strip()
    parts = ["%s session %s" % (prov, uuid), short(clean(ctx.title(key)), 80) or "untitled", "cwd %s" % ctx.cwd_of(key) or "cwd ?"]
    if path:
        parts.append("transcript %s" % path)
    host = ctx.host_of(key)
    if host and host != "mac":
        parts.append("host %s" % host)
    if ctx.account_of(key):
        parts.append("account %s" % ctx.account_of(key))
    parts.append("status: sessions show %s" % short_key(key))
    return " | ".join(parts)


# ---------------------------------------------------------------- accounts

def cli_login_status(acc):
    """Ask the provider CLI itself. Returns {logged_in, expires_at} or None when it cannot be asked."""
    cdir = acc.get("config_dir")
    env = dict(os.environ)
    try:
        if acc["tool"] == "claude":
            exe = shutil.which("claude") or str(HOME / ".local" / "bin" / "claude")
            # The default profile must run without CLAUDE_CONFIG_DIR: setting it, even to
            # ~/.claude, makes the CLI look up a different Keychain item and report logged out.
            env.pop("CLAUDE_CONFIG_DIR", None)
            if cdir and os.path.realpath(os.path.expanduser(cdir)) != os.path.realpath(HOME / ".claude"):
                env["CLAUDE_CONFIG_DIR"] = cdir
            out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=8, env=env).stdout
            d = json.loads(out)
            return {"logged_in": bool(d.get("loggedIn")), "expires_at": d.get("loginExpiresAt")}
        exe = shutil.which("codex") or "/opt/homebrew/bin/codex"
        if cdir:
            env["CODEX_HOME"] = cdir
        r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=8, env=env)
        return {"logged_in": r.returncode == 0 and "logged in" in (r.stdout + r.stderr).lower(), "expires_at": None}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def account_rows():
    d = http_json("/api/accounts", 5.0)
    accounts = (d or {}).get("accounts")
    if not accounts:
        note("accounts come from the data/accounts.json cache, not the live server")
        accounts = load_accounts_cfg()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        cli = list(pool.map(cli_login_status, accounts))
    rows = []
    for a, c in zip(accounts, cli):
        api_in = a.get("logged_in")
        rows.append({
            "id": a["id"], "provider": a["tool"], "label": a.get("label"), "plan": a.get("plan"),
            "logged_in": c["logged_in"] if c else api_in, "logged_in_cli": c["logged_in"] if c else None,
            "logged_in_dashboard": api_in, "login_expires_at": (c or {}).get("expires_at"),
            "windows": [{"label": w.get("label") or w.get("kind"), "percent": w.get("percent"), "resets_at": w.get("resets_at")}
                        for w in a.get("windows") or []],
            "note": a.get("error") or "", "config_dir": a.get("config_dir"),
        })
    return rows


# ---------------------------------------------------------------- output

def table(rows, cols, out=None):
    """cols: [(header, getter, max_width)]. Left-aligned, two spaces between columns."""
    out = out or sys.stdout
    cells = [[short(str(g(r) if g(r) is not None else "-"), w) for _, g, w in cols] for r in rows]
    widths = [max([len(h)] + [len(c[i]) for c in cells]) for i, (h, _, _) in enumerate(cols)]
    lines = ["  ".join(h.ljust(widths[i]) for i, (h, _, _) in enumerate(cols)).rstrip()]
    for c in cells:
        lines.append("  ".join(v.ljust(widths[i]) for i, v in enumerate(c)).rstrip())
    out.write("\n".join(lines) + "\n")


def short_key(k):
    if not k:
        return "-"
    prov, _, uuid = k.partition("-")
    return "%s-%s" % (prov, uuid[:13] if prov == "codex" else uuid[:8])   # codex ids share their first 8 characters


def emit(args, payload, render):
    if args.json:
        obj = {"notes": NOTES, "generated_at": now()}
        obj.update(payload if isinstance(payload, dict) else {"items": payload})
        print(json.dumps(obj, indent=1, default=str))
        return
    for n in NOTES:
        print("note: " + n, file=sys.stderr)
    render()


def cmd_live(args):
    ctx = Context(live_age=LIVE_SNAPSHOT_MAX_S)
    rows, hidden = live_rows(ctx, args.all)
    for n in ctx.live.get("nodes") or []:
        if not n.get("ok") and n.get("name") != "mac":
            note("%s live sessions unavailable: %s" % (n.get("name"), n.get("error") or "not probed"))

    def render():
        if not rows:
            print("no live sessions")
        else:
            table(rows, [("STATUS", lambda r: r["status"], 8), ("PROV", lambda r: r["provider"], 6),
                         ("MODEL", lambda r: r["model"], 20), ("ACCOUNT", lambda r: r["account"], 15),
                         ("TITLE", lambda r: r["title"], 38), ("KEY", lambda r: short_key(r["key"]), 22),
                         ("CWD", lambda r: tilde(r["cwd"]), 34), ("HOST", lambda r: r["host"], 5),
                         ("AGE", lambda r: age(r["last_activity"]), 5),
                         ("NOTE", lambda r: r["reason"] if r["status"] in ("stuck", "failed") else "", 90)])
        if hidden > 0:
            print("+%d provider processes without a chat attribution (--all to list)" % hidden)
    emit(args, {"live": rows}, render)


def cmd_recent(args):
    ctx = Context()
    rows = recent_rows(ctx, args.hours)

    def render():
        if not rows:
            print("no chats active in the last %g hours" % args.hours)
            return
        table(rows, [("AGE", lambda r: age(r["last_activity"]), 5), ("STATUS", lambda r: r["status"], 8),
                     ("PROV", lambda r: r["provider"], 6), ("MODEL", lambda r: r["model"], 20),
                     ("ACCOUNT", lambda r: r["account"], 15), ("TITLE", lambda r: r["title"], 38),
                     ("KEY", lambda r: short_key(r["key"]), 22), ("CWD", lambda r: tilde(r["cwd"]), 34),
                     ("HOST", lambda r: r["host"], 5)])
    emit(args, {"hours": args.hours, "recent": rows}, render)


def group_headless(rows):
    """Collapse unindexed headless sessions (automation jobs run outside any chat) into one row per cause and folder."""
    out, groups = [], {}
    for p in rows:
        if not p.get("headless"):
            out.append(p)
            continue
        g = groups.setdefault((p["kind"], p.get("project_dir")), dict(p, count=0, keys=[]))
        g["count"] += 1
        g["keys"].append(p["key"])
        g["since"] = max(g["since"], p["since"])
    for g in groups.values():
        g["title"] = "%d headless sessions in %s" % (g["count"], g.get("project_dir") or "?")
        g["display_key"] = "-"
        out.append(g)
    return sorted(out, key=lambda p: -p["since"])


def cmd_problems(args):
    ctx = Context()
    rows = find_problems(ctx, PROBLEM_HOURS)
    shown = rows if args.json or args.expand else group_headless(rows)

    def render():
        if not shown:
            print("no stuck or failed sessions in the last %d hours" % PROBLEM_HOURS)
            return
        table(shown, [("AGE", lambda p: age(p["since"]), 5), ("KIND", lambda p: p["kind"], 21),
                      ("PROV", lambda p: p["provider"], 6), ("ACCOUNT", lambda p: p["account"], 15),
                      ("TITLE", lambda p: p["title"], 70), ("KEY", lambda p: p.get("display_key") or short_key(p["key"]), 22),
                      ("RUN", lambda p: (p.get("run_id") or "")[:8], 8)])
        print()
        for p in shown:
            print("%s  %s" % (p.get("display_key") or short_key(p["key"]), p["reason"]))
            print("    fix: %s" % p["fix"])
    emit(args, {"problems": rows}, render)


def cmd_show(args):
    ctx = Context()
    rec = chat_record(ctx, resolve(ctx, args.key))

    def render():
        rows = [("key", rec["key"]), ("provider", rec["provider"]), ("session id", rec["session_id"]),
                ("title", rec["title"] or "-"), ("cwd", rec["cwd"] or "-"), ("host", rec["host"]),
                ("account", rec["account"] or "-"), ("model", rec["model"] or "-"), ("status", rec["status"])]
        p = rec["problem"]
        if p:
            rows += [("problem", "%s: %s" % (p["kind"], p["reason"])), ("fix", p["fix"])]
        rows += [("runs", ", ".join("%s (%s)" % (r["id"], r["status"]) for r in rec["run_ids"]) or "none"),
                 ("transcript", "\n            ".join(rec["transcripts"]) or "not found"),
                 ("subagents", "%d transcripts" % rec["subagent_transcripts"]),
                 ("last activity", "%s ago (%s)" % (age(rec["last_activity"]), local_str(rec["last_activity"])))]
        for k, v in rows:
            print("%-13s %s" % (k, v))
        print("last turns")
        for t in rec["turns"]:
            print("  [%s %s%s] %s" % (t["role"], local_str(iso_ts(t["ts"])), " ERROR" if t["error"] else "", t["text"]))
    emit(args, rec, render)


def cmd_tail(args):
    ctx = Context()
    key = resolve(ctx, args.key)
    path, _ = ctx.transcript(key)
    if not path:
        sys.exit("no transcript found for %s" % key)
    turns = read_turns(split_key(key)[0], path, args.n)
    for t in turns:
        if len(t["text"]) > args.chars:
            t["text"] = t["text"][: args.chars] + "... [%d more chars]" % (len(t["text"]) - args.chars)

    def render():
        for t in turns:
            print("[%s %s%s]\n%s\n" % (t["role"], local_str(iso_ts(t["ts"])), " ERROR" if t["error"] else "", t["text"]))
    emit(args, {"key": key, "transcript": path, "turns": turns}, render)


def cmd_accounts(args):
    rows = account_rows()

    def usage(r):
        return "; ".join("%s %s%% resets %s" % (w["label"], w["percent"], local_str(iso_ts(w["resets_at"])))
                         for w in r["windows"] if w["percent"] is not None) or "unknown"

    def login(r):
        if r["logged_in"] is None:
            return "?"
        text = "yes" if r["logged_in"] else "NO"
        if r["logged_in_dashboard"] is not None and r["logged_in_cli"] is not None and r["logged_in_dashboard"] != r["logged_in_cli"]:
            text += " (dashboard says %s)" % ("yes" if r["logged_in_dashboard"] else "no")
        return text

    def render():
        table(rows, [("ID", lambda r: r["id"], 16), ("PROV", lambda r: r["provider"], 6), ("LABEL", lambda r: r["label"], 12),
                     ("LOGIN", login, 26),
                     ("EXPIRES", lambda r: local_str(iso_ts(r["login_expires_at"])) if r["login_expires_at"] else "-", 18),
                     ("USAGE", usage, 70), ("NOTE", lambda r: r["note"], 40)])
    emit(args, {"accounts": rows}, render)


def cmd_ref(args):
    ctx = Context()
    key = resolve(ctx, args.key)
    line = ref_line(ctx, key)
    emit(args, {"key": key, "ref": line}, lambda: print(line))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sessions", description="Read-only view of the chat dashboard's sessions.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.set_defaults(fn=fn)
        return p
    p = add("live", cmd_live, "live sessions on this machine and SC")
    p.add_argument("--all", action="store_true", help="include provider processes with no chat attribution")
    p = add("recent", cmd_recent, "chats active in the last N hours")
    p.add_argument("--hours", type=float, default=24)
    p = add("problems", cmd_problems, "stuck or failed sessions of the last 48 hours, with the fix")
    p.add_argument("--expand", action="store_true", help="list every headless session instead of one row per folder")
    p = add("show", cmd_show, "one session in detail")
    p.add_argument("key", help="chat key, uuid or any unique prefix")
    p = add("tail", cmd_tail, "last cleaned turns of a session")
    p.add_argument("key")
    p.add_argument("-n", type=int, default=6)
    p.add_argument("--chars", type=int, default=4000, help="trim each turn to this many characters")
    add("accounts", cmd_accounts, "accounts, login state and usage windows")
    p = add("ref", cmd_ref, "one line to paste into another agent's chat")
    p.add_argument("key")
    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except BrokenPipeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
