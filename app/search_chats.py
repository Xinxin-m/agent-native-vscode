#!/usr/bin/env python3
"""Search the cleaned chat history (Claude Code + Codex) from the command line.

This is the first place to look for "what did we decide about X", "have I done X
before", "where did we put X". It searches the cleaned, deduplicated chats in
data/chats/ plus their generated titles/descriptions/keywords in data/enrich.json.

  python3 search_chats.py hyperliquid funding        # AND of all terms
  python3 search_chats.py vercel --any               # OR
  python3 search_chats.py slurm -n 30                # more results
  python3 search_chats.py oak --source codex         # one agent only
  python3 search_chats.py oak --since 2026-08-01
  python3 search_chats.py --full claude-79361545-e154-4921-90cd-ce73e589ab7e

Exit status is 1 when nothing matched, so callers can branch on it.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CHATS = os.path.join(ROOT, "data", "chats")
ENRICH = os.path.join(ROOT, "data", "enrich.json")

W_TITLE, W_KEYWORD, W_DESC, W_BODY_CAP = 10, 6, 4, 8


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def when(chat):
    """ts_first/ts_last may be epoch seconds, epoch ms, or an ISO string."""
    raw = chat.get("ts_last") or chat.get("ts_first")
    if raw in (None, ""):
        return None
    if isinstance(raw, str) and not raw.replace(".", "").isdigit():
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val > 1e11:  # milliseconds
        val /= 1000.0
    try:
        return dt.datetime.fromtimestamp(val)
    except (OverflowError, OSError, ValueError):
        return None


def prose_hits(text, pattern, width=110):
    """Matches that sit in readable prose, not inside a base64 blob or a URL.

    Transcripts carry pasted images and data URIs; a term found inside one is
    noise, so a match only counts when its neighbourhood looks like writing.
    Returns (count, snippet_of_first).
    """
    count, best = 0, ""
    for hit in pattern.finditer(text):
        start = max(0, hit.start() - width // 3)
        end = min(len(text), hit.start() + width)
        window = text[start:end]
        if window.count(" ") / max(len(window), 1) < 0.08:
            continue
        count += 1
        if not best:
            out = re.sub(r"\s+", " ", window).strip()
            best = ("..." if start else "") + out + ("..." if end < len(text) else "")
    return count, best


def main():
    ap = argparse.ArgumentParser(description="Search cleaned agent chat history.")
    ap.add_argument("terms", nargs="*", help="search terms")
    ap.add_argument("-n", "--limit", type=int, default=15)
    ap.add_argument("--any", action="store_true", help="match ANY term instead of all")
    ap.add_argument("--source", choices=["claude", "codex"], help="restrict to one agent")
    ap.add_argument("--since", help="only chats last active on/after YYYY-MM-DD")
    ap.add_argument("--full", metavar="KEY", help="print one whole chat instead of searching")
    args = ap.parse_args()

    if args.full:
        chat = load_json(os.path.join(CHATS, args.full + ".json"), None)
        if chat is None:
            sys.exit("no such chat key: " + args.full)
        print("# %s   (%s, cwd=%s)\n" % (args.full, chat.get("source"), chat.get("cwd")))
        for turn in chat.get("turns", []):
            print("## %s\n%s\n" % (turn.get("role", "?").upper(), turn.get("text", "")))
        return

    if not args.terms:
        sys.exit("give at least one search term (or --full KEY)")

    since = None
    if args.since:
        try:
            since = dt.datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            sys.exit("--since wants YYYY-MM-DD")

    pats = [re.compile(re.escape(t), re.I) for t in args.terms]
    enrich = load_json(ENRICH, {})
    results = []

    for name in os.listdir(CHATS):
        if not name.endswith(".json"):
            continue
        key = name[:-5]
        if args.source and not key.startswith(args.source):
            continue
        chat = load_json(os.path.join(CHATS, name), None)
        if not chat:
            continue
        stamp = when(chat)
        if since and (stamp is None or stamp < since):
            continue

        meta = enrich.get(key, {})
        title = meta.get("title") or chat.get("title_seed") or "(untitled)"
        desc = meta.get("description", "")
        keywords = " ".join(meta.get("keywords", []))
        body = "\n".join(t.get("text", "") for t in chat.get("turns", []))

        score, matched, best = 0, 0, ""
        for pat in pats:
            hit = 0
            if pat.search(title):
                hit += W_TITLE
            if pat.search(keywords):
                hit += W_KEYWORD
            if pat.search(desc):
                hit += W_DESC
            found, text_hit = prose_hits(body, pat)
            if found:
                hit += min(found, W_BODY_CAP)
                if not best:
                    best = text_hit
            if hit:
                matched += 1
                score += hit
        if not matched or (not args.any and matched < len(pats)):
            continue
        results.append((score, stamp, key, title, chat.get("cwd", ""), best or desc))

    if not results:
        print("no match in cleaned chats for: " + " ".join(args.terms))
        sys.exit(1)

    results.sort(key=lambda r: (r[0], r[1] or dt.datetime.min), reverse=True)
    print("%d matching chats (showing %d)\n" % (len(results), min(args.limit, len(results))))
    for score, stamp, key, title, cwd, text in results[: args.limit]:
        date = stamp.strftime("%Y-%m-%d") if stamp else "----------"
        print("%s  [%3d]  %s" % (date, score, title))
        print("        %s" % key)
        if cwd:
            print("        cwd: %s" % cwd)
        if text:
            print("        %s" % text)
        print()
    print("read one in full:  python3 %s --full <key>" % os.path.basename(__file__))


if __name__ == "__main__":
    main()
