#!/usr/bin/env python3
"""Delete hashed build outputs that no entry file still reaches.

  python3 scripts/prune_bundles.py <dir> [<entry> ...] [--keep-days N] [--dry-run]

A file is hashed when its name ends in -XXXXXXXX.<ext> (the Vite/Rolldown pattern). Starting
from the entries (index.html, boot.js ...), every text file that is kept is searched for the
names of hashed files in <dir>; those are kept too. Unhashed files are never touched.
--keep-days N also keeps hashed files written in the last N days (and what they reach), so a
browser tab still running a recent build can load its lazy chunks.
"""
import re, sys, time
from pathlib import Path

HASHED = re.compile(r'^.+-[A-Za-z0-9_-]{8}\.[a-z0-9]+$')
TEXT = {'.js', '.mjs', '.css', '.html', '.json', '.cjs'}
TOKEN = re.compile(r'[\w.-]+-[A-Za-z0-9_-]{8}\.[a-z0-9]+')

def reachable(folder, entries, days=0):
    hashed = {p.name for p in folder.iterdir() if p.is_file() and HASHED.match(p.name)}
    keep = {n for n in hashed if days and time.time() - (folder / n).stat().st_mtime < days * 86400}
    queue = [Path(e) for e in entries if Path(e).is_file()] + [folder / n for n in keep]
    queue += [p for p in folder.iterdir() if p.is_file() and p.name not in hashed and p.suffix in TEXT]
    seen = set()
    while queue:
        path = queue.pop()
        if path in seen or path.suffix not in TEXT:
            continue
        seen.add(path)
        for name in set(TOKEN.findall(path.read_text(errors='ignore'))) & (hashed - keep):
            keep.add(name); queue.append(folder / name)
    return hashed, keep

def main(argv):
    dry = '--dry-run' in argv
    args = [a for a in argv if a != '--dry-run']
    days = 0
    if '--keep-days' in args:
        i = args.index('--keep-days'); days = float(args[i + 1]); del args[i:i + 2]
    folder, entries = Path(args[0]), args[1:]
    hashed, keep = reachable(folder, entries, days)
    stale = sorted(hashed - keep)
    freed = sum((folder / n).stat().st_size for n in stale)
    if not dry:
        for name in stale:
            (folder / name).unlink()
    print(f"{'would remove' if dry else 'removed'} {len(stale)} of {len(hashed)} hashed files, {freed/1e6:.1f} MB; kept {len(keep)}")

if __name__ == '__main__':
    main(sys.argv[1:])
