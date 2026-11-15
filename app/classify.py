#!/usr/bin/env python3
"""
classify.py — Cluster enriched chats into ChatGPT-style projects (topics) with
sonnet, then write data/projects.json.

Safe to re-run: chats the user already placed (any key present in
projects.json assignments) are never reassigned; existing projects are reused
by name. Only unassigned chats are classified, in batches of BATCH so a large
backlog cannot overflow one response — each batch is merged and written before
the next runs, and later batches see the projects the earlier ones created.

Run:  python3 classify.py [--refine-workspaces] [--all]   (--all reclassifies everything from scratch)
"""
import json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clis  # noqa: E402  (resolve the CLI even under a service's minimal PATH)
from automation import automation_kind, effective_projects, human_project_name

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
INDEX = os.path.join(DATA, "index.json")
ENRICH = os.path.join(DATA, "enrich.json")
PROJECTS = os.path.join(DATA, "projects.json")


def _config() -> dict:
    try:
        with open(os.path.join(DATA, "config.json")) as fh:
            return json.load(fh)
    except Exception:
        return {}


_CFG = _config()

# Clustering the whole library in one call: one shot, and a bad partition is
# visible on every page — the reasoning tier. See ../MODELS.md.
MODEL = (_CFG.get("models") or {}).get("reasoning") or "claude-sonnet-5"
MISC_NAME = _CFG.get("misc_project") or "Miscellaneous & Ops"
BATCH = int(_CFG.get("classify_batch") or 150)

# Who the chats belong to and how their work divides up. These live in
# data/config.json (gitignored) because good project names need to know the
# person's actual workstreams — and because that is exactly what must not ship
# in source. enrich.py reads the same two keys.
DEFAULT_USER_CONTEXT = (
    "The user is a technical professional who uses AI assistants for work "
    "projects, research, and maintaining their own tools and infrastructure.")
DEFAULT_PROJECT_EXAMPLES = (
    '"Main Project", "Reading & Research", "Infra & Setup", "Client Work"')
USER_CONTEXT = _CFG.get("user_context") or DEFAULT_USER_CONTEXT
PROJECT_EXAMPLES = _CFG.get("project_examples") or DEFAULT_PROJECT_EXAMPLES
# Optional hand-written rules, e.g. "'Acme' is one project covering every task
# done for that client; never split it per task." One string per rule.
PROJECT_RULES = [str(r) for r in (_CFG.get("project_rules") or []) if str(r).strip()]


def atomic_json_dump(obj, path, indent=None):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)

PROMPT = """You are organizing a person's AI-chat history into PROJECTS, the way ChatGPT/Claude group chats into project folders. """ + USER_CONTEXT + """

Below is one chat per line: KEY | SOURCE | DIR | TITLE | TOPIC_HINT | KEYWORDS | OPS?

Create 6-14 projects named by goal or workstream (short, specific, Title Case, e.g. """ + PROJECT_EXAMPLES + """). Rules:
- Group by GOAL, not by tool. Chats advancing the same thread belong together even if run in different folders.
- One job, client, or side project is ONE project covering everything done for it. Do not split it into per-task projects.
- Purely operational/setup/one-off chats (OPS?=true and no clear goal) go to "{misc}".
- If existing projects are listed below, reuse their exact names where they fit; only add new ones when needed.
- EVERY key must appear in assignments exactly once.
{rules}

{existing}

Return STRICT JSON only (no code fences):
{{"projects": ["Name1", "Name2", ...], "assignments": {{"<key>": "<project name>", ...}}}}

CHATS
=====
{lines}"""


def extract_json(s):
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "p"


def main():
    reclassify_all = "--all" in sys.argv
    with open(INDEX) as fh:
        index = json.load(fh)
    enrich = {}
    if os.path.exists(ENRICH):
        with open(ENRICH) as fh:
            enrich = json.load(fh)
    proj = {"projects": [], "assignments": {}}
    if os.path.exists(PROJECTS) and not reclassify_all:
        try:
            with open(PROJECTS) as fh:
                proj = json.load(fh)
        except Exception:
            pass

    # Explicit paid refinement may replace only untouched workspace seeds.
    # A drag/drop placement to a different project always wins.
    if "--refine-workspaces" in sys.argv:
        from workspace_groups import refine_workspace_seeds
        refine_workspace_seeds(proj)

    # assignments for chats that left the index (deleted, or filtered out)
    for k in [k for k in proj["assignments"] if k not in index]:
        proj["assignments"].pop(k)

    proj = effective_projects(index, proj)
    atomic_json_dump(proj, PROJECTS, indent=1)
    todo = [k for k in index if not index[k].get('is_child') and not automation_kind(index[k]) and k not in proj.get('manual_assignments', []) and (reclassify_all or k not in proj["assignments"])]
    if not todo:
        print("nothing to classify")
        return

    n_set, failures = 0, 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        lines = []
        for k in batch:
            m = index[k]
            e = enrich.get(k) or {}
            d = os.path.basename(m.get("cwd") or "") or "?"
            lines.append(" | ".join([
                k, m["source"], d,
                (e.get("title") or m.get("title_seed") or m.get("first_prompt", ""))[:80],
                e.get("topic_hint", "")[:40],
                ",".join(e.get("keywords", [])[:4]),
                "true" if e.get("is_ops") else "false",
            ]))

        existing = ""
        if proj["projects"]:
            existing = "Existing projects (reuse these names where they fit):\n- " + \
                "\n- ".join(p["name"] for p in proj["projects"] if not p.get("automation"))

        rules = "".join("- " + r + "\n" for r in PROJECT_RULES)
        prompt = PROMPT.format(misc=MISC_NAME, existing=existing, rules=rules,
                               lines="\n".join(lines))
        print(f"classifying {len(batch)} chats ({start + len(batch)}/{len(todo)}) "
              f"with {MODEL} ({len(prompt)} chars)...", flush=True)
        r = subprocess.run(
            [clis.cli("claude", _CFG), "-p", "--model", MODEL,
             "--no-session-persistence", "--strict-mcp-config"],
            input=prompt, capture_output=True, text=True, timeout=900,
            cwd=tempfile.gettempdir(), env=clis.env())
        d = extract_json(r.stdout or "")
        if not d or "assignments" not in d:
            # one bad batch must not lose the batches already merged
            print("FAILED to parse model output for this batch:")
            print((r.stdout or r.stderr)[:1200])
            failures += 1
            continue

        by_name = {p["name"]: p for p in proj["projects"]}
        for name in d.get("projects", []):
            name = human_project_name(name, MISC_NAME)
            if name not in by_name:
                p = {"id": slug(name), "name": name}
                if name == MISC_NAME:
                    p["pinned_bottom"] = True
                # avoid id collisions with differently-named projects
                ids = {x["id"] for x in proj["projects"]}
                base, i = p["id"], 2
                while p["id"] in ids:
                    p["id"] = f"{base}-{i}"; i += 1
                proj["projects"].append(p)
                by_name[name] = p

        for k, name in d["assignments"].items():
            name = human_project_name(name, MISC_NAME)
            if k not in batch or index[k].get('is_child') or (k in proj["assignments"] and not reclassify_all):
                continue
            p = by_name.get(name)
            if not p:
                p = {"id": slug(name), "name": name}
                proj["projects"].append(p)
                by_name[name] = p
            proj["assignments"][k] = p["id"]
            n_set += 1

        # misc pinned last, everything else keeps model order
        proj["projects"].sort(key=lambda x: x.get("pinned_bottom", False))
        proj = effective_projects(index, proj)
        atomic_json_dump(proj, PROJECTS, indent=1)

    missing = [k for k in todo if k not in proj["assignments"]]
    print(f"assigned {n_set}; projects: {[p['name'] for p in proj['projects']]}")
    if missing:
        print(f"WARNING: {len(missing)} chats left unassigned (will show as Ungrouped)")
    if failures and not n_set:
        sys.exit(1)


if __name__ == "__main__":
    main()
