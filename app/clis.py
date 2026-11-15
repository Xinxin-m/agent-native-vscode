#!/usr/bin/env python3
"""
clis.py — find the `claude` and `codex` executables, wherever they were installed.

Why this exists: a launchd or systemd service starts with a minimal PATH
(/usr/bin:/bin:/usr/sbin:/sbin). The CLIs usually live somewhere else --
~/.local/bin, /opt/homebrew/bin, a node prefix -- so a service-started refresh
raises FileNotFoundError('claude') even though the same command works in your
shell. Every module that shells out to a CLI resolves it through here, and
passes env() so the CLI's own children can find their toolchain too.

Override in data/config.json when a CLI lives somewhere unusual:

    {"claude_bin": "/opt/whatever/bin/claude", "codex_bin": "codex"}
"""
import json
import os
import shutil

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))

# Ordered by how likely an install is to be there, most specific first.
EXTRA_DIRS = [
    os.path.join(HOME, ".local", "bin"),
    os.path.join(HOME, ".bun", "bin"),
    os.path.join(HOME, ".volta", "bin"),
    os.path.join(HOME, ".npm-global", "bin"),
    os.path.join(HOME, "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
]


def _config():
    try:
        with open(os.path.join(HERE, "data", "config.json")) as fh:
            return json.load(fh)
    except Exception:
        return {}


def search_path():
    """PATH plus the usual install locations, without duplicates."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for extra in EXTRA_DIRS:
        if extra not in parts and os.path.isdir(extra):
            parts.append(extra)
    return os.pathsep.join(parts)


def env(**extra):
    """Environment for a CLI subprocess: widened PATH, plus anything given."""
    return dict(os.environ, PATH=search_path(), **extra)


def cli(name, cfg=None):
    """Absolute path to `name`, or the bare name if it cannot be found (so the
    caller's error mentions the command rather than a path we invented)."""
    cfg = _config() if cfg is None else cfg
    override = cfg.get(f"{name}_bin")
    if override:
        override = os.path.expanduser(str(override))
        return override if (os.sep in override) else (shutil.which(override) or override)
    found = shutil.which(name, path=search_path())
    return found or name


def missing(names=("claude", "codex")):
    """Which of these CLIs cannot be found anywhere we know to look."""
    return [n for n in names if cli(n) == n and not shutil.which(n, path=search_path())]


if __name__ == "__main__":
    for name in ("claude", "codex"):
        print(f"{name}: {cli(name)}")
    print("PATH searched:", search_path())
