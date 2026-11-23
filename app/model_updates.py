"""POST /api/models/refresh: CLI versions, optional CLI self-update, fresh model lists.

Body {"install": false} (default) only checks: current versions, the newest published
versions (npm registry, no model call) and the models each CLI has cached.
{"install": true} also runs `claude update` and `codex update` for a CLI that is behind,
unless the broker has an active run. {"known": {"claude": [ids], "codex": [ids]}} names
the models the client already shows, so new_models is what it has not seen.
The model tiers file is never written; tiers_stale reports a newer Claude it lacks."""
import datetime
import json
import os
import re
import subprocess
import sys

import clis

PACKAGES = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
UPDATE = {"claude": ["update"], "codex": ["update"]}  # verified with `<cli> update --help`
VERSION_TIMEOUT, LATEST_TIMEOUT, UPDATE_TIMEOUT = 15, 30, 300
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _run(argv, timeout):
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, env=clis.env())
        return done.returncode, (done.stdout + done.stderr).strip()
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout} s"
    except OSError as exc:
        return None, str(exc)


def _version(text):
    m = VERSION.search(text or "")
    return ".".join(m.groups()) if m else ""


def _key(version):
    return tuple(int(x) for x in version.split(".")) if version else ()


def cli_version(tool):
    return _version(_run([clis.cli(tool), "--version"], VERSION_TIMEOUT)[1])


def claude_channel():
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as fh:
            return json.load(fh).get("autoUpdatesChannel") or "latest"
    except Exception:
        return "latest"


def latest_version(tool):
    tag = claude_channel() if tool == "claude" else "latest"
    code, out = _run([clis.cli("npm"), "view", f"{PACKAGES[tool]}@{tag}", "version"], LATEST_TIMEOUT)
    return _version(out) if code == 0 else ""


def active_runs():
    broker = sys.modules.get("broker")
    if broker is None:
        return []
    return [r for r in broker.instance().store.list() if r.get("status") not in broker.TERMINAL]


def _newest(ids):
    out = {}
    for i in ids:
        m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d+))?$", i)
        if m and (m[1] not in out or (int(m[2]), int(m[3] or 0)) > out[m[1]][0]):
            out[m[1]] = ((int(m[2]), int(m[3] or 0)), i)
    return out


def tiers_stale(models, tier_ids):
    """Families where a listed Claude version is newer than the tiers file names."""
    listed, tiers = _newest(m["id"] for m in models), _newest(tier_ids)
    notes = [f"{listed[f][1]} is newer than {tiers[f][1]} in the tiers file"
             for f in listed if f in tiers and listed[f][0] > tiers[f][0]]
    return "; ".join(notes)


def refresh(server, body):
    body = body if isinstance(body, dict) else {}
    known = body.get("known") if isinstance(body.get("known"), dict) else {}
    lists = {"claude": server.claude_models, "codex": server.codex_models}
    report = {}
    for tool in ("claude", "codex"):
        ids = known.get(tool)
        before = [m["id"] for m in lists[tool]()] if not isinstance(ids, list) else ids
        current, latest = cli_version(tool), latest_version(tool)
        report[tool] = {"version_before": current, "version_after": current, "latest": latest,
                        "update_available": bool(current and latest and _key(latest) > _key(current)),
                        "_before": before}
    out = {"ok": True}
    if body.get("install") is True:
        try:
            busy = active_runs()
        except Exception as exc:
            busy = None
            out["install_skipped"] = f"Could not read broker runs ({type(exc).__name__}); nothing installed."
        if busy:
            out["install_skipped"] = f"{len(busy)} active run(s) in the broker; nothing installed. Try again when they finish."
        elif busy is not None:
            for tool, item in report.items():
                if not item["update_available"]:
                    continue
                code, text = _run([clis.cli(tool)] + UPDATE[tool], UPDATE_TIMEOUT)
                item["install"] = {"ok": code == 0, "output": text[-2000:]}
                item["version_after"] = cli_version(tool) or item["version_before"]
    for tool, item in report.items():
        models = lists[tool]()
        before = set(item.pop("_before"))
        item["models"] = models
        item["new_models"] = [m for m in models if m["id"] and m["id"] not in before]
    stale = tiers_stale(report["claude"]["models"], server.claude_tier_models())
    if stale:
        out["tiers_stale"] = stale
    out.update(report, checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    return out


def _route(req, query, body):
    # The core server module, also when server.py runs as __main__ (see projects._route).
    return refresh(sys.modules.get(req.__class__.__module__), body)


ROUTES = {("POST", "/api/models/refresh"): _route}
BACKGROUND = []
