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


def publish(library, live_keys=None):
    # Serialize archive writes without waiting for the pipeline's paid labeling stages.
    with open(os.path.join(indexer.DATA, 'archive-write.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        saved = load(indexer.INDEX, None)
        if saved is None:
            raise RuntimeError('Archive index unreadable; preserving existing data')
        cfg = load(indexer.CONFIG, {})
        merged = library.snapshot(saved, cfg, running_keys() if live_keys is None else live_keys)
        changed = {key:chat for key,chat in library.records.items()
                   if saved.get(key, {}).get('src_sig') != merged[key].get('src_sig')}
        if not changed:
            return 0
        for key, chat in changed.items():
            previous = load(os.path.join(indexer.CHATS, key + '.json'), {})
            indexer.preserve_execution_metadata(chat, previous)
            indexer.preserve_execution_metadata(merged[key], chat)
            indexer.atomic_json_dump(chat, os.path.join(indexer.CHATS, key + '.json'))
        indexer.atomic_json_dump(merged, indexer.INDEX, indent=1)
        # The archive now owns these snapshots. Retain signatures, not turn bodies.
        library.records.clear()
        return len(changed)


def main():
    # Only one live worker; it may safely overlap the server and ordinary hooks.
    with open(os.path.join(indexer.DATA, 'live-refresh.lock'), 'a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        library = LiveLibrary(interval=0)
        while True:
            started = time.monotonic()
            try:
                count = publish(library)
                if count:
                    print(f'live refreshed {count} chats in {time.monotonic()-started:.2f}s', flush=True)
            except Exception as error:
                print(f'live refresh: {type(error).__name__}: {error}', flush=True)
            time.sleep(max(.1, 2-(time.monotonic()-started)))


if __name__ == '__main__':
    main()
