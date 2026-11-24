#!/usr/bin/env python3
"""Continuously publish changed local transcripts without paid labels or server restarts."""
import fcntl
import json
import os
import time

import indexer
from live_library import LiveLibrary


def load(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def running_keys(path=None):
    """Chat keys of running sessions in the last live inventory (data/live.json)."""
    rows = load(path or os.path.join(indexer.DATA, 'live.json'), {}).get('sessions') or []
    return {r['tool'] + '-' + r['session_id'] for r in rows
            if isinstance(r, dict) and r.get('alive') and r.get('tool') and r.get('session_id')}


LIGHT_FIELDS = ('src_sig', 'files_all', 'source_locations') + indexer.EXECUTION_FIELDS


def index_stat():
    try:
        st = os.stat(indexer.INDEX)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def light_view(index):
    """Only what the transcript scan reads from each saved chat; avoids holding the whole index."""
    return {key: {f: chat[f] for f in LIGHT_FIELDS if f in chat} for key, chat in index.items()}


def publish(library, live_keys=None):
    cfg = load(indexer.CONFIG, {})
    keys = running_keys() if live_keys is None else live_keys
    # Scan against a light copy of the index that is reloaded only when index.json changes.
    stat = index_stat()
    if stat is None or stat != library.index_stat:
        saved = load(indexer.INDEX, None)
        if saved is None:
            raise RuntimeError('Archive index unreadable; preserving existing data')
        library.index_light, library.index_stat = light_view(saved), stat
    library.refresh(library.index_light, cfg, keys)
    if not library.records:
        return 0
    # Serialize archive writes without waiting for the pipeline's paid labeling stages.
    with open(os.path.join(indexer.DATA, 'archive-write.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        saved = load(indexer.INDEX, None)
        if saved is None:
            raise RuntimeError('Archive index unreadable; preserving existing data')
        merged = library.merge(saved)
        changed = {key:chat for key,chat in library.records.items()
                   if saved.get(key, {}).get('src_sig') != merged[key].get('src_sig')}
        if changed:
            for key, chat in changed.items():
                previous = load(os.path.join(indexer.CHATS, key + '.json'), {})
                indexer.preserve_execution_metadata(chat, previous)
                indexer.preserve_execution_metadata(merged[key], chat)
                indexer.atomic_json_dump(chat, os.path.join(indexer.CHATS, key + '.json'))
            indexer.atomic_json_dump(merged, indexer.INDEX)
            library.index_light, library.index_stat = light_view(merged), index_stat()
        # The archive now owns these snapshots. Retain signatures, not turn bodies.
        library.records.clear()
        return len(changed)


SUMMARY_EVERY = 600


def main():
    # Only one live worker; it may safely overlap the server and ordinary hooks.
    with open(os.path.join(indexer.DATA, 'live-refresh.lock'), 'a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        library = LiveLibrary(interval=0)
        refreshed = cycles = 0
        last_summary = time.monotonic()
        last_error = None
        while True:
            started = time.monotonic()
            try:
                count = publish(library)
                refreshed += count
                cycles += bool(count)
                last_error = None
            except Exception as error:
                message = f'live refresh: {type(error).__name__}: {error}'
                if message != last_error:
                    print(message, flush=True)
                last_error = message
            if refreshed and started - last_summary >= SUMMARY_EVERY:
                print(f'live refreshed {refreshed} chats in {cycles} cycles since last summary', flush=True)
                refreshed = cycles = 0
                last_summary = started
            time.sleep(max(.1, 2-(time.monotonic()-started)))


if __name__ == '__main__':
    main()
