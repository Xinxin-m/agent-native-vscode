#!/usr/bin/env python3
"""
remote_sync.py -- pull recent Claude Code and Codex transcripts from remote hosts.

For each `remote_sources` entry in data/config.json:

  ssh HOST 'cd ~/.claude/projects && find . -maxdepth 4 -name "*.jsonl" -newermt "-N days" -printf "%P\\n"'
      | rsync -a --files-from=- HOST:.claude/projects/  data/remote/<name>/claude-projects/
  ssh HOST 'cd ~/.codex/sessions && find . -name "*.jsonl" -newermt "-N days" -printf "%P\\n"'
      | rsync -a --files-from=- HOST:.codex/sessions/   data/remote/<name>/codex-sessions/
  rsync -a HOST:.codex/session_index.jsonl data/remote/<name>/codex-session_index.jsonl

Only find and rsync run on the remote (a cluster login node is small). Nothing is
ever deleted on either side. When any file changed, pipeline.py (incremental)
runs afterwards so the index picks the new transcripts up.

Writes data/remote/<name>/sync.json {last_ok, last_error, files, seconds}.
Contract: docs/V2-CONTRACTS.md, section remote_sync.py.

    python3 remote_sync.py      # one sync now, prints the summary
"""
import json, os, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
# rsync always runs with -W (whole file): macOS /usr/bin/rsync is openrsync
# (protocol 29), and its delta transfer against the cluster's rsync 3.2.7 dies with
# "unexpected end of file" on some files (verified 2026-10-21 on a 23 MB jsonl).
LIST_TIMEOUT = 300
RSYNC_TIMEOUT = 1800
PIPELINE_TIMEOUT = 3600

_lock = threading.Lock()


def _jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def _config():
    return _jload(os.path.join(DATA, "config.json"), {})


def _remote_rel(path):
    """'~/.claude/projects' -> '.claude/projects' (rsync paths are relative to the remote home)."""
    path = path.strip()
    if path.startswith("~/"):
        path = path[2:]
    return path.rstrip("/")


def _pull_tree(host, remote_dir, dest, days, maxdepth=None):
    """List *.jsonl under remote_dir modified in the last `days` days and copy the
    ones whose (size, mtime) differ locally into dest, keeping the relative
    layout. rsync first; whatever is still stale after an rsync failure is
    streamed with `tar cf -` over ssh. Returns the number of files listed."""
    rel = _remote_rel(remote_dir)
    depth = f"-maxdepth {int(maxdepth)} " if maxdepth else ""
    remote_cmd = (f'cd ~/{rel} 2>/dev/null || exit 0; find . {depth}-type f -name "*.jsonl" '
                  f'-newermt "-{int(days)} days" -printf "%s %T@ %P\\n"')
    listing = subprocess.run(["ssh", *SSH_OPTS, host, remote_cmd], capture_output=True,
                             text=True, timeout=LIST_TIMEOUT)
    if listing.returncode != 0:
        raise RuntimeError(f"ssh find {rel}: exit {listing.returncode}: {listing.stderr.strip()[-300:]}")
    remote = {}
    for line in listing.stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[2]:
            remote[parts[2]] = (int(parts[0]), int(float(parts[1])))
    if not remote:
        return 0
    os.makedirs(dest, exist_ok=True)

    def stale():
        out = []
        for path, (size, mtime) in remote.items():
            try:
                st = os.stat(os.path.join(dest, path))
                if st.st_size == size and int(st.st_mtime) == mtime:
                    continue
            except OSError:
                pass
            out.append(path)
        return out

    need = stale()
    if not need:
        return len(remote)
    rs = subprocess.run(["rsync", "-a", "-W", "--files-from=-", "-e", "ssh " + " ".join(SSH_OPTS),
                         f"{host}:{rel}/", dest.rstrip("/") + "/"],
                        input="\n".join(need) + "\n", capture_output=True, text=True,
                        timeout=RSYNC_TIMEOUT)
    if rs.returncode != 0:
        need = stale()
        if need:
            _tar_pull(host, rel, dest, need)
        need = stale()
        if need:
            raise RuntimeError(f"{rel}: {len(need)} files still stale after rsync "
                               f"(exit {rs.returncode}) and tar fallback")
    return len(remote)


def _tar_pull(host, rel, dest, paths):
    """Stream paths (relative to ~/rel) as one tar over ssh into dest. Names get a
    './' prefix because Claude project slugs start with '-'."""
    ssh = subprocess.Popen(["ssh", *SSH_OPTS, host, f"cd ~/{rel} && tar cf - --null -T -"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tar = subprocess.Popen(["tar", "xf", "-", "-C", dest], stdin=ssh.stdout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ssh.stdout.close()
    ssh.stdin.write(b"".join(b"./" + p.encode() + b"\0" for p in paths))
    ssh.stdin.close()
    try:
        tar.wait(timeout=RSYNC_TIMEOUT)
        ssh.wait(timeout=60)
    except subprocess.TimeoutExpired:
        ssh.kill()
        tar.kill()
        raise RuntimeError(f"tar {rel}: timed out")


def _pull_file(host, remote_path, dest):
    rel = _remote_rel(remote_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    rs = subprocess.run(["rsync", "-a", "-W", "-e", "ssh " + " ".join(SSH_OPTS), f"{host}:{rel}", dest],
                        capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if rs.returncode != 0:
        if "No such file" in rs.stderr:
            return 0
        raise RuntimeError(f"rsync {rel}: exit {rs.returncode}: {rs.stderr.strip()[-300:]}")
    return 1


def _snapshot(base):
    """{path: (mtime, size)} of everything under base, to detect changes."""
    out = {}
    for dirpath, _, names in os.walk(base):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
                out[p] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return out


def sync_source(src):
    name = src.get("name") or src.get("host")
    host = src["host"]
    days = int(src.get("days", 30))
    base = os.path.join(DATA, "remote", name)
    state_path = os.path.join(base, "sync.json")
    state = _jload(state_path, {})
    t0 = time.time()
    before = _snapshot(base)
    try:
        n = _pull_tree(host, src.get("claude_projects", "~/.claude/projects"),
                       os.path.join(base, "claude-projects"), days, maxdepth=4)
        n += _pull_tree(host, src.get("codex_sessions", "~/.codex/sessions"),
                        os.path.join(base, "codex-sessions"), days)
        codex_root = os.path.dirname(_remote_rel(src.get("codex_sessions", "~/.codex/sessions")))
        n += _pull_file(host, f"{codex_root}/session_index.jsonl",
                        os.path.join(base, "codex-session_index.jsonl"))
        state.update(last_ok=int(time.time()), last_error="", files=n,
                     seconds=round(time.time() - t0, 1))
    except Exception as e:
        state.update(last_error=f"{int(time.time())}: {type(e).__name__}: {e}",
                     seconds=round(time.time() - t0, 1))
        state.setdefault("last_ok", None)
        state.setdefault("files", 0)
    after = _snapshot(base)
    after.pop(state_path, None)
    before.pop(state_path, None)
    state["changed"] = sum(1 for p, v in after.items() if before.get(p) != v)
    state["name"] = name
    state["host"] = host
    _jsave(state_path, state)
    return state


def _run_pipeline():
    """Import synced history deterministically; background sync never bills
    provider jobs. --wait queues behind a refresh already in flight."""
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline.py"),
                            "--reason", "remote-sync", "--wait", "--quiet",
                            "--parse-only", "--keep-missing"],
                           cwd=ROOT, capture_output=True, text=True, timeout=PIPELINE_TIMEOUT)
        return r.returncode == 0
    except Exception:
        return False


def sync(run_pipeline=True):
    """Sync every remote source; run the pipeline when anything changed."""
    if not _lock.acquire(blocking=False):
        return {"busy": True, "sources": status()["sources"]}
    try:
        results = [sync_source(s) for s in (_config().get("remote_sources") or [])]
        changed = sum(r.get("changed", 0) for r in results)
        pipeline = None
        if run_pipeline and changed:
            pipeline = _run_pipeline()
        return {"busy": False, "changed": changed, "pipeline": pipeline, "sources": results}
    finally:
        _lock.release()


def status():
    out = []
    for s in _config().get("remote_sources") or []:
        name = s.get("name") or s.get("host")
        st = _jload(os.path.join(DATA, "remote", name, "sync.json"), {})
        out.append({"name": name, "host": s.get("host"), "last_ok": st.get("last_ok"),
                    "last_error": st.get("last_error", ""), "files": st.get("files", 0),
                    "seconds": st.get("seconds"), "changed": st.get("changed", 0)})
    return {"running": _lock.locked(), "sources": out}


def _get(req, query, body):
    return status()


def _post_run(req, query, body):
    if _lock.locked():
        return {"started": False, "running": True, **status()}
    threading.Thread(target=sync, daemon=True).start()
    return {"started": True, **status()}


ROUTES = {("GET", "/api/remote_sync"): _get,
          ("POST", "/api/remote_sync/run"): _post_run}
BACKGROUND = [(sync, int(_config().get("remote_sync_s", 600)))]


if __name__ == "__main__":
    skip = "--no-pipeline" in sys.argv
    res = sync(run_pipeline=not skip)
    for s in res.get("sources", []):
        print(f"{s['name']} ({s['host']}): files={s.get('files')} changed={s.get('changed')} "
              f"seconds={s.get('seconds')} last_ok={s.get('last_ok')} "
              f"error={s.get('last_error') or '-'}")
    print(f"pipeline: {'skipped' if skip else res.get('pipeline')}")
