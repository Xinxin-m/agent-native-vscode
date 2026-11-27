"""Read-only live overlay for local transcripts, independent of paid indexing hooks."""
import glob
import json
import os
import threading
import time

import indexer


class LiveLibrary:
    def __init__(self, interval=2):
        self.interval = interval
        self.lock = threading.RLock()
        self.checked = float('-inf')
        self.records = {}
        self.signatures = {}
        self.revision = 0
        self.live_keys = set()
        self.index_stat = None
        self.codex_ids = {}
        self.index_light = {}

    def refresh(self, saved, config, live_keys=()):
        """Re-scan transcripts. saved only needs src_sig, files_all, source_locations and
        the execution fields per chat."""
        self.live_keys = set(live_keys)
        with self.lock:
            now = time.monotonic()
            if now - self.checked >= self.interval:
                self._refresh(saved, {**indexer.DEFAULT_CONFIG, **config})
                self.checked = now

    def snapshot(self, saved, config, live_keys=()):
        """live_keys: chats with a running process; they are indexed once they have a user turn,
        even before the first reply."""
        self.refresh(saved, config, live_keys)
        return self.merge(saved)

    def merge(self, saved):
        with self.lock:
            result = dict(saved)
            for key, (chat, signature) in self.records.items():
                previous = saved.get(key, {})
                saved_signature = previous.get('src_sig')
                if saved_signature and saved_signature != signature:
                    paths = set(previous.get('files_all', ())) | set(chat['files_all'])
                    if saved_signature == indexer.src_sig(paths):
                        continue  # The archive already published the newer rollout while we waited for its lock.
                meta = indexer.meta_of(chat, signature)
                indexer.preserve_execution_metadata(meta, previous)
                result[key] = meta
            indexer.resolve_lineage(result)
            return result

    def _codex_id(self, path):
        # A rollout file's session id is fixed by its first line; read it once per path.
        if path not in self.codex_ids:
            self.codex_ids[path] = indexer.codex_session_id(path)
        return self.codex_ids[path]

    def _refresh(self, saved, cfg):
        units = {}
        names = {}
        for node, croot, xroot, xindex in indexer.source_roots(cfg):
            if node != 'mac':
                continue  # Remote files are refreshed by the existing mirror service.
            if xindex:
                try:
                    with open(xindex) as handle:
                        for line in handle:
                            try:
                                value = json.loads(line)
                                if value.get('id') and value.get('thread_name'):
                                    names[value['id']] = value['thread_name']
                            except ValueError:
                                pass
                except OSError:
                    pass
            paths = glob.glob(os.path.join(croot, '*', '*.jsonl')) if croot else []
            if croot:
                paths += glob.glob(os.path.join(croot, '*', '*', 'subagents', '*.jsonl'))
            for path in paths:
                sid = os.path.basename(path)[:-6]
                if os.path.basename(os.path.dirname(path)) == 'subagents':
                    sid = os.path.basename(os.path.dirname(os.path.dirname(path))) + '-agent-' + sid.removeprefix('agent-')
                units.setdefault(('claude-' + sid, 'claude'), set()).add(path)
            for path in glob.glob(os.path.join(xroot, '**', '*.jsonl'), recursive=True) if xroot else []:
                units.setdefault(('codex-' + self._codex_id(path), 'codex'), set()).add(path)
        for (key, source), found in units.items():
            previous = saved.get(key, {})
            # Include prior copies so resumes merge and cross-host provenance survives.
            paths = sorted(found | {p for p in previous.get('files_all', []) if os.path.isfile(p)})
            signature = indexer.src_sig(paths)
            new_live = key in self.live_keys and key not in saved and key not in self.records
            if self.signatures.get(key) == signature and not new_live:
                continue
            if previous.get('src_sig') == signature:
                self.signatures[key] = signature
                continue
            by_node = {'mac': sorted(found)}
            for location in previous.get('source_locations', []):
                host, path = location.get('host_id'), location.get('path')
                if host and host != 'mac' and path in paths:
                    by_node.setdefault(host, []).append(path)
            chat = indexer.parse_unit(key, source, by_node, names)
            if not chat or not chat['n_user']:
                self.signatures[key] = signature
                continue
            # Empty newly-created sessions are visible while a first reply is pending.
            if not chat['n_answer'] and chat.get('status') != 'working' and key not in self.live_keys:
                self.signatures[key] = signature
                continue
            indexer.preserve_execution_metadata(chat, previous)
            chat['source_locations'] = indexer.source_locations(by_node, cfg)
            chat['account_attribution'] = 'verified' if chat.get('account_id') else 'unknown'
            # Keep the signature from before the read, even if the file grew while parsing.
            self.records[key] = (chat, signature)
            self.signatures[key] = signature
            self.revision += 1

    def chat(self, key):
        with self.lock:
            record = self.records.get(key)
            return record[0] if record else None
