"""Recurring, local taxonomy review. Reports evidence; never pays or moves chats implicitly."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from automation import automation_kind

ROOT = Path(__file__).resolve().parent


def descendants(projects, project_id):
    selected = {project_id}
    while True:
        added = {p['id'] for p in projects if p.get('parent_id') in selected}
        if added <= selected:
            return selected
        selected.update(added)


def matching_rules(meta, label, rules):
    text = ' '.join(str(label.get(k) or '') for k in ('title', 'description', 'topic_hint', 'keywords'))
    text += ' ' + str(meta.get('title_seed') or '')
    matches = []
    for rule in rules:
        any_terms, all_terms = rule.get('any', []), rule.get('all', [])
        if not any_terms and not all_terms:
            continue
        if any_terms and not any(re.search(term, text, re.I) for term in any_terms):
            continue
        if not all(re.search(term, text, re.I) for term in all_terms):
            continue
        if any(re.search(term, text, re.I) for term in rule.get('exclude', [])):
            continue
        matches.append(rule)
    return matches


def audit(index, enrich, projects, config, now=None, previous=None, flags=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    assignments = projects.get('assignments', {})
    names = {p['id']: p['name'] for p in projects.get('projects', [])}
    rules = config.get('classification_rules', [])
    manual = set(projects.get('manual_assignments', []))
    titles = (flags or {}).get('titles') or {}  # a manual rename wins
    items = []
    recent = 0
    fingerprints = {}
    old_fingerprints = (previous or {}).get('fingerprints', {})
    pending = {row['key']: row for row in (previous or {}).get('items', [])}
    for key, meta in index.items():
        if meta.get('is_child') or automation_kind(meta):
            continue
        stamp = str(meta.get('ts_last') or '')
        if stamp >= (now-dt.timedelta(days=14)).isoformat()[:10]:
            recent += 1
        label = enrich.get(key) or {}
        fingerprint = hashlib.sha256(json.dumps([meta.get('ts_last'),label],sort_keys=True).encode()).hexdigest()
        fingerprints[key] = fingerprint
        matches = matching_rules(meta, label, rules)
        current = assignments.get(key)
        destinations = {r.get('project_id') for r in matches}
        if destinations:
            target = matches[0].get('project_id')
            if target != current:
                items.append({'key': key, 'title': titles.get(key) or label.get('title') or meta.get('title_seed') or key,
                              'current': current, 'suggested': target, 'manual': key in manual,
                              'kind': 'rule_conflict' if len(destinations)>1 else 'placement',
                              'reason': matches[0].get('reason') or 'Matches configured purpose rule',
                              'ts_last': stamp})
        elif not current or current.startswith('workspace-'):
            items.append({'key':key,'title':titles.get(key) or label.get('title') or meta.get('title_seed') or key,
                          'current':current,'suggested':None,'manual':key in manual,
                          'kind':'unclassified' if not current else 'workspace_seed',
                          'reason':'Review purpose and whether a dedicated project is needed', 'ts_last':stamp})
        elif (stamp >= (now-dt.timedelta(days=14)).isoformat()[:10] and old_fingerprints.get(key) != fingerprint) or (pending.get(key,{}).get('kind') == 'purpose_review' and pending[key].get('current') == current):
            items.append({'key':key,'title':titles.get(key) or label.get('title') or meta.get('title_seed') or key,
                          'current':current,'suggested':None,'manual':key in manual,'kind':'purpose_review',
                          'reason':'Recent or changed conversation: inspect whether purpose still fits, or a new project/parent/merge is needed','ts_last':stamp})
    items.sort(key=lambda row: row.get('ts_last',''), reverse=True)
    by_name = {}
    for pid,name in names.items():
        by_name.setdefault(re.sub(r'[^a-z0-9]','',name.lower()),[]).append(pid)
    duplicates = [ids for ids in by_name.values() if len(ids)>1]
    structure = []
    archived = set((flags or {}).get('archived', []))
    for project in projects.get('projects', []):
        pid = project['id']
        children = [p['id'] for p in projects.get('projects', []) if p.get('parent_id') == pid]
        direct = [k for k, value in assignments.items() if value == pid and k in index and not index[k].get('is_child')]
        if not direct and not children:
            structure.append({'project_id':pid, 'kind':'empty_project', 'reason':'Review whether this empty project should be retained or removed'})
        elif not direct and len(children) == 1:
            structure.append({'project_id':pid, 'kind':'single_child_parent', 'reason':'Consider a flat project to remove an unnecessary navigation level'})
        if direct and all(k in archived for k in direct) and not project.get('archived'):
            structure.append({'project_id':pid, 'kind':'completed_project', 'reason':'All direct conversations are archived; review whether the project is complete'})
    return {'generated_at':now.isoformat(),'rules_signature':hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest(),
            'fingerprints':fingerprints,'recent_roots_14d':recent,'review_count':len(items),'items':items,
            'possible_duplicate_projects':duplicates,'structure_suggestions':structure,
            'policy':'Review assigned and unassigned chats after every refresh. Manual decisions are never changed by this report. New projects, merges, parent groups and archival changes require an explicit review.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    args=parser.parse_args()
    def load(name,default):
        path=args.data/(name+'.json')
        return json.loads(path.read_text()) if path.exists() else default
    result=audit(load('index',{}),load('enrich',{}),load('projects',{'projects':[],'assignments':{}}),load('config',{}),previous=load('classification-review',{}),flags=load('chat_flags',{}))
    path=args.data/'classification-review.json'
    from classify import atomic_json_dump
    atomic_json_dump(result,str(path),indent=1)
    print(f"Taxonomy review: {result['review_count']} chats; {result['recent_roots_14d']} recently active roots; no assignments changed")


if __name__=='__main__':
    main()
