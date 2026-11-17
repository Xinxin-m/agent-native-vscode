"""Parsed data files for request handlers, refreshed off the request path.

index.json is rewritten every few seconds while any chat is active, so a cache
keyed only on the file's stat would still parse 20+ MB on most clicks.
latest() returns the newest parsed copy at once and re-reads a changed file in a
background thread; only the first read of a file waits. Values are shared
between callers: never mutate them.
"""
import json
import os
import threading

_FILES = {}  # path -> (stamp, value)
_REFRESHING = set()
_LOCK = threading.Lock()


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def _read(path):
    stamp = _stamp(path)  # taken first, so a write during the parse is seen as a change
    with open(path) as fh:
        value = json.load(fh)
    _FILES[path] = (stamp, value)
    return value


def _refresh(path):
    try:
        _read(path)
    except (OSError, ValueError):
        pass  # keep serving the last good copy; a half-written file is retried next call
    finally:
        with _LOCK:
            _REFRESHING.discard(path)


def latest(path, default):
    path = str(path)
    entry = _FILES.get(path)
    if entry is None:
        try:
            return _read(path)
        except (OSError, ValueError):
            return default
    if entry[0] != _stamp(path):
        with _LOCK:
            start = path not in _REFRESHING
            _REFRESHING.add(path)
        if start:
            threading.Thread(target=_refresh, args=(path,), daemon=True).start()
    return entry[1]
