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
  POST /api/rescan            re-run incremental indexer, enrich, and project grouping
  POST /api/send              {key, message, model?, effort?} resume via claude/codex CLI
  POST /api/open              {path} open file in editor
"""
import json, mimetypes, os, re, shutil, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clis  # noqa: E402  (the CLIs are invisible to a service's minimal PATH)
from automation import automation_kind, effective_projects

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
CLAUDE_MODELS = [
    {"id": "claude-opus-5-5", "name": "Opus 5.5"},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5"},
    {"id": "claude-fable-5-2", "name": "Fable 5.2"},
    {"id": "claude-haiku-5-5", "name": "Haiku 5.5"},
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
        "claude": {"models": CLAUDE_MODELS, "efforts": CLAUDE_EFFORTS},
        "codex": {"models": codex_models(), "efforts": CODEX_EFFORTS},
    }


def default_flags():
    return {"archived": [], "starred": []}


def config_exclusions(cfg):
    values = cfg.get("excluded_cwds", []) if isinstance(cfg, dict) else []
    return [value for value in values if isinstance(value, str) and value.strip()] if isinstance(values, list) else []


def data_revision():
    """A cheap token for the browser to learn that library data changed."""
    newest = 0
    for name in ("index", "enrich", "projects", "flags", "views", "config"):
        try:
            newest = max(newest, os.stat(FILES[name]).st_mtime_ns)
        except OSError:
            pass
    return str(newest)


# ---------------------------------------------------------------- actions
def do_rescan():
    """Bring the library up to date through the shared pipeline: same stages,
    order, and lock as the session hooks, but uncapped -- an explicit Rescan
    should finish the whole backlog rather than a hook-sized slice. Progress
    goes to data/refresh.log."""
    with SCAN_LOCK:
        subprocess.run([sys.executable, os.path.join(HERE, "pipeline.py"),
                        "--reason", "rescan", "--uncapped", "--wait", "--quiet"],
                       cwd=HERE, capture_output=True, timeout=7200)


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
                                  **{field: m[field] for field in ('account_id', 'execution_host', 'host_id', 'conversation_key', 'source_key', 'last_run_id', 'parent_key', 'root_key', 'parent_agent_id', 'agent_id', 'agent_label', 'agent_path', 'is_child', 'lineage_source', 'spawned_children', 'source_locations', 'account_attribution') if field in m},
                                  "usage": m.get("usage", {}),
                                  "title": e.get("title") or m.get("title_seed")
                                           or (m.get("first_prompt") or "")[:60] or k,
                                  "description": e.get("description", ""),
                                  "keywords": e.get("keywords", []),
                                  "topic_hint": e.get("topic_hint", ""),
                                  "is_ops": e.get("is_ops", False),
                                  "automation": bool(automation_kind(m)),
                                  "automation_kind": automation_kind(m),
                                  "enriched": "title" in e,
                                  "status": m.get("status", "complete"),
                                  "last_role": m.get("last_role", ""),
                                  "transcript_revision": json.dumps(m.get("src_sig", [])),
                                  "attention_required": bool(m.get("attention_required", False)),
                                  "archived": k in archived or bool(automation_kind(m)),
                                  "starred": k in starred})
                project_state = effective_projects(index, jload("projects", default_projects()))
                from session_lineage import effective_assignments
                project_state["assignments"] = effective_assignments(index, project_state["assignments"])
                return self._json({
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
                    "revision": data_revision(),
                })
        if u.path == "/api/revision":
            return self._json({"revision": data_revision()})
        if u.path == "/api/chat":
            key = (q.get("key") or [""])[0]
            path = os.path.join(CHATS, os.path.basename(key) + ".json")
            if not os.path.isfile(path):
                return self._json({"error": "not found"}, 404)
            return self._json(json.load(open(path)))
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
                flags = {"archived": sorted(archived), "starred": sorted(starred)}
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
                jsave("config", cfg)
            return self._json({"ok": True})

        if p == "/api/rescan":
            threading.Thread(target=do_rescan, daemon=True).start()
            return self._json({"ok": True, "note": "rescan started"})

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
PLUGIN_MODULES = ("live", "accounts", "stream", "usage_history", "remote_sync", "projects", "hosts", "broker", "session_lineage", "commands", "file_service", "preview_server", "search_api", "open_destinations", "conversation_settings", "settings_insights")
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
    cfg = jload("config", {})
    cfg = cfg if isinstance(cfg, dict) else {}
    port = int(cfg.get("port", 8744))
    load_plugins()
    run_background()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"chat dashboard: http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
