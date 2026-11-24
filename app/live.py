#!/usr/bin/env python3
"""Live sessions: every Claude and Codex chat running right now, on this Mac and
on each remote source (docs/V2-CONTRACTS.md, `GET /api/live`).

Mac Claude sessions come from ~/.claude/sessions/<pid>.json whose pid is alive and
still the same process (procStart matches). Mac Codex sessions come from `ps`.
Remote sessions come from `ssh <live_host> '<live_cmd>'` (for example a `chats --json` script).
The route answers from the in-memory cache (seeded from data/live.json); the
background thread refreshes the Mac list every `live_refresh_s` seconds. Remote
sources are probed only on an explicit POST /api/live/refresh: a probe may run
scheduler commands (squeue + srun on a cluster), and a timer loop of those can overload it.

  python3 live.py          refresh now and print the merged list as a table
  python3 live.py --json   refresh now and print the payload
"""
import calendar, ctypes, glob, json, math, os, secrets, signal, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
import json_cache

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HOME = os.path.expanduser("~")
LIVE_JSON = os.path.join(DATA, "live.json")


def _jload(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _index():
    """Shared parsed index, refreshed in the background; read-only."""
    return json_cache.latest(os.path.join(DATA, "index.json"), {})


def _jsave(path, obj):
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def _config():
    return _jload(os.path.join(DATA, "config.json"), {})


def _refresh_s():
    try:
        return int(os.environ.get("DASHBOARD_LIVE_REFRESH_S") or _config().get("live_refresh_s", 60))
    except ValueError:
        return 60


def sh(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# ------------------------------------------------------------- transcripts
def _ts(s):
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return None


def _tail_lines(path):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 3_000_000))
        return fh.read().decode("utf-8", "replace").split("\n")  # JSON strings may hold raw U+2028


def last_prompt(session_id, cwd):
    """(epoch, text, mtime) of the last message I typed, from the transcript tail.
    Same rules as the remote `chats` script."""
    slug = "".join(c if c.isalnum() else "-" for c in (cwd or ""))
    paths = glob.glob(f"{HOME}/.claude/projects/{slug}/{session_id}.jsonl") or \
        glob.glob(f"{HOME}/.claude/projects/*/{session_id}.jsonl")
    if not paths:
        return None, "", None
    path = paths[0]
    mtime = os.path.getmtime(path)
    try:
        lines = _tail_lines(path)
    except OSError:
        return None, "", mtime
    for line in reversed(lines):
        if '"type":"user"' not in line and '"queued_command"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isMeta") or d.get("isSidechain"):
            continue
        c = d.get("message", {}).get("content")
        a = d.get("attachment") if d.get("type") == "attachment" else None
        if isinstance(a, dict):  # a message sent while the turn ran
            if a.get("type") != "queued_command" or a.get("commandMode", "prompt") != "prompt":
                continue
            c = a.get("prompt")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        c = (c or "").strip()
        if not c or c.startswith(("<", "[Request interrupted", "SYSTEM NOTICE", "[slurm")):
            continue
        return _ts(d.get("timestamp", "")), " ".join(c.split()), mtime
    return None, "", mtime


def codex_last_prompt(path):
    mtime = os.path.getmtime(path)
    try:
        lines = _tail_lines(path)
    except OSError:
        return None, "", mtime
    for line in reversed(lines):
        if '"user_message"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("payload", {})
        c = " ".join((p.get("message") or "").split()) if p.get("type") == "user_message" else ""
        if c:
            return _ts(d.get("timestamp", "")), c, mtime
    return None, "", mtime


def codex_rollouts(limit=300):
    """[(mtime, cwd, session_id, originator, path)] newest first, subagent threads skipped."""
    out = []
    files = sorted(glob.glob(f"{HOME}/.codex/sessions/*/*/*/rollout-*.jsonl"),
                   key=os.path.getmtime, reverse=True)[:limit]
    for p in files:
        try:
            with open(p) as fh:
                d = json.loads(fh.readline()).get("payload", {})
        except (OSError, ValueError):
            continue
        if isinstance(d.get("source"), dict):
            continue
        out.append((os.path.getmtime(p), d.get("cwd"), d.get("id") or d.get("session_id"),
                    d.get("originator") or "", p))
    return out


# ------------------------------------------------------------- Mac processes
def proc_table():
    """{pid: (ppid, comm, args)} for every process on the Mac."""
    comm, procs = {}, {}
    for line in sh(["ps", "-axww", "-o", "pid=,comm="]).splitlines():
        f = line.strip().split(None, 1)
        if len(f) == 2:
            comm[int(f[0])] = f[1]
    for line in sh(["ps", "-axww", "-o", "pid=,ppid=,args="]).splitlines():
        f = line.strip().split(None, 2)
        if len(f) >= 2:
            pid = int(f[0])
            procs[pid] = (int(f[1]), comm.get(pid, ""), f[2] if len(f) > 2 else "")
    return procs


def start_times():
    out = {}
    for line in sh(["ps", "-axo", "pid=,lstart="]).splitlines():
        f = line.strip().split(None, 1)
        if len(f) == 2:
            out[int(f[0])] = " ".join(f[1].split())
    return out


def _same_start(proc_start, lstart):
    """procStart in the session file is UTC; ps lstart is local time."""
    if not proc_start or not lstart:
        return True
    try:
        fmt = "%a %b %d %H:%M:%S %Y"
        utc = calendar.timegm(time.strptime(" ".join(proc_start.split()), fmt))
        local = time.mktime(time.strptime(lstart, fmt))
    except ValueError:
        return True
    return abs(utc - local) <= 2


def tmux_state():
    """({pane_pid: session}, {session: attached})"""
    panes, attached = {}, {}
    for line in sh(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"]).splitlines():
        f = line.split(" ", 1)
        if len(f) == 2 and f[0].isdigit():
            panes[int(f[0])] = f[1]
    for line in sh(["tmux", "list-sessions", "-F", "#{session_attached} #{session_name}"]).splitlines():
        f = line.split(" ", 1)
        if len(f) == 2:
            attached[f[1]] = f[0] != "0"
    return panes, attached


def classify(pid, procs, panes):
    """(where, tmux_session) from the process and its parent chain."""
    chain, seen, p = [], set(), pid
    while p and p not in seen and p in procs:
        seen.add(p)
        if p in panes:
            return "tmux", panes[p]
        chain.append(procs[p])
        p = procs[p][0]
    text = " ".join(c + " " + a for _, c, a in chain)
    if "Claude.app" in text or "/Application Support/Claude/" in text or "ChatGPT.app" in text:
        return "desktop", None
    if ".vscode" in text or "Code Helper" in text or "Visual Studio Code.app" in text:
        return "vscode", None
    if not chain:
        return "other", None
    return "terminal", None


def mac_sessions():
    procs, starts = proc_table(), start_times()
    panes, attached = tmux_state()
    rows, unclassified = [], []
    for path in glob.glob(f"{HOME}/.claude/sessions/*.json"):
        d = _jload(path, None)
        if not isinstance(d, dict) or not d.get("pid") or not d.get("sessionId"):
            continue
        pid = int(d["pid"])
        dom = d.get("pidDomain", "")
        if dom.count(":") >= 2 and dom.split(":")[0] != "darwin":
            continue                                  # recorded on another machine
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        if pid not in procs or os.path.basename(procs[pid][1]) != "claude":
            continue                                  # pid reused by something else
        if not _same_start(d.get("procStart"), starts.get(pid)):
            continue                                  # pid reused by a newer process
        where, tm = classify(pid, procs, panes)
        if where in ("other", "terminal") and d.get("entrypoint") == "claude-desktop":
            where = "desktop"
        if where == "other":
            unclassified.append(pid)
        when, text, mtime = last_prompt(d["sessionId"], d.get("cwd"))
        upd = (d.get("updatedAt") or 0) / 1000 or None
        rows.append(dict(
            id=f"mac:{pid}", tool="claude", node="mac", host="mac", job="", where=where,
            name=tm or d.get("name") or os.path.basename(d.get("cwd") or "") or "claude",
            status=d.get("status") or "?", attached=attached.get(tm) if tm else None,
            remote_control=f"https://claude.ai/code/{d['bridgeSessionId']}" if d.get("bridgeSessionId") else "",
            cwd=d.get("cwd") or "", session_id=d["sessionId"], pid=pid,
            last_msg_ts=when, last_act_ts=max(filter(None, (mtime, upd)), default=None),
            last_text=text[:500], alive=True,
            attach_cmd=f"tmux attach -t {tm}" if tm else "", restart_cmd=""))
    # Codex: top-level codex processes (not helpers spawned by another codex).
    rollouts = None
    for pid, (ppid, comm, args) in procs.items():
        if os.path.basename(comm) != "codex" or os.path.basename(procs.get(ppid, (0, ""))[1]) == "codex":
            continue
        if rollouts is None:
            rollouts = codex_rollouts()
        where, tm = classify(pid, procs, panes)
        cwd = ""
        for line in sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines():
            if line.startswith("n"):
                cwd = line[1:]
        app_server = "app-server" in args
        want = "codex_vscode" if where == "vscode" else ("Codex Desktop" if where == "desktop" else "")
        match = next((r for r in rollouts if cwd and cwd != "/" and r[1] == cwd), None)
        if not match and app_server and want:
            match = next((r for r in rollouts if r[3] == want), None)
        # A shared app server cannot be assigned to the newest transcript in
        # its cwd. That fabricated both a conversation identity and idle age.
        if app_server:
            match = None
        sid, rollout = (match[2], match[4]) if match else ("", None)
        when, text, mtime = codex_last_prompt(rollout) if rollout else (None, "", None)
        if not match:
            unclassified.append(pid)
        label = {"vscode": "Codex service · VS Code", "desktop": "Codex service · Desktop"}.get(where, "Codex process")
        rows.append(dict(
            id=f"mac:{pid}", tool="codex", node="mac", host="mac", job="", where=where,
            name=tm or label, status="?", attached=attached.get(tm) if tm else None,
            remote_control="", cwd=cwd or "",
            session_id="", pid=pid, last_msg_ts=None, last_act_ts=None,
            last_text="", alive=True, process_only=True,
            activity_note="Shared app service; conversation activity is not attributable" if app_server else "Conversation activity is not verified for this process",
            attach_cmd=f"tmux attach -t {tm}" if tm else "", restart_cmd=""))
    observed_at = time.time()
    owned = _broker_processes()
    for row in rows:
        row['observed_at'] = observed_at
        row['identity'] = process_identity(row['pid']) if row['tool'] == 'claude' or row['pid'] in owned else None
        row['stop_allowed'] = False
        run = owned.get(row['pid'])
        if run and row['identity'] and all(row['identity'].get(k) == (run.get('process_identity') or {}).get(k)
                                           for k in ('pid', 'start_sec', 'start_usec')):
            # Started by the Library broker: Stop interrupts the run through the broker.
            row.update(broker_run_id=run['id'], run_key=run.get('key') or run.get('conversation_key') or '',
                       stop_allowed=True, stop_reason='')
            continue
        try:
            _validate_stop_session(row)
            row['stop_allowed'] = True
            row['stop_reason'] = ''
        except ValueError as exc:
            row['stop_reason'] = str(exc)
    return rows, unclassified


# ------------------------------------------------------------- remote
def remote_sessions(src):
    name = src.get("name") or src.get("live_host") or "remote"
    host, cmd = src.get("live_host") or src.get("host"), src.get("live_cmd") or "timeout -s KILL 30 ~/.local/bin/chats --json"
    node = {"name": name, "ok": False, "error": "", "jobs": []}
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, cmd],
                           capture_output=True, text=True, timeout=40)
        if r.returncode != 0:
            raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1][:300])
        d = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        node["error"] = "ssh timed out after 40 s"
        return node, None
    except Exception as e:
        node["error"] = f"{type(e).__name__}: {e}"[:300]
        return node, None
    sessions = []
    for s in d.get("sessions") or []:
        s = dict(s)
        s["node"] = name
        s['observed_at'] = time.time()
        s['stop_allowed'] = False
        s['stop_reason'] = f'Listed by the {name} status script without a verified process; stop it on {name}.'
        sid = s.get("id") or ""
        s["id"] = f"{name}:{sid.split(':', 1)[1]}" if ":" in sid else f"{name}:{s.get('host')}:{s.get('name')}"
        sessions.append(s)
    node.update(ok=True, jobs=d.get("jobs") or [])
    return node, sessions


# ------------------------------------------------------------- cache
_LOCK = threading.Lock()
_CACHE = None
_LAST_REMOTE = {}          # source name -> (fetched_at, sessions) of the last good fetch


def refresh(remote=True):
    """Collect everything, update the cache and data/live.json, return the payload.
    remote=False keeps the last remote lists instead of probing them."""
    global _CACHE
    if not _LOCK.acquire(blocking=False):
        with _LOCK:                       # a refresh is in flight: wait for it, reuse it
            return _CACHE
    try:
        t0 = time.time()
        cfg = _config()
        srcs = [s for s in (cfg.get("remote_sources") or []) if s.get("live_host") or s.get("live_cmd")]
        if not remote:
            srcs = []
        with ThreadPoolExecutor(max_workers=1 + len(srcs)) as ex:
            mac_f = ex.submit(mac_sessions)
            rem_f = [ex.submit(remote_sessions, s) for s in srcs]
            mac_rows, unclassified = mac_f.result()
            remote = [f.result() for f in rem_f]
        if not remote:
            for s in cfg.get("remote_sources") or []:
                if not (s.get("live_host") or s.get("live_cmd")):
                    continue
                name = s.get("name") or s.get("live_host") or "remote"
                remote.append(({"name": name, "ok": name in _LAST_REMOTE, "jobs": [],
                                "error": "probed on refresh only"}, None))
        now = time.time()
        nodes = [{"name": "mac", "ok": True, "unclassified_pids": unclassified}]
        sessions = list(mac_rows)
        for node, rows in remote:
            if rows is None and node["name"] in _LAST_REMOTE:
                t, rows = _LAST_REMOTE[node["name"]]
                node["error"] += f" (showing the list from {time.strftime('%H:%M', time.localtime(t))})"
                node["stale_since"] = t
            elif rows is not None:
                _LAST_REMOTE[node["name"]] = (now, rows)
            nodes.append(node)
            sessions.extend(rows or [])
        index = _index()
        for s in sessions:
            key = f"{s.get('tool')}-{s.get('session_id')}" if s.get("session_id") else ""
            s["chat_key"] = key if key in index else ""
        sessions.sort(key=lambda s: (bool(s.get("alive")), s.get("last_msg_ts") or s.get("last_act_ts") or 0),
                      reverse=True)
        payload = {"fetched_at": int(now), "refresh_s": _refresh_s(), "seconds": round(time.time() - t0, 2),
                   "nodes": nodes, "sessions": sessions}
        payload['idle_review'] = idle_review(payload, now=now)
        _CACHE = payload
        try:
            _jsave(LIVE_JSON, payload)
        except OSError:
            pass
        return payload
    finally:
        _LOCK.release()


def _with_host_sessions(payload):
    """Merge explicit host probe caches only; reading this never contacts a host."""
    sessions = [dict(row) for row in payload.get('sessions', [])]
    nodes = [dict(node) for node in payload.get('nodes', [])]
    try:
        import hosts
        records = hosts.host_records()
    except ImportError:
        records = []
    for host in records:
        if host.get('transport') == 'local' or not host.get('sessions'):
            continue
        if not any(node.get('name') == host['id'] for node in nodes):
            nodes.append({'name': host['id'], 'ok': host.get('state') == 'online',
                          'error': host.get('error') or '',
                          'stale_since': host.get('observed_at') if host.get('state') != 'online' else None})
        for observed in host['sessions']:
            existing = next((row for row in sessions if row.get('node') == host['id'] and row.get('pid') == observed.get('pid')), None)
            if existing is None:
                sessions.append(dict(observed))
            elif observed.get('observed_at', 0) > existing.get('observed_at', 0):
                existing.update(observed)
    index = _index()
    for row in sessions:
        key = row.get('tool', '') + '-' + row.get('session_id', '')
        if not row.get('chat_key') and key in index:
            row['chat_key'] = key
    now = time.time()
    for row in sessions:
        row['live_status'] = session_status(row, now)
        row.update(index_state(row))
    result = dict(payload, sessions=sessions, nodes=nodes)
    result['idle_review'] = idle_review(result)
    return result


def session_status(row, now=None):
    """waiting | working | idle | None, from the verified process row only (fresh, alive,
    attributed to one conversation). Never from transcript recency."""
    now = time.time() if now is None else now
    if row.get('alive') is not True or row.get('process_only') or not row.get('session_id') or not _fresh(row, now):
        return None
    return {'waiting': 'waiting', 'busy': 'working', 'running': 'working', 'working': 'working',
            'idle': 'idle'}.get(row.get('status'))


def _claude_transcript(session_id):
    roots = [os.path.join(HOME, '.claude', 'projects')] + glob.glob(os.path.join(HOME, '.claude-profiles', '*', 'projects'))
    return any(glob.glob(os.path.join(root, '*', session_id + '.jsonl')) for root in roots)


def index_state(row):
    """Why a live session has no Library chat. A Desktop session that never received a
    message has no transcript file at all, so there is nothing to index (research-77,
    2026-11-22); a transcript that exists is indexed by the live refresh job."""
    if row.get('chat_key') or row.get('process_only') or not row.get('session_id'):
        return {}
    if row.get('tool') == 'claude' and (row.get('node') or 'mac') == 'mac' and not _claude_transcript(row['session_id']):
        return {'index_state': 'no_transcript', 'index_note': 'No messages yet, so there is no transcript to index.'}
    return {'index_state': 'not_indexed', 'index_note': 'Transcript found; it is indexed on the next refresh.'}


def get_live(req, query, body):
    if _CACHE is not None:
        return _with_host_sessions(_CACHE)
    cached = _jload(LIVE_JSON, None)
    if cached:
        return _with_host_sessions(cached)
    return {"fetched_at": None, "refresh_s": _refresh_s(), "nodes": [], "sessions": []}


def post_refresh(req, query, body):
    return refresh()


ROUTES = {("GET", "/api/live"): get_live, ("POST", "/api/live/refresh"): post_refresh}
BACKGROUND = [(lambda: refresh(remote=False), _refresh_s())]


# ------------------------------------------------------------- safe local session controls
FRESH_OBSERVATION_S = 150
_PREVIEWS = {}
_PREVIEW_LOCK = threading.Lock()


class _ProcBsdInfo(ctypes.Structure):
    # sys/proc_info.h PROC_PIDTBSDINFO on Darwin, read-only kernel identity.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid', 'svuid', 'svgid', 'reserved')] + [
        ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')] + [
        ('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


def process_identity(pid):
    """Native microsecond process identity; no weak ps fallback for stopping."""
    if sys.platform != 'darwin' or not isinstance(pid, int) or pid <= 1:
        return None
    try:
        lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        info = _ProcBsdInfo()
        size = ctypes.sizeof(info)
        if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size or info.pid != pid:
            return None
        path = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(pid, path, len(path)) <= 0:
            return None
        boot = sh(['sysctl', '-n', 'kern.bootsessionuuid'], timeout=2).strip()
        if not boot:
            return None
        return dict(pid=pid, uid=info.uid, start_sec=info.start_sec, start_usec=info.start_usec,
                    executable=path.value.decode('utf-8'), boot_id=boot)
    except (OSError, ValueError, AttributeError):
        return None


def _fresh(row, now):
    observed = row.get('observed_at')
    return isinstance(observed, (int, float)) and 0 <= now - observed <= FRESH_OBSERVATION_S


def idle_review(payload, now=None):
    """Fresh, explicitly idle sessions only. Last user prompt never implies idle."""
    now = time.time() if now is None else now
    return [dict(row, idle_seconds=int(now - row['last_act_ts'])) for row in payload.get('sessions', [])
            if row.get('alive') is True and row.get('status') == 'idle' and _fresh(row, now)
            and isinstance(row.get('last_act_ts'), (int, float)) and now - row['last_act_ts'] > 86400]


def _claude_executable(path):
    if not isinstance(path, str) or not os.path.isabs(path):
        return False
    # Native Claude installations name the executable after its version, while
    # ps displays the CLI's process title as claude. Accept that exact install
    # directory as well as an executable actually named claude.
    versions = os.path.join(HOME, '.local', 'share', 'claude', 'versions')
    return os.path.basename(path) == 'claude' or (os.path.dirname(path) == versions and
            bool(__import__('re').fullmatch(r'[0-9]+(?:\.[0-9]+)+(?:[-.][A-Za-z0-9]+)*', os.path.basename(path))))


def _broker_processes():
    """pid -> run record for broker-owned provider processes still marked running.

    Reads the run journals only; never constructs the broker (that would mark runs
    of another process interrupted)."""
    owned = {}
    for path in glob.glob(os.path.join(DATA, 'execution', 'runs', '*', 'run.json')):
        run = _jload(path, {})
        if not isinstance(run, dict) or run.get('status') in ('completed', 'failed', 'interrupted') and not run.get('recovery_blocked'):
            continue  # a blocked recovery is a broker-owned process that outlived a server restart
        if isinstance(run.get('process_pid'), int) and run.get('process_transport', 'local') == 'local':
            owned[run['process_pid']] = run
    return owned


# When Stop is offered (2026-11-15): broker-owned runs always (through the broker);
# a Claude CLI on this Mac (terminal, tmux, VS Code, desktop app) when its pid,
# start time, session file and workspace all match; a remote session only through a
# connector that re-verifies the process. Codex processes outside the broker serve
# several conversations and are never stopped from here.
def _stop_candidate(row):
    if row.get('broker_run_id'):
        if row.get('alive') is not True or not _fresh(row, time.time()):
            raise ValueError('This list is out of date; refresh, then stop.')
        return
    if row.get('node') != 'mac':
        if row.get('alive') is not True or row.get('tool') != 'claude' or not row.get('stop_allowed') or not row.get('identity') or not row.get('execution_identity') or not _fresh(row, time.time()):
            raise ValueError('This remote session must be re-checked on its host before it can be stopped; refresh and try again.')
        return
    if row.get('tool') != 'claude':
        raise ValueError('This Codex process may serve several conversations, so Stop is off here; stop it in its own app.')
    if row.get('alive') is not True:
        raise ValueError('This session is no longer running.')
    if not _fresh(row, time.time()):
        raise ValueError('This list is out of date; refresh, then stop.')
    identity = row.get('identity')
    if not isinstance(identity, dict) or any(identity.get(k) is None for k in ('pid', 'uid', 'start_sec', 'start_usec', 'executable', 'boot_id')):
        raise ValueError('Its process could not be verified, so Stop is off.')
    if identity['uid'] != os.getuid() or identity['pid'] != row.get('pid') or identity['pid'] <= 1:
        raise ValueError('Its process belongs to another user or has changed, so Stop is off.')
    if not identity['boot_id'] or not identity['start_sec'] or not _claude_executable(identity['executable']) or not row.get('session_id'):
        raise ValueError('Its process could not be matched to one conversation, so Stop is off.')


def _validate_stop_session(row, policy=None):
    _stop_candidate(row)
    if row.get('broker_run_id'):
        if process_identity(row['pid']) != row['identity']:
            raise ValueError('Its process changed; refresh the list.')
        return
    if row.get('node') != 'mac':
        return  # Connector repeats native validation immediately before SIGTERM.
    if process_identity(row['pid']) != row['identity']:
        raise ValueError('Process identity changed; refresh the session list')
    record = _jload(os.path.join(HOME, '.claude', 'sessions', str(row['pid']) + '.json'), {})
    if policy is not None and policy['basis'] == 'idle':
        last_activity = max(row.get('last_act_ts') or 0, (record.get('updatedAt') or 0) / 1000)
        if record.get('status') != 'idle' or not last_activity or time.time() - last_activity <= policy['hours'] * 3600:
            raise ValueError('Agent activity changed before automatic stopping')
    if record.get('sessionId') != row['session_id'] or record.get('pid') != row['pid']:
        raise ValueError('Session changed')
    if not row.get('cwd') or os.path.realpath(record.get('cwd') or '') != os.path.realpath(row['cwd']):
        raise ValueError('Session workspace does not match')
    try:
        start = calendar.timegm(time.strptime(' '.join(record['procStart'].split()), '%a %b %d %H:%M:%S %Y'))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Session process start identity is missing')
    if start != row['identity']['start_sec']:
        raise ValueError('Session file belongs to a different process')


def _current_stop_session(session_id):
    if isinstance(session_id, str) and not session_id.startswith('mac:'):
        import hosts
        host = hosts.probe_host(session_id.split(':', 1)[0])
        if host.get('state') != 'online':
            raise ValueError(host.get('error') or 'Remote host unavailable')
        rows = host.get('sessions') or []
    else:
        rows, _ = mac_sessions()
    matches = [row for row in rows if row.get('id') == session_id]
    if len(matches) != 1:
        raise ValueError('Session is gone or ambiguous')
    row = matches[0]
    _stop_candidate(row)
    return row


def stop_preview(session_id):
    row = _current_stop_session(session_id)
    _validate_stop_session(row)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _PREVIEW_LOCK:
        for expired in [k for k, v in _PREVIEWS.items() if v['expires_at'] < now]:
            _PREVIEWS.pop(expired, None)
        if len(_PREVIEWS) >= 100:
            raise ValueError('Too many stop previews; wait for existing previews to expire')
        _PREVIEWS[token] = {'row': row, 'expires_at': now + 60}
    return {'token': token, 'expires_at': now + 60, 'session': {k: row.get(k) for k in ('id', 'name', 'pid', 'tool', 'cwd', 'session_id', 'host')},
            'scope': 'Send SIGTERM to this agent process only. Its transcript, tmux session and allocation remain. No force kill or child-process cleanup.'}


def stop_commit(token, source='manual', policy=None):
    if not isinstance(token, str):
        raise ValueError('A stop preview is required')
    with _PREVIEW_LOCK:
        preview = _PREVIEWS.pop(token, None)
    if not preview or preview['expires_at'] < time.time():
        raise ValueError('Stop preview expired or was already used')
    before = preview['row']
    current = _current_stop_session(before['id'])
    if any(current.get(k) != before.get(k) for k in ('identity', 'session_id', 'cwd', 'tool', 'execution_identity')):
        raise ValueError('Session identity changed after preview')
    _validate_stop_session(current, policy=policy)
    if policy is not None:
        since = current.get('started_at') or (current.get('identity') or {}).get('start_sec') if policy['basis'] == 'age' else current.get('last_act_ts')
        if stop_policy() != policy or (policy['basis'] == 'idle' and current.get('status') != 'idle') or not isinstance(since, (int, float)) or time.time() - since <= policy['hours'] * 3600:
            raise ValueError('Automatic stop rule or session activity changed')
    if current.get('broker_run_id'):
        if policy is not None:
            raise ValueError('Automatic stopping never interrupts a Library run.')
        import broker
        broker.instance().control(current['broker_run_id'], {'action': 'interrupt'})
        result = {'ok': True, 'state': 'stop_requested', 'id': current['id'], 'run_id': current['broker_run_id']}
        _record_outcome(current, result['state'], source)
        return result
    if current.get('node') != 'mac':
        import hosts
        result = hosts.session_control(current['node'], 'stop', current, policy=policy)
        _record_outcome(current, result['state'], source)
        return result
    # Recheck the kernel identity immediately before signaling. Never signal a
    # process group, tmux server, Slurm job, arbitrary caller-supplied PID or child.
    if process_identity(current['pid']) != current['identity']:
        raise ValueError('Process changed immediately before stopping')
    os.kill(current['pid'], signal.SIGTERM)
    result = {'ok': True, 'state': 'stop_requested', 'id': current['id']}
    _record_outcome(current, result['state'], source)
    return result


def _control_route(fn, req, body, field):
    # Browser requests carry Origin; reject cross-site access to localhost.
    origin = req.headers.get('Origin', '') if req is not None else ''
    host = req.headers.get('Host', '') if req is not None else ''
    if origin and origin not in ('http://' + host, 'https://' + host):
        return {'error': 'Cross-origin control request rejected'}, 403
    try:
        return fn((body or {}).get(field))
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {'error': str(exc)}, 409


def post_stop_preview(req, query, body):
    return _control_route(stop_preview, req, body, 'id')


def post_stop_commit(req, query, body):
    return _control_route(stop_commit, req, body, 'token')


ROUTES.update({('POST', '/api/live/stop/preview'): post_stop_preview,
               ('POST', '/api/live/stop/commit'): post_stop_commit})


def _idle_skip(row):
    """Reason this row is not stopped by Stop all idle, or '' when it is a candidate."""
    if row.get('status') != 'idle' or row.get('alive') is not True:
        return 'not idle (%s)' % (row.get('status') or 'unknown')
    if row.get('broker_run_id'):
        return 'owned by a Library run; stop it from its chat'
    if row.get('process_only') or row.get('tool') != 'claude' or not row.get('session_id'):
        return 'shared or unattributed process'
    if not row.get('stop_allowed'):
        return row.get('stop_reason') or 'stop not allowed'
    return ''


def stop_idle_sessions(source='manual-all-idle', sessions=None, verify=None, signal_process=None, status_of=None):
    """Stop every Mac session whose fresh status is idle and whose stop is allowed. Each
    process identity and its session file are re-verified immediately before SIGTERM, and
    the status is read again from the session file. Busy, waiting, shared, unattributed,
    Library-run and ambiguous (duplicate id) rows are skipped. Returns per-session outcomes.
    Arguments after source are test hooks (fake processes)."""
    rows = mac_sessions()[0] if sessions is None else sessions
    verify = verify or _validate_stop_session
    signal_process = signal_process or (lambda row: os.kill(row['pid'], signal.SIGTERM))
    status_of = status_of or (lambda row: _jload(os.path.join(HOME, '.claude', 'sessions', str(row['pid']) + '.json'), {}).get('status'))
    counts = {}
    for row in rows:
        counts[row.get('id')] = counts.get(row.get('id'), 0) + 1
    outcomes = []
    for row in rows:
        out = {'id': row.get('id'), 'name': row.get('name'), 'pid': row.get('pid'), 'chat_key': row.get('chat_key', '')}
        reason = 'ambiguous: several processes share this id' if counts[row.get('id')] > 1 else _idle_skip(row)
        if reason:
            outcomes.append(dict(out, state='skipped', reason=reason))
            continue
        try:
            verify(row)
            if status_of(row) != 'idle':
                raise ValueError('Agent activity changed before stopping')
            signal_process(row)
        except (ValueError, OSError) as exc:
            outcomes.append(dict(out, state='refused', reason=str(exc)))
            continue
        _record_outcome(row, 'stop_requested', source)
        outcomes.append(dict(out, state='stop_requested', reason=''))
    return {'ok': True, 'stopped': sum(o['state'] == 'stop_requested' for o in outcomes), 'outcomes': outcomes}


def post_stop_idle(req, query, body):
    origin = req.headers.get('Origin', '') if req is not None else ''
    host = req.headers.get('Host', '') if req is not None else ''
    if origin and origin not in ('http://' + host, 'https://' + host):
        return {'error': 'Cross-origin control request rejected'}, 403
    return stop_idle_sessions()


ROUTES[('POST', '/api/live/stop_idle')] = post_stop_idle


# ------------------------------------------------------------- optional stop policy
POLICY_JSON = os.path.join(DATA, 'live-policy.json')
OUTCOMES_JSON = os.path.join(DATA, 'live-stop-outcomes.json')
_POLICY_LOCK = threading.RLock()
_AUTO_LOCK = threading.Lock()
DEFAULT_POLICY = {'enabled': False, 'basis': 'age', 'hours': 24}


def _policy(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('enabled must be true or false')
    if value.get('basis') not in ('age', 'idle'):
        raise ValueError('basis must be age or idle')
    hours = value.get('hours')
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or not 1 <= hours <= 8760:
        raise ValueError('hours must be between 1 and 8760')
    return {key: value[key] for key in DEFAULT_POLICY}


def stop_policy():
    try:
        return _policy(_jload(POLICY_JSON, DEFAULT_POLICY))
    except ValueError:
        return dict(DEFAULT_POLICY)


def _record_outcome(row, state, source, error=''):
    with _POLICY_LOCK:
        records = _jload(OUTCOMES_JSON, [])
        records.append({'id': row.get('id'), 'name': row.get('name'), 'identity': row.get('identity'), 'session_id': row.get('session_id'), 'node': row.get('node'),
                        'execution_identity': row.get('execution_identity'), 'pid': row.get('pid'),
                        'state': state, 'source': source, 'at': time.time(), 'error': error})
        _jsave(OUTCOMES_JSON, records[-100:])


def stop_outcomes(wait=True):
    # Settings must not wait behind an automatic stop in progress (it holds the
    # lock through a fresh process scan or an SSH call): show the saved records.
    if not _POLICY_LOCK.acquire(blocking=wait):
        return [{k: v for k, v in row.items() if k not in ('identity', 'execution_identity')} for row in _jload(OUTCOMES_JSON, [])]
    try:
        return _stop_outcomes()
    finally:
        _POLICY_LOCK.release()


def _stop_outcomes():
    with _POLICY_LOCK:  # reentrant
        records = _jload(OUTCOMES_JSON, [])
        changed = False
        for row in records:
            identity = row.get('identity')
            if row.get('state') == 'stop_requested' and identity:
                if row.get('node') not in (None, 'mac'):
                    try:
                        import hosts
                        result = hosts.session_control(row['node'], 'stop-status', row)
                        if result['state'] == 'stopped':
                            row['state'] = 'stopped'
                            changed = True
                    except (ValueError, OSError, subprocess.TimeoutExpired):
                        pass  # An unreachable host does not establish process exit.
                    continue
                current = process_identity(identity['pid'])
                # Missing native identity can also mean inspection failed. Only
                # mark stopped when the PID is gone, or positively reused.
                gone = current is not None and current != identity
                if current is None:
                    try:
                        os.kill(identity['pid'], 0)
                    except ProcessLookupError:
                        gone = True
                    except OSError:
                        pass
                if gone:
                    row['state'] = 'stopped'
                    changed = True
        if changed:
            _jsave(OUTCOMES_JSON, records)
        return [{k: v for k, v in row.items() if k not in ('identity', 'execution_identity')} for row in records]


def get_policy(req, query, body):
    return dict(stop_policy(), outcomes=stop_outcomes(wait=False), scope='Verified Claude processes on this Mac and connected Linux hosts with process controls. Age rules include busy sessions; idle rules require idle status. Shared or ambiguous Codex processes and unverified hosts are excluded.')


def post_policy(req, query, body):
    try:
        value = _policy(body)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    with _POLICY_LOCK:
        _jsave(POLICY_JSON, value)
    return get_policy(req, query, body)


def enforce_stop_policy():
    if not _AUTO_LOCK.acquire(blocking=False):
        return
    try:
        policy = stop_policy()
        if not policy['enabled']:
            return
        rows, _ = mac_sessions()  # Fresh native observations, never cached inventory.
        try:
            import hosts
            for host in hosts.host_records():
                if host.get('transport') == 'ssh' and host.get('configured') and 'stop' in host.get('capabilities', []):
                    observed = hosts.probe_host(host['id'])
                    if observed.get('state') == 'online':
                        rows.extend(observed.get('sessions') or [])
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
        now = time.time()
        previous = _jload(OUTCOMES_JSON, [])
        for row in rows:
            identity = row.get('identity') or {}
            since = (row.get('started_at') or identity.get('start_sec')) if policy['basis'] == 'age' else row.get('last_act_ts')
            if not isinstance(since, (int, float)) or now - since <= policy['hours'] * 3600:
                continue
            if not row.get('stop_allowed') or row.get('broker_run_id') or (policy['basis'] == 'idle' and row.get('status') != 'idle'):
                continue
            if any(item.get('identity') == identity and now - item.get('at', 0) < 3600 for item in previous):
                continue
            try:
                # Re-read the session, validate native process identity, and use
                # the same single-use preview protocol as an explicit Stop.
                preview = stop_preview(row['id'])
                if stop_policy() != policy:  # A changed/disabled rule wins.
                    with _PREVIEW_LOCK:
                        _PREVIEWS.pop(preview['token'], None)
                    break
                current = _current_stop_session(row['id'])
                threshold = (current.get('started_at') or (current.get('identity') or {}).get('start_sec')) if policy['basis'] == 'age' else current.get('last_act_ts')
                if (policy['basis'] == 'idle' and current.get('status') != 'idle') or not isinstance(threshold, (int, float)) or time.time() - threshold <= policy['hours'] * 3600:
                    with _PREVIEW_LOCK:
                        _PREVIEWS.pop(preview['token'], None)
                    continue
                with _POLICY_LOCK:
                    stop_commit(preview['token'], source='automatic', policy=policy)
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                _record_outcome(row, 'error', 'automatic', str(exc))
    finally:
        _AUTO_LOCK.release()


ROUTES.update({('GET', '/api/live/policy'): get_policy, ('POST', '/api/live/policy'): post_policy})
BACKGROUND.append((enforce_stop_policy, 60))


# ------------------------------------------------------------- CLI
def _ago(t):
    if not t:
        return "-"
    s = int(time.time() - t)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d{s % 86400 // 3600:02d}h"


def main():
    p = refresh()
    if "--json" in sys.argv:
        json.dump(p, sys.stdout, indent=1)
        print()
        return
    for n in p["nodes"]:
        extra = f"  jobs: {', '.join(j['name'] + '@' + j['node'] for j in n.get('jobs', []))}" if n.get("jobs") else ""
        err = f"  error: {n['error']}" if n.get("error") else ""
        unc = f"  unclassified pids: {n['unclassified_pids']}" if n.get("unclassified_pids") else ""
        print(f"node {n['name']}: {'ok' if n['ok'] else 'FAILED'}{extra}{err}{unc}")
    print(f"refresh took {p['seconds']} s, {len(p['sessions'])} sessions\n")
    print(f"{'LAST MSG':>8} {'ACTIVE':>7}  {'NODE':<4} {'HOST':<7} {'TOOL':<6} {'WHERE':<8} {'STATUS':<6} "
          f"{'NAME':<24} {'CHAT':<5} LAST THING I SAID")
    for s in p["sessions"]:
        print(f"{_ago(s.get('last_msg_ts')):>8} {_ago(s.get('last_act_ts')):>7}  {s['node']:<4} "
              f"{(s.get('host') or '-'):<7} {s['tool']:<6} {s['where']:<8} {s['status']:<6} "
              f"{s['name'][:24]:<24} {'yes' if s.get('chat_key') else '-':<5} {(s.get('last_text') or '')[:60]}")


if __name__ == "__main__":
    main()
