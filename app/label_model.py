"""
label_model.py -- the one place a labelling prompt goes to a model.

Tier: the "mechanical" tier of models.json (path in config `models_file`, default
~/.claude/evolve/data/models.json), falling back to config models.bulk. Claude is
tried first; when it reports a usage limit or a sign-in problem the same prompt
goes to Codex's mechanical model. When every provider is unavailable the circuit
breaker (data/enrich_backoff.json) pauses all labelling for BACKOFF_S, so a
session hook cannot spawn hundreds of failing CLI processes.
"""
import json
import os
import re
import subprocess
import tempfile
import time

import clis

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BACKOFF_FILE = os.path.join(DATA, "enrich_backoff.json")
BACKOFF_S = 1800
UNAVAILABLE = re.compile(
    r"hit your [a-z0-9 -]*limit|usage limit|rate limit|quota|Failed to authenticate|"
    r"Not logged in|not signed in|log ?in again|please log ?in|login reached its time limit|"
    r"unauthorized|401", re.I)


class Unavailable(Exception):
    """Every provider is out of quota or signed out."""


class CallFailed(Exception):
    """One call failed for a reason a later run may not repeat (timeout, empty answer)."""


def models(cfg):
    path = os.path.expanduser(cfg.get("models_file") or "~/.claude/evolve/data/models.json")
    try:
        with open(path) as fh:
            tier = json.load(fh)["tiers"]["mechanical"]
    except (OSError, ValueError, KeyError, TypeError):
        tier = {}
    return {"claude": tier.get("claude") or (cfg.get("models") or {}).get("bulk") or "claude-haiku-4-5",
            "codex": tier.get("codex") or "gpt-6-luna"}


def _state():
    try:
        with open(BACKOFF_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(state):
    tmp = BACKOFF_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, BACKOFF_FILE)


def paused():
    return _state().get("until", 0) > time.time()


def _down(provider):
    return _state().get(provider + "_until", 0) > time.time()


def _trip(provider, reason, providers):
    state = _state()
    state[provider + "_until"] = time.time() + BACKOFF_S
    state["reason"] = reason
    if all(state.get(p + "_until", 0) > time.time() for p in providers):
        state["until"] = min(state[p + "_until"] for p in providers)
    _save(state)


def _claude(prompt, model, timeout, cfg):
    # --strict-mcp-config with no --mcp-config: labelling is pure text, so the
    # user's MCP servers are not started in the subprocess.
    return subprocess.run(
        [clis.cli("claude", cfg), "-p", "--model", model, "--no-session-persistence", "--strict-mcp-config"],
        input=prompt, capture_output=True, text=True, timeout=timeout,
        cwd=tempfile.gettempdir(), env=clis.env(DASHBOARD_REFRESH_ACTIVE="1"))


def _codex(prompt, model, timeout, cfg):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "last.txt")
        r = subprocess.run(
            [clis.cli("codex", cfg), "exec", "-m", model, "--skip-git-repo-check", "--ephemeral",
             "--ignore-user-config", "-s", "read-only", "--color", "never", "-C", tmp, "-o", out, "-"],
            input=prompt, capture_output=True, text=True, timeout=timeout,
            cwd=tmp, env=clis.env(DASHBOARD_REFRESH_ACTIVE="1"))
        try:
            with open(out) as fh:
                answer = fh.read()
        except OSError:
            answer = ""
        r.stdout = answer or ""
        return r


CALLERS = {"claude": _claude, "codex": _codex}


def ask(prompt, cfg, timeout=420):
    """Return (text, provider). Raises Unavailable or CallFailed."""
    wanted = [p for p in (cfg.get("label_providers") or ["claude", "codex"]) if p in CALLERS]
    if paused():
        raise Unavailable("paused until the labelling backoff expires")
    names = models(cfg)
    last = ""
    for provider in wanted:
        if _down(provider):
            continue
        try:
            r = CALLERS[provider](prompt, names[provider], timeout, cfg)
        except subprocess.TimeoutExpired:
            raise CallFailed("timeout")
        except FileNotFoundError:
            _trip(provider, provider + " not installed", wanted)
            continue
        text = (r.stdout or "").strip()
        noise = text + "\n" + (r.stderr or "")
        hit = UNAVAILABLE.search(noise)
        if hit and (r.returncode != 0 or not text.lstrip().startswith(("[", "{", "`"))):
            last = hit.group(0)
            _trip(provider, last, wanted)
            continue
        if not text:
            raise CallFailed((r.stderr or "empty answer")[:200])
        return text, provider
    raise Unavailable(last or "no provider available")
