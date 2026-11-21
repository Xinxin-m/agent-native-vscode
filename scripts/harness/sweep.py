#!/usr/bin/env python3
"""List hygiene findings in a workspace directory. Reports only, unless --fix.

Categories: stray_root, version_copies, empty_dirs, stale_scratch, large_files,
duplicates, broken_links, scattered_kinds, os_junk. Markdown goes to stdout or --md,
JSON to --json. --fix removes only empty directories and OS junk files, and never
anything that matches the protect list. Exit code 1 when a --max threshold is exceeded.
"""
import argparse
import collections
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

DEFAULT_ALLOW = ["README*", "LICENSE*", "CHANGELOG*", "CONTRIBUTING*", "CLAUDE.md", "AGENTS.md", ".gitignore",
                 ".gitattributes", ".editorconfig", "Makefile", "pyproject.toml", "package.json", "package-lock.json",
                 "*.code-workspace", ".sweep-allow"]
DEFAULT_PROTECT = ["data/", "datasets/", "raw/", "results/", "output/", "outputs/", "figures/", "backups/", "logs/",
                   "notebooks/", "*.csv", "*.parquet", "*.ipynb"]
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "site-packages", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".tox", ".cache", ".next", "dist", "build"}
JUNK = [".DS_Store", "Thumbs.db", "desktop.ini", "._*"]
VERSION = re.compile(r"([-_ ]v\d+| copy( \d+)?|[-_ ]final([-_ ][\w-]*)?|[-_ ]old| \(\d+\))(\.[^.]+)?$", re.I)
CONVENTIONAL = {"src", "docs", "doc", "tests", "test", "scripts", "bin", "lib", "assets", "static", "build", "dist",
                "config", "tmp", "temp", "scratch", "web", "app", "media", "images", "img", "examples", "vendor"}
LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def protected(rel, patterns):
    parts = Path(rel).parts
    for pattern in patterns:
        if pattern.endswith("/"):
            if pattern[:-1] in parts:
                return True
        elif fnmatch.fnmatch(parts[-1] if parts else "", pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return False


def is_junk(name):
    return any(fnmatch.fnmatch(name, j) for j in JUNK)


def walk(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        yield Path(folder), dirs, sorted(files)


def kind_key(name):
    base = re.sub(r"\d+", "#", name.lower())
    token = re.split(r"[-_ .]", base)[0]
    return token + "-*" if token != base else base


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def broken_links(path, root):
    found, fenced = [], False
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return found
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced:
            continue
        for target in LINK.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/", "~")):
                continue
            clean = unquote(target.split("#")[0].split("?")[0])
            if clean and not (path.parent / clean).exists():
                found.append({"path": str(path.relative_to(root)), "line": number, "target": target})
    return found


def scan(root, args):
    root = root.resolve()
    allow = list(DEFAULT_ALLOW)
    allow_file = args.allowlist or root / ".sweep-allow"
    if Path(allow_file).is_file():
        allow += [l.strip() for l in Path(allow_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    protect = DEFAULT_PROTECT + args.protect
    now, findings = time.time(), collections.defaultdict(list)
    by_size, parents = collections.defaultdict(list), collections.defaultdict(set)
    for folder, dirs, files in walk(root):
        rel_folder = folder.relative_to(root)
        depth = len(rel_folder.parts)
        for d in dirs:
            if depth < args.kind_depth and d.lower() not in CONVENTIONAL and not d.startswith("."):
                parents[kind_key(d)].add((str(rel_folder), d))
        for name in files:
            path = folder / name
            rel = str(path.relative_to(root))
            try:
                info = path.lstat()
            except OSError:
                continue
            item = {"path": rel, "protected": protected(rel, protect)}
            if is_junk(name):
                findings["os_junk"].append(item)
                continue
            if depth == 0 and not any(fnmatch.fnmatch(name, a) for a in allow):
                findings["stray_root"].append(item)
            if VERSION.search(name):
                findings["version_copies"].append(item)
            if set(rel_folder.parts) & set(args.scratch) and now - info.st_mtime > args.stale_days * 86400:
                findings["stale_scratch"].append(dict(item, days=int((now - info.st_mtime) // 86400)))
            if info.st_size >= args.large_mb * 1048576:
                findings["large_files"].append(dict(item, mb=round(info.st_size / (1 << 20), 1)))
            if info.st_size >= args.dup_min_kb * 1024 and not path.is_symlink():
                by_size[info.st_size].append(path)
            if name.lower().endswith(".md"):
                findings["broken_links"] += broken_links(path, root)
    empty = set()
    for folder, _, _ in sorted(walk(root), key=lambda t: -len(t[0].parts)):
        try:
            entries = [folder / e for e in os.listdir(folder)]
        except OSError:
            continue
        if folder != root and all(e in empty or (e.is_file() and is_junk(e.name)) for e in entries):
            empty.add(folder)
            rel = str(folder.relative_to(root))
            findings["empty_dirs"].append({"path": rel, "protected": protected(rel, protect)})
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        groups = collections.defaultdict(list)
        for path in paths:
            try:
                groups[sha256(path)].append(str(path.relative_to(root)))
            except OSError:
                continue
        findings["duplicates"] += [{"paths": sorted(g), "bytes": size} for g in groups.values() if len(g) > 1]
    for key, places in sorted(parents.items()):
        if len({p for p, _ in places}) > 1:
            findings["scattered_kinds"].append({"kind": key, "folders": sorted(str(Path(p) / d) for p, d in places)})
    return root, protect, dict(findings)


def fix(root, findings):
    removed = []
    for item in findings.get("os_junk", []):
        if not item["protected"]:
            (root / item["path"]).unlink(missing_ok=True)
            removed.append(item["path"])
    for item in findings.get("empty_dirs", []):  # deepest first, as listed
        folder = root / item["path"]
        if item["protected"]:
            continue
        for child in folder.iterdir():
            if child.is_file() and is_junk(child.name):
                child.unlink()
        try:
            folder.rmdir()
            removed.append(item["path"] + "/")
        except OSError:
            pass
    return removed


def markdown(root, findings, removed):
    lines = [f"# Workspace sweep: {root.name}", "", "| Category | Findings |", "| --- | --- |"]
    lines += [f"| {k} | {len(v)} |" for k, v in sorted(findings.items())]
    for key, items in sorted(findings.items()):
        lines += ["", f"## {key}", ""]
        for item in items[:200]:
            if "paths" in item:
                lines.append(f"- {item['bytes']} bytes: " + ", ".join(f"`{p}`" for p in item["paths"]))
            elif "folders" in item:
                lines.append(f"- `{item['kind']}`: " + ", ".join(f"`{p}`" for p in item["folders"]))
            elif "target" in item:
                lines.append(f"- `{item['path']}` line {item['line']}: `{item['target']}`")
            else:
                extra = "".join(f", {k} {item[k]}" for k in ("days", "mb") if k in item)
                lines.append(f"- `{item['path']}`{extra}{' (protected)' if item['protected'] else ''}")
        if len(items) > 200:
            lines.append(f"- ... {len(items) - 200} more in the JSON report")
    if removed:
        lines += ["", "## Removed by --fix", ""] + [f"- `{p}`" for p in removed]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--allowlist", type=Path, help="root allowlist, one glob per line (default <root>/.sweep-allow)")
    parser.add_argument("--protect", action="append", default=[], help="extra protect pattern; 'name/' is a folder")
    parser.add_argument("--scratch", nargs="+", default=["tmp", "temp", "scratch"])
    parser.add_argument("--stale-days", type=int, default=7)
    parser.add_argument("--large-mb", type=float, default=50)
    parser.add_argument("--dup-min-kb", type=float, default=1)
    parser.add_argument("--kind-depth", type=int, default=3, help="how deep to look for scattered folder kinds")
    parser.add_argument("--md", type=Path, help="write Markdown here instead of stdout")
    parser.add_argument("--json", type=Path, help="write JSON findings here")
    parser.add_argument("--max", action="append", default=[], metavar="CATEGORY=N", help="fail when a category exceeds N")
    parser.add_argument("--fix", action="store_true", help="remove empty folders and OS junk outside the protect list")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    root, protect, findings = scan(args.root, args)
    removed = fix(root, findings) if args.fix else []
    text = markdown(root, findings, removed)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(text)
    else:
        sys.stdout.write(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        counts = {k: len(v) for k, v in findings.items()}
        args.json.write_text(json.dumps({"root": str(root), "protect": protect, "counts": counts,
                                         "findings": findings, "removed": removed}, indent=2) + "\n")
    over = []
    for spec in args.max:
        key, _, limit = spec.partition("=")
        if len(findings.get(key, [])) > int(limit or 0):
            over.append(f"{key}={len(findings[key])}>{limit}")
    if over:
        print("threshold exceeded: " + ", ".join(over), file=sys.stderr)
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
