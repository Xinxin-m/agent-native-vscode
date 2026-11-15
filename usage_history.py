#!/usr/bin/env python3
"""
usage_history.py - GET /api/usage_history: token and turn aggregates over the
last N days, computed from data/index.json (usage, usage_days, models, node,
source) and data/projects.json assignments. Deterministic, no model calls.

Days and hours are local values recorded per model call by indexer.py. Exact
buckets retain model, account, host and root/child identity. Legacy aggregates
retain their totals with unknown model/hour rather than estimated allocation.
Quota snapshots are deliberately not part of these token measurements.
"""
import datetime
import json
import os
import threading
import time
from automation import automation_kind

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE_S = 60
TOKEN_FIELDS = ("in", "out", "cache_read", "cache_create", "turns")

_cache = {}  # days -> (computed_at, payload)
_lock = threading.Lock()


def _jload(name, default):
    try:
        with open(os.path.join(DATA, name + ".json")) as fh:
            return json.load(fh)
    except Exception:
        return default


def _local_hour(ts):
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone().hour
    except ValueError:
        return None


def _bump(table, key, add, chat_key, extra=None):
    row = table.get(key)
    if row is None:
        row = table[key] = {**{f: 0 for f in TOKEN_FIELDS}, "chats": set()}
        if extra:
            row.update(extra)
    for f in TOKEN_FIELDS:
        row[f] += add.get(f, 0)
    row["chats"].add(chat_key)


def _rows(table, label):
    out = []
    for key, row in table.items():
        r = {label: key}
        r.update({k: (len(v) if k == "chats" else v) for k, v in row.items()})
        out.append(r)
    out.sort(key=lambda r: (-r["turns"], -r["out"], str(r[label])))
    return out


def compute(days, include_ops=False):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)
    lo, hi = start.isoformat(), today.isoformat()

    index = _jload("index", {})
    if not include_ops:
        # Automated `claude -p` runs (brief generators, digests, labelling)
        # are flagged is_ops by enrich.py; they are not the user's own chats
        # and would swamp every chart (5,188 of 5,805 chats on 2026-10-21).
        enrich = _jload("enrich", {})
        def is_ops(key, meta):
            root = (meta.get('root_key') or key) if meta.get('is_child') else key
            return bool((enrich.get(root) or {}).get('is_ops') or automation_kind(index.get(root) or {}))
        index = {k: m for k, m in index.items() if not is_ops(k, m)}
    projects = _jload("projects", {"projects": [], "assignments": {}})
    names = {p.get("id"): p.get("name") or p.get("id")
             for p in projects.get("projects", [])}
    assign = projects.get("assignments", {})

    totals = {f: 0 for f in TOKEN_FIELDS}
    chat_keys = set()
    by_model, by_node, by_project, by_tool, by_account, by_role = {}, {}, {}, {}, {}, {}
    by_day, bucket_totals = {}, {}
    hours = [0] * 24
    unknown_hour_turns = 0

    for key, meta in index.items():
        # Old cached index records cannot establish model-level allocations.
        # Their measured daily totals remain useful and explicitly unknown.
        records = meta.get("usage_buckets")
        if not records:
            records = [dict(day=day, model="unknown", hour=None, **u)
                       for day, u in (meta.get("usage_days") or {}).items()]
        tool = meta.get("source") or "other"
        root = meta.get("root_key") or key
        pid = assign.get(root) if meta.get("is_child") else assign.get(key)
        for record in records:
            day = record.get("day") or "unknown"
            if not (lo <= day <= hi):
                continue
            add = {f: int(record.get(f, 0) or 0) for f in TOKEN_FIELDS}
            if not any(add.values()):
                continue
            chat_keys.add(key)
            model = record.get("model") or "unknown"
            # A current Library binding does not establish historical account
            # ownership. Only a token bucket's native/run provenance does.
            account = record.get("account_id") or "unknown"
            host = record.get("host_id") or meta.get("usage_host") or meta.get("node") or "unknown"
            role = "child" if meta.get("is_child") else "root"
            hour = record.get("hour")
            dims = (day, hour, model, account, host, root, key, role, tool)
            bucket = bucket_totals.setdefault(dims, {f: 0 for f in TOKEN_FIELDS})
            for f in TOKEN_FIELDS:
                totals[f] += add[f]
                bucket[f] += add[f]
            trow = by_day.setdefault(day, {}).setdefault(tool, {f: 0 for f in TOKEN_FIELDS})
            for f in TOKEN_FIELDS:
                trow[f] += add[f]
            _bump(by_tool, tool, add, key)
            _bump(by_node, host, add, key)
            _bump(by_project, names.get(pid, pid) if pid else "Unassigned", add, key)
            _bump(by_model, model, add, key)
            _bump(by_account, account, add, key)
            _bump(by_role, role, add, key)
            if isinstance(hour, int) and 0 <= hour < 24:
                hours[hour] += add["turns"]
            else:
                unknown_hour_turns += add["turns"]

    day_rows = []
    d = start
    while d <= today:
        ds = d.isoformat()
        row = {"day": ds}
        for tool in sorted(set(by_tool) | {"claude", "codex"}):
            row[tool] = by_day.get(ds, {}).get(tool, {"out": 0, "in": 0, "turns": 0})
        day_rows.append(row)
        d += datetime.timedelta(days=1)

    totals["chats"] = len(chat_keys)
    return {
        "days": days, "from": lo, "to": hi,
        "totals": totals,
        "by_model": _rows(by_model, "model"),
        "by_account": _rows(by_account, "account"),
        "by_role": _rows(by_role, "role"),
        "buckets": [dict(zip(("day", "hour", "model", "account_id", "host_id", "root_key", "chat_key", "role", "source"), dims), **u)
                    for dims, u in bucket_totals.items()],
        "unknown_hour_turns": unknown_hour_turns,
        "measurement": "transcript_tokens",
        "by_day": day_rows,
        "by_node": _rows(by_node, "node"),
        "by_project": _rows(by_project, "project"),
        "by_tool": _rows(by_tool, "tool"),
        "by_hour": [{"hour": h, "turns": hours[h]} for h in range(24)],
    }


def usage_history(req, query, body):
    try:
        days = int((query or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))
    include_ops = str((query or {}).get("include_ops") or "") in ("1", "true", "yes")
    ck = (days, include_ops)
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    payload = compute(days, include_ops)
    payload["include_ops"] = include_ops
    with _lock:
        _cache[ck] = (now, payload)
    return payload


ROUTES = {("GET", "/api/usage_history"): usage_history}
BACKGROUND = []
